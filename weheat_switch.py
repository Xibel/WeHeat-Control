#!/usr/bin/env python3
"""
WeHeat-switch: switch a Weheat heat pump between "OpenTherm" (ControlMethod 4)
and "heating curve" (ControlMethod 1), from the command line or from Homey
through a small HTTP API. Experimental: it uses the API Weheat's own portal
uses (https://api.weheat.nl/api/v1), which isn't a documented public API.

Commands (see README.md):
  python weheat_switch.py login        log in and show who you are
  python weheat_switch.py find         look for your installation and control board
  python weheat_switch.py status       read the settings: control method and curve
  python weheat_switch.py save         remember the current heating curve
  python weheat_switch.py opentherm    remember the curve, then switch to OpenTherm
  python weheat_switch.py curve        switch back to the heating curve (the remembered points)
  python weheat_switch.py serve        run the HTTP API for Homey
  add --dry-run to opentherm / curve to only show what would be sent

Settings in config.json (copy config.example.json). Tokens and the remembered
curve go in state.json. Neither file is ever printed; keep both private.
"""
import argparse, base64, hashlib, json, os, re, secrets, sys, threading, time
from datetime import datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get("WEHEAT_SWITCH_CONFIG", os.path.join(HERE, "config.json"))

# Weheat's login (Keycloak). The portal's own client needs a secret, so we use
# the public client of Weheat's third-party API debugger page, with the same
# login flow a browser does (authorization code + PKCE).
AUTH = "https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect"
CLIENT_ID = "weheat-third-party-debugger"
REDIRECT_URI = "https://api.weheat.nl/third_party/api/debugger"
API = "https://api.weheat.nl/api/v1"
THIRD_PARTY = "https://api.weheat.nl/third_party/api/v1"

OPENTHERM, HEATING_CURVE = 4, 1
METHOD_NAME = {OPENTHERM: "OpenTherm", HEATING_CURVE: "heating curve"}
UA = "WeHeat-switch/0.1"
_lock = threading.Lock()


# ── settings and state ───────────────────────────────────────────────────────
def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        sys.exit(f"No {CONFIG_FILE}: copy config.example.json to config.json and fill it in.")
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("username", "password"):
        if not cfg.get(k):
            sys.exit(f"config.json: '{k}' is empty.")
    cfg.setdefault("state_file", "state.json")
    if not os.path.isabs(cfg["state_file"]):
        cfg["state_file"] = os.path.join(os.path.dirname(os.path.abspath(CONFIG_FILE)), cfg["state_file"])
    return cfg


def load_state(cfg) -> dict:
    try:
        with open(cfg["state_file"], encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(cfg, state: dict):
    tmp = cfg["state_file"] + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # tokens: owner only
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, cfg["state_file"])


def log(msg: str):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


# ── login ────────────────────────────────────────────────────────────────────
class _LoginForm(HTMLParser):
    """The Keycloak login page: the form's action and its hidden fields."""
    def __init__(self):
        super().__init__()
        self.action, self.fields, self.error, self._in_error = None, {}, "", False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and self.action is None and ("kc-form-login" == a.get("id") or "login-actions" in (a.get("action") or "")):
            self.action = a.get("action")
        elif tag == "input" and a.get("type") == "hidden" and a.get("name"):
            self.fields[a["name"]] = a.get("value") or ""
        elif tag == "span" and "kc-feedback-text" in (a.get("class") or "") or a.get("id") == "input-error":
            self._in_error = True

    def handle_data(self, data):
        if self._in_error and data.strip():
            self.error += data.strip() + " "

    def handle_endtag(self, tag):
        if tag == "span":
            self._in_error = False


def _login_action(html: str) -> tuple:
    """Where to post the login, and hidden fields. Weheat's login page is a
    Keycloakify app: the address is in its kcContext JSON (url.loginAction);
    a classic Keycloak page has it in <form id="kc-form-login">."""
    m = re.search(r'"loginAction"\s*:\s*"([^"]+)"', html)
    if m:
        return m.group(1).replace("\\/", "/").replace("&amp;", "&"), {}
    form = _LoginForm()
    form.feed(html)
    return (form.action or "").replace("&amp;", "&"), form.fields


