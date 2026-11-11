# WeHeat-Control

Switch a **Weheat** heat pump between its **heating curve** and **OpenTherm**
from **Homey** (or the command line), on your own network.

| Mode | What the pump does |
|---|---|
| `curve` | Runs on its **heating curve**: the water temperature follows the outside temperature (ControlMethod 1). Going back to `curve` puts back exactly the curve points it had before. |
| `opentherm` | The **OpenTherm** room thermostat decides (ControlMethod 4). |

Homey sends `POST http://<server>:8095/api/mode` with `{"mode": "opentherm"}`
or `{"mode": "curve"}`.

> **Experimental.** This uses the API Weheat's own web portal uses
> (`https://api.weheat.nl/api/v1`). It's not a documented public API and may
> change without notice. It changes settings on the heat pump: use it on your
> own pump only, and check the result in the Weheat app the first times.

## How it works

1. **Login.** It logs in to the Weheat portal (`portal.weheat.nl`) with your
   own Weheat username and password, the way a browser does: the portal's
   sign-in, Weheat's login page (`auth.weheat.nl`), back to the portal, and
   then the portal session's token (about 5 minutes; renewed by itself, a new
   login when needed). Weheat's settings API only accepts the portal's own
   token: a token from the official third-party API gets *"This endpoint is
   not available for third-party API clients"*. (That official API is
   read-only; `find` also uses it.)
2. **Read.** `GET /api/v1/installations/<installation>/settings` returns the
   pump's settings, including `ControlMethod` (`1` = heating curve, `4` =
   OpenTherm) and `HeatingCurveY` (the curve points).
3. **Remember.** Each time it leaves the heating curve, it first saves the
   curve points in `state.json`. It refuses to switch if it can't read them,
   so it can always go back.
4. **Switch.** `PATCH /api/v1/control-boards/<control board>/settings`:
   - OpenTherm: `{"settings":{"ControlMethod":4},"updateImmediately":false}`
   - back to the curve: `{"settings":{"ControlMethod":1,"HeatingCurveY":[44.1,28,24,19,18]},"updateImmediately":false}`
     (the remembered points)

   A mode the pump is already in sends nothing.

## Install

On a computer that's always on, on the same network as Homey (a Raspberry Pi
or a small Linux server). Needs Python 3.10 or newer. The code is at
https://github.com/Xibel/WeHeat-Control.

```sh
git clone https://github.com/Xibel/WeHeat-Control.git weheat-control
cd weheat-control
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json
chmod 600 config.json
```

Fill in `config.json`:

| Setting | What |
|---|---|
| `username`, `password` | Your Weheat login (the same as in the Weheat app). |
| `installation_id` | Leave empty: `find` fills it in. Or copy it from the portal's address bar (`portal.weheat.nl/.../installations/<id>/...`). |
| `control_board_id` | Leave empty: `find` fills it in when it can. Otherwise: open the portal in Chrome, developer tools (F12) → Network, change a setting, and look at the `control-boards/<id>/settings` request. |
| `update_immediately` | `false`, what the portal sends: the pump takes the change at its next sync. |
| `api_host`, `api_port` | Where the API listens (default all network cards, port `8095`). |
| `api_token` | Optional. When set, Homey must send it (see below). Recommended. |
| `state_file` | Tokens, ids and the remembered curve (default `state.json`). |

`config.json` and `state.json` hold your password and tokens. They're in
`.gitignore`; never share them.

## Step 1: try it (read only)

```sh
.venv/bin/python weheat_control.py login          # can we log in?
.venv/bin/python weheat_control.py find           # installation and control board
.venv/bin/python weheat_control.py status         # mode, curve, what's remembered
.venv/bin/python weheat_control.py status --raw   # every setting, as Weheat sends it
```

## Step 2: switch by hand

```sh
.venv/bin/python weheat_control.py save                      # remember the curve now
.venv/bin/python weheat_control.py mode opentherm --dry-run  # only show what would be sent
.venv/bin/python weheat_control.py mode opentherm
.venv/bin/python weheat_control.py mode curve
```

Then check in the Weheat app that the pump did what you expected. If you
change the curve yourself later, that's fine: the curve is saved again each
time the pump leaves it.

## Step 3: the API for Homey

```sh
.venv/bin/python weheat_control.py serve
```

| Request | What |
|---|---|
| `POST /api/mode` with `{"mode": "opentherm"}` | Switch to OpenTherm |
| `POST /api/mode` with `{"mode": "curve"}` | Back to the heating curve (the remembered points) |
| `GET /api/mode` | Now: control method, curve, last mode, the remembered curve |
| `GET /api/health` | `{"ok": true}`; no token needed |

Answers are JSON, for example
`{"changed": true, "mode": "opentherm", "sent": {"ControlMethod": 4}, "ok": true}`.
A wrong body gives HTTP 400; a problem with Weheat gives HTTP 502 with
`{"error": "..."}`.

### The API token

The `api_token` is only between Homey and this script. Weheat never sees it.

- **What it is:** a made-up shared password, `api_token` in `config.json`.
  The script checks it on every request. Without it, or with a wrong one, it
  answers 401 and changes nothing. Only `GET /api/health` works without it.
- **Why:** without it, anyone on the network who knows the address could
  switch the heat pump.
- **Where it goes:** put the same value in the Homey flow, as the header
  `Authorization: Bearer <api_token>`, or as `?token=<api_token>` at the end
  of the address (for cards that can't set headers).
- **Separate from the Weheat login:** the script logs in to Weheat by itself
  with the `username` and `password` in `config.json`, and keeps that Weheat
  token to itself. Homey never needs your Weheat password.
- **Leaving it empty** turns the check off: the script logs a warning at
  start-up, and anyone on the network can switch the pump. Not recommended.

Make up your own long random text, for example:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

### In Homey

**HomeyScript** (a "Run a script with argument" card; the argument is
`opentherm` or `curve`):

```javascript
const BASE = "http://192.168.1.20:8095";   // the computer running WeHeat-Control
const TOKEN = "your api_token";            // or "" without a token

const mode = args[0];                      // "opentherm" or "curve"
const r = await fetch(`${BASE}/api/mode`, {
  method: "POST",
  headers: { "Content-Type": "application/json", ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}) },
  body: JSON.stringify({ mode }),
});
const answer = await r.json();
if (!r.ok) throw new Error(answer.error);
return answer.mode;
```

**Without scripting**, use an HTTP request card (for example from the "HTTP
request flow cards" app): method `POST`, address
`http://192.168.1.20:8095/api/mode?token=<your api_token>`, body
`{"mode": "opentherm"}` (or `"curve"`), content type `application/json`.

Plain HTTP: keep the API on your own network, don't forward the port to the
internet.

### Keep it running (Linux, systemd)

`/etc/systemd/system/weheat-control.service`:

```ini
[Unit]
Description=WeHeat-Control API
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/weheat-control
ExecStart=/opt/weheat-control/.venv/bin/python weheat_control.py serve
Restart=on-failure
User=weheat

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl enable --now weheat-control
journalctl -u weheat-control -f        # every switch is logged
```
