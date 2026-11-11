# WeHeat-switch

Switch a **Weheat** heat pump between **OpenTherm** (the room thermostat
decides) and its **heating curve** (the water temperature follows the outside
temperature), from the command line or from **Homey** through a small HTTP API.

The idea: during the most expensive hours the pump can run on OpenTherm,
which you can turn down, and after them go back to the heating curve. This
first version only does the switching. There's no price logic yet.

> **Experimental.** This uses the API Weheat's own web portal uses
> (`https://api.weheat.nl/api/v1`). It's not a documented public API, so it may
> change without notice. It changes settings on your heat pump: use it on your
> own pump only, and check the result in the Weheat app.

## How it works

1. **Login.** It logs in to Weheat (Keycloak at `auth.weheat.nl`) with your
   own username and password, the same way a browser does: the login page,
   your username and password, then a short-lived token (about 5 minutes). It
   renews the token by itself while it can, and logs in again when it can't.
2. **Find.** It looks up your installation and the pump's control board, and
   remembers their ids.
3. **Read.** `GET /api/v1/installations/<installation>/settings` returns the
   settings, including `ControlMethod` (`1` = heating curve, `4` = OpenTherm)
   and `HeatingCurveY` (the curve points).
4. **Remember the curve.** Before switching to OpenTherm it saves the current
   `HeatingCurveY` in `state.json`, so it can put exactly that curve back.
5. **Switch.** `PATCH /api/v1/control-boards/<board>/settings` with:
   - OpenTherm: `{"settings":{"ControlMethod":4},"updateImmediately":false}`
   - heating curve: `{"settings":{"ControlMethod":1,"HeatingCurveY":[...remembered...]},"updateImmediately":false}`

It never switches to OpenTherm if it couldn't read and save the curve first.

## Install

Needs Python 3.10 or newer.

```sh
cd WeHeat-switch
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json
chmod 600 config.json
```

Then fill in `config.json`:

| Setting | What |
|---|---|
| `username`, `password` | Your Weheat login (the same as in the app). |
| `installation_id` | Leave empty: `find` fills it in. Or paste it from the portal's address bar (`.../installations/<id>/...`). |
| `control_board_id` | Leave empty: `find` fills it in when it can. Otherwise take it from the portal (browser developer tools, the `control-boards/<id>/settings` request). |
| `update_immediately` | `false` (what the portal sends). The pump takes the new setting at its next sync. |
| `api_host`, `api_port` | Where the HTTP API listens (default `0.0.0.0:8095`). |
| `api_token` | Make up a long random text. Homey must send it with every request. |
| `state_file` | Where tokens, ids and the remembered curve are kept (default `state.json`). |

`config.json` and `state.json` hold your password and tokens. They're in
`.gitignore`; never share them.

## Step 1: try it (read only)

```sh
.venv/bin/python weheat_switch.py login     # can we log in?
.venv/bin/python weheat_switch.py find      # which installation and control board?
.venv/bin/python weheat_switch.py status    # which control method, which curve?
.venv/bin/python weheat_switch.py status --raw   # the whole settings answer
```

`status` shows for example:

```json
{
  "control_method": 1,
  "control_method_name": "heating curve",
  "heating_curve_y": [44.1, 28, 24, 19, 18]
}
```

## Step 2: switch

```sh
.venv/bin/python weheat_switch.py save                  # remember the curve now
.venv/bin/python weheat_switch.py opentherm --dry-run   # show what would be sent
.venv/bin/python weheat_switch.py opentherm             # remember the curve, then OpenTherm
.venv/bin/python weheat_switch.py curve --dry-run
.venv/bin/python weheat_switch.py curve                 # back to the remembered curve
```

Switching to a mode the pump is already in does nothing. If you change the
curve yourself later, run `save` again while the pump is on the curve (or let
`opentherm` do it: it saves the curve each time it switches).

## Step 3: the HTTP API for Homey

```sh
.venv/bin/python weheat_switch.py serve
```

| Request | What |
|---|---|
| `GET /health` | `{"ok": true}`; no token needed |
| `GET /status` | Control method, curve, remembered curve |
| `POST /opentherm` | Remember the curve, switch to OpenTherm |
| `POST /curve` | Switch back to the remembered curve |

Send the token as the header `Authorization: Bearer <api_token>`, or as
`?token=<api_token>` when the client can't set headers. Each answer is JSON;
errors come back as HTTP 502 with `{"error": "..."}`.

**In Homey** (an Advanced Flow or a normal flow), use the HTTP request card
(for example the "HTTP request flow cards" app):

- method `POST`
- URL `http://<computer running this>:8095/opentherm?token=<api_token>`
  (or `/curve` to go back)

Run it only on your own network; it's plain HTTP.

### Keep it running (Linux, systemd)

`/etc/systemd/system/weheat-switch.service`:

```ini
[Unit]
Description=WeHeat-switch API
After=network-online.target

[Service]
WorkingDirectory=/opt/weheat-switch
ExecStart=/opt/weheat-switch/.venv/bin/python weheat_switch.py serve
Restart=on-failure
User=weheat

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl enable --now weheat-switch
journalctl -u weheat-switch -f
```

## Next

- Price logic: switch to OpenTherm in the 4 most expensive hours of the day
  and back to the curve after them.