def _login_message(html: str) -> str:
    """The error Keycloak shows (wrong password, ...), if any."""
    m = re.search(r'"message"\s*:\s*\{[^}]*?"summary"\s*:\s*"([^"]*)"', html, re.S)
    if m:
        return m.group(1)
    form = _LoginForm()
    form.feed(html)
    return form.error.strip()


def _tokens_from(resp_json: dict) -> dict:
    now = time.time()
    return {"access_token": resp_json["access_token"],
            "access_expires": now + resp_json.get("expires_in", 300) - 30,
            "refresh_token": resp_json.get("refresh_token"),
            "refresh_expires": now + resp_json.get("refresh_expires_in", 1800) - 30
            if resp_json.get("refresh_expires_in") else None}


def login(cfg) -> dict:
    """Log in like a browser: login page, post the form, catch the code at the
    redirect address (not followed), exchange it for tokens."""
    s = requests.Session()
    s.headers["User-Agent"] = UA
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    r = s.get(f"{AUTH}/auth", timeout=30, params={
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
        "scope": "openid", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    r.raise_for_status()
    action, fields = _login_action(r.text)
    if not action:
        raise RuntimeError("Weheat's login page has no login address (did the page change?)")
    data = dict(fields, username=cfg["username"], password=cfg["password"], credentialId="")
    r = s.post(urljoin(r.url, action), data=data, timeout=30, allow_redirects=False)
    # Keycloak answers with a redirect to REDIRECT_URI?code=...; anything else is
    # the login page again (wrong password) or an extra step (terms, new password)
    loc = r.headers.get("Location", "")
    if r.status_code not in (302, 303) or not loc.startswith(REDIRECT_URI):
        why = _login_message(r.text)
        raise RuntimeError("Login failed" + (f": {why}" if why else
                           f" (HTTP {r.status_code}; log in once in a browser at {REDIRECT_URI} to see why)"))
    q = parse_qs(urlparse(loc).query)
    if q.get("state", [""])[0] != state or "code" not in q:
        raise RuntimeError("Login: no code in Weheat's answer")
    r = s.post(f"{AUTH}/token", timeout=30, data={
        "grant_type": "authorization_code", "client_id": CLIENT_ID, "code": q["code"][0],
        "redirect_uri": REDIRECT_URI, "code_verifier": verifier})
    if not r.ok:
        raise RuntimeError(f"Token exchange failed: HTTP {r.status_code} {r.text[:200]}")
    return _tokens_from(r.json())


def access_token(cfg, force_login=False) -> str:
    """A valid access token: the saved one, else refreshed, else a new login."""
    with _lock:
        st = load_state(cfg)
        tok = st.get("tokens") or {}
        now = time.time()
        if not force_login and tok.get("access_token") and tok.get("access_expires", 0) > now:
            return tok["access_token"]
        new = None
        if not force_login and tok.get("refresh_token") and (tok.get("refresh_expires") or now + 1) > now:
            r = requests.post(f"{AUTH}/token", timeout=30, headers={"User-Agent": UA}, data={
                "grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": tok["refresh_token"]})
            if r.ok:
                new = _tokens_from(r.json())
        if new is None:
            new = login(cfg)
            log("Logged in to Weheat.")
        st["tokens"] = new
        save_state(cfg, st)
        return new["access_token"]


def jwt_claims(tok: str) -> dict:
    try:
        p = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    except Exception:
        return {}


# ── API ──────────────────────────────────────────────────────────────────────
def call(cfg, method: str, url: str, body=None) -> requests.Response:
    for attempt in (0, 1):
        tok = access_token(cfg, force_login=attempt == 1)
        r = requests.request(method, url, json=body, timeout=30, headers={
            "Authorization": f"Bearer {tok}", "Accept": "application/json", "User-Agent": UA})
        if r.status_code != 401:
            return r
    return r


def get_json(cfg, url: str):
    r = call(cfg, "GET", url)
    if not r.ok:
        raise RuntimeError(f"GET {url}: HTTP {r.status_code} {r.text[:300]}")
    return r.json()


def ids(cfg) -> tuple:
    """(installation_id, control_board_id) from config.json, else from state.json (found by 'find')."""
    st = load_state(cfg)
    inst = cfg.get("installation_id") or st.get("installation_id")
    board = cfg.get("control_board_id") or st.get("control_board_id")
    if not inst:
        sys.exit("No installation_id: run 'find' first, or put it in config.json.")
    return inst, board


def read_settings(cfg) -> dict:
    inst, _ = ids(cfg)
    return get_json(cfg, f"{API}/installations/{inst}/settings")


def _find_key(obj, names):
    """The first value whose key is one of names, anywhere in a nested JSON."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names:
                return v
        for v in obj.values():
            hit = _find_key(v, names)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_key(v, names)
            if hit is not None:
                return hit
    return None


def summary(settings) -> dict:
    method = _find_key(settings, ("ControlMethod", "controlMethod"))
    curve = _find_key(settings, ("HeatingCurveY", "heatingCurveY"))
    return {"control_method": method,
            "control_method_name": METHOD_NAME.get(method, "unknown" if method is None else f"other ({method})"),
            "heating_curve_y": curve}


def remember_curve(cfg, settings=None) -> list | None:
    """Save the current curve points, only while the pump is on its heating curve."""
    s = summary(settings or read_settings(cfg))
    if s["control_method"] != HEATING_CURVE or not s["heating_curve_y"]:
        return None
    st = load_state(cfg)
    st["saved_curve"] = {"HeatingCurveY": s["heating_curve_y"], "saved_at": datetime.now().isoformat(timespec="seconds")}
    save_state(cfg, st)
    return s["heating_curve_y"]


def patch(cfg, settings: dict, dry_run=False) -> dict:
    _, board = ids(cfg)
    if not board:
        sys.exit("No control_board_id: run 'find' first, or put it in config.json.")
    body = {"settings": settings, "updateImmediately": bool(cfg.get("update_immediately", False))}
    url = f"{API}/control-boards/{board}/settings"
    if dry_run:
        return {"dry_run": True, "method": "PATCH", "url": url, "body": body}
    r = call(cfg, "PATCH", url, body)
    if not r.ok:
        raise RuntimeError(f"PATCH {url}: HTTP {r.status_code} {r.text[:300]}")
    return {"ok": True, "status": r.status_code}


def to_opentherm(cfg, dry_run=False) -> dict:
    s = read_settings(cfg)
    now = summary(s)
    if now["control_method"] == OPENTHERM:
        return {"changed": False, "message": "already on OpenTherm", **now}
    saved = remember_curve(cfg, s)
    if saved is None and not (load_state(cfg).get("saved_curve")):
        raise RuntimeError("Not switching: couldn't read the current curve, so it couldn't be put back later.")
    res = patch(cfg, {"ControlMethod": OPENTHERM}, dry_run)
    if not dry_run:
        log(f"Switched to OpenTherm (curve remembered: {saved}).")
    return {"changed": not dry_run, "saved_curve": saved, **res}


def to_curve(cfg, dry_run=False) -> dict:
    saved = (load_state(cfg).get("saved_curve") or {}).get("HeatingCurveY")
    if not saved:
        raise RuntimeError("No remembered curve: run 'save' while the pump is on its heating curve.")
    now = summary(read_settings(cfg))
    if now["control_method"] == HEATING_CURVE and now["heating_curve_y"] == saved:
        return {"changed": False, "message": "already on the heating curve", **now}
    res = patch(cfg, {"ControlMethod": HEATING_CURVE, "HeatingCurveY": saved}, dry_run)
    if not dry_run:
        log(f"Switched to the heating curve {saved}.")
    return {"changed": not dry_run, "curve": saved, **res}


# ── finding the installation and control board ──────────────────────────────
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _walk_ids(obj, path=""):
    """(path, key, value) for every GUID-looking value, to show what's where."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and GUID.match(v):
                yield path, k, v
            else:
                yield from _walk_ids(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            yield from _walk_ids(v, f"{path}[{i}]")


def find(cfg) -> dict:
    """Try the likely list endpoints; print every id found and keep the obvious ones."""
    found = {}
    for url in (f"{API}/installations", f"{API}/heat-pumps", f"{API}/users/me",
                f"{THIRD_PARTY}/heat-pumps"):
        r = call(cfg, "GET", url)
        print(f"\nGET {url} -> HTTP {r.status_code}")
        if not r.ok:
            continue
        try:
            data = r.json()
        except ValueError:
            continue
        for path, key, val in _walk_ids(data):
            print(f"  {path + '.' if path else ''}{key} = {val}")
            k = key.lower()
            if url.endswith("/installations") and k == "id" and "installation_id" not in found:
                found["installation_id"] = val
            if "controlboard" in k.replace("_", "") and "control_board_id" not in found:
                found["control_board_id"] = val
    if found:
        st = load_state(cfg)
        st.update(found)
        save_state(cfg, st)
    return found


# ── HTTP API for Homey ───────────────────────────────────────────────────────
def serve(cfg):
    host, port = cfg.get("api_host", "0.0.0.0"), int(cfg.get("api_port", 8095))
    secret = cfg.get("api_token") or ""
    if not secret:
        sys.exit("config.json: set 'api_token' (any long random text) before running the API.")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self):
            given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip() \
                or parse_qs(urlparse(self.path).query).get("token", [""])[0]
            return secrets.compare_digest(given, secret)

        def _route(self, method):
            path = urlparse(self.path).path.rstrip("/")
            if path == "/health":
                return self._send(200, {"ok": True})
            if not self._authorised():
                return self._send(401, {"error": "wrong or missing token"})
            try:
                if method == "GET" and path == "/status":
                    st = load_state(cfg)
                    return self._send(200, {**summary(read_settings(cfg)), "saved_curve": st.get("saved_curve")})
                if method == "POST" and path == "/opentherm":
                    return self._send(200, to_opentherm(cfg))
                if method == "POST" and path == "/curve":
                    return self._send(200, to_curve(cfg))
                return self._send(404, {"error": "use GET /status, POST /opentherm or POST /curve"})
            except Exception as e:
                log(f"{method} {path}: {e}")
                return self._send(502, {"error": str(e)})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def log_message(self, fmt, *args):   # keep tokens in query strings out of the log
            log(f"{self.client_address[0]} {self.command} {urlparse(self.path).path}")

    log(f"WeHeat-switch API on http://{host}:{port}  (GET /status, POST /opentherm, POST /curve)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


# ── command line ─────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Switch a Weheat heat pump between OpenTherm and the heating curve.")
    p.add_argument("command", choices=["login", "find", "status", "save", "opentherm", "curve", "serve"])
    p.add_argument("--dry-run", action="store_true", help="opentherm / curve: only show what would be sent")
    p.add_argument("--raw", action="store_true", help="status: print the whole settings answer")
    a = p.parse_args()
    cfg = load_config()
    try:
        if a.command == "login":
            claims = jwt_claims(access_token(cfg, force_login=True))
            print(f"Logged in as {claims.get('preferred_username') or claims.get('email') or '?'}; "
                  f"token valid until {datetime.fromtimestamp(claims.get('exp', 0)):%H:%M:%S}")
        elif a.command == "find":
            found = find(cfg)
            print("\nSaved in state.json:" if found else "\nNothing saved; put the ids in config.json yourself.", found or "")
        elif a.command == "status":
            s = read_settings(cfg)
            print(json.dumps(s if a.raw else summary(s), indent=2))
            saved = load_state(cfg).get("saved_curve")
            print(f"Remembered curve: {saved}" if saved else "No curve remembered yet ('save').")
        elif a.command == "save":
            c = remember_curve(cfg)
            print(f"Remembered curve: {c}" if c else "Not on the heating curve now: nothing saved.")
        elif a.command == "opentherm":
            print(json.dumps(to_opentherm(cfg, a.dry_run), indent=2))
        elif a.command == "curve":
            print(json.dumps(to_curve(cfg, a.dry_run), indent=2))
        elif a.command == "serve":
            serve(cfg)
    except RuntimeError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
