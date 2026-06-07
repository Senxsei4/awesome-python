# Oracle AI — Project Citadel

A MetaTrader 5 / NinjaTrader 8 auto-trader with a CustomTkinter dashboard. It
ingests TradingView-style webhook signals, sizes positions with a dynamic risk
model, detects market regimes with a lazy-loaded scikit-learn K-Means model,
reports to Telegram, and can relay every trading event to an external website.
It runs as a desktop app (Windows `.exe`) or fully headless on a VPS.

> ⚠️ **Live trading carries financial risk — you assume all of it.** Test on a
> demo account first.

## Contents

- [Configuration (secrets)](#configuration-secrets)
- [Multi-user data isolation](#multi-user-data-isolation)
- [Running — desktop GUI](#running--desktop-gui)
- [Running — headless on a VPS](#running--headless-on-a-vps)
- [External data relay (send to any website)](#external-data-relay-send-to-any-website)
- [Remote control (website buttons → live reconfig)](#remote-control-website-buttons--live-reconfig)
- [Webhook signal format](#webhook-signal-format)
- [Building the Windows .exe (desktop icon)](#building-the-windows-exe-desktop-icon)
- [Runtime state files](#runtime-state-files)

## Configuration (secrets)

All secrets come from the environment — nothing is hardcoded. On startup the app
also loads a `.env` file next to `oracle_citadel.py` (real env vars take
precedence). `.env` and all runtime state are gitignored.

Create `oracle_citadel/.env`:

```dotenv
# Telegram bot for alerts + inbound command & control
ORACLE_TELEGRAM_TOKEN=123456:your-telegram-bot-token
ORACLE_TELEGRAM_CHAT_ID=000000000

# Shared secret required on every inbound webhook. If unset, the webhook
# endpoint fails closed (HTTP 503) and rejects all signals.
ORACLE_WEBHOOK_PASSPHRASE=choose-a-long-random-string

# Webhook bind address (optional; CLI flags --host/--port override these)
ORACLE_WEBHOOK_HOST=0.0.0.0
ORACLE_WEBHOOK_PORT=80

# Optional bootstrap login, seeded only on first run. Otherwise register an
# operator via the GUI "CREATE CLEARANCE" flow.
ORACLE_ADMIN_USER=
ORACLE_ADMIN_PASSWORD=

# External website that hosts your dashboard + control buttons.
# Endpoints below default to this base if their own URL is left blank.
ORACLE_SITE_BASE=https://www.alphadomain.space

# Optional: forward trading data to an external website (see below)
ORACLE_ROUTE_EXTERNAL=false
ORACLE_DATA_RELAY_URL=
ORACLE_DATA_RELAY_TOKEN=

# Optional: let the website's buttons reconfigure Oracle in real time (see
# "Remote control" below). Shared secret used both to authenticate the push
# /control endpoint and as the Bearer token when polling the site.
ORACLE_CONTROL_PASSPHRASE=choose-another-long-random-string
ORACLE_CONTROL_POLL=false
ORACLE_CONTROL_POLL_URL=
```

| Variable | Required | Purpose |
| --- | --- | --- |
| `ORACLE_TELEGRAM_TOKEN` / `ORACLE_TELEGRAM_CHAT_ID` | for alerts | Telegram bot + destination chat. Absent ⇒ Telegram messages dropped. |
| `ORACLE_WEBHOOK_PASSPHRASE` | yes | Shared secret on every `/webhook` POST. Unset ⇒ all webhooks rejected. |
| `ORACLE_WEBHOOK_HOST` / `ORACLE_WEBHOOK_PORT` | no | Bind address (default `0.0.0.0:80`). |
| `ORACLE_ADMIN_USER` / `ORACLE_ADMIN_PASSWORD` | no | Optional bootstrap account, first run only. |
| `ORACLE_ROUTE_EXTERNAL` / `ORACLE_DATA_RELAY_URL` / `ORACLE_DATA_RELAY_TOKEN` | no | External data relay (default off). |
| `ORACLE_SITE_BASE` | no | Base URL of your site (default `https://www.alphadomain.space`); relay/control URLs default under it. |
| `ORACLE_CONTROL_PASSPHRASE` / `ORACLE_CONTROL_POLL` / `ORACLE_CONTROL_POLL_URL` | no | Remote control auth + pull-poll (default off). |

## Multi-user data isolation

Multiple operators can use Oracle without ever touching each other's data. Every
piece of per-operator state is namespaced by the logged-in username:

- `oracle_settings_<user>.json` — risk config, routing, relay URL/token
- `oracle_accounts_<user>.json` — that user's linked MT5 sub-accounts
- `oracle_memory_<user>.json` — evolution memory, high-water mark
- `rlhf_dataset_<user>.json` — that user's trade feedback log

The **only** shared file is `oracle_users.json`, the credential registry used at
the login screen. A new user registers via **CREATE CLEARANCE**, and from first
login onward their settings, accounts, memory, and feedback are kept entirely
separate. The external relay also tags every payload with `"user": <name>` so a
shared external sink can keep each operator's stream distinct.

> For two users to run **simultaneously** on one host, give each its own process
> and a distinct `--port` (port 80 can only be bound once). Headless example:
> `--user alice --port 8081` and `--user bob --port 8082`.

## Running — desktop GUI

```bash
pip install -r requirements.txt
python oracle_citadel.py
```

Then: **log in / CREATE CLEARANCE → Accounts tab → ADD ACCOUNT** (MT5 login,
password, server) **→ ⚙️ → SET AS PRIMARY** to connect the auto-trader to that
MT5 terminal. The webhook listener starts automatically after login.

## Running — headless on a VPS

No display required (the GUI/Tk stack is imported defensively and skipped):

```bash
python oracle_citadel.py --headless --user alice --port 8080
```

- `--headless` runs the Flask webhook server + all background workers with no UI.
- Operator identity comes from `--user` (or `$ORACLE_ADMIN_USER`) so per-user
  state files resolve correctly; the webhook stays guarded by the passphrase.
- `--host` / `--port` override the env defaults.
- Logs go to stdout (capture them with systemd/journald).
- If the GUI stack can't be imported, the app auto-falls back to headless.

Example systemd unit:

```ini
[Service]
WorkingDirectory=/opt/oracle_citadel
EnvironmentFile=/opt/oracle_citadel/.env
ExecStart=/usr/bin/python3 oracle_citadel.py --headless --user alice --port 8080
Restart=always
```

## External data relay (send to any website)

Oracle can forward its trading data to any external HTTPS endpoint you control.
Enable it via the **Routing** tab (URL + optional Bearer token) or via the
`ORACLE_ROUTE_EXTERNAL` / `ORACLE_DATA_RELAY_URL` / `ORACLE_DATA_RELAY_TOKEN`
env vars. Delivery is asynchronous (its own queue/worker), so a slow or
unreachable site never blocks trade execution.

Each event is POSTed as JSON:

```json
{
  "source": "oracle_citadel",
  "event": "trade_closed",
  "user": "alice",
  "timestamp": "2026-06-07T14:03:22.117Z",
  "data": { "symbol": "USTEC", "type": "LONG", "lots": 1.0, "net": 42.5, "...": "..." }
}
```

If a token is set it is sent as `Authorization: Bearer <token>`. Relayed events:

| `event` | When |
| --- | --- |
| `signal_received` | An authenticated webhook signal arrives (secret key stripped). |
| `trade_entry` | A market order / sniper-grid entry fills. |
| `trade_rejected` | MT5 rejects an entry. |
| `partial_close` | A scale-out partial close executes. |
| `trade_closed` | A position fully closes (autopsy with PnL). |
| `eod_report` | The daily end-of-day summary is generated. |

## Remote control (website buttons → live reconfig)

Buttons on your site (`www.alphadomain.space`) can drive Oracle on the VPS in
real time — pause/resume, flatten, request a report, or change risk/routing
settings on the fly. Two transports share the same handler; pick whichever fits
your VPS's network:

### Push — site calls Oracle (lowest latency)

If the VPS is reachable, your site's backend POSTs each button press to Oracle's
`/control` endpoint (same server/port as the webhook):

```bash
curl -X POST http://<vps-ip>:<port>/control \
  -H "Content-Type: application/json" \
  -d '{"key":"<ORACLE_CONTROL_PASSPHRASE>","command":"pause"}'
```

### Pull — Oracle polls the site (firewall-friendly)

If the VPS has no inbound access, enable polling (`ORACLE_CONTROL_POLL=true` or
the **Enable Remote Control Polling** switch in the Network tab). Oracle then
long-polls `ORACLE_CONTROL_POLL_URL` (default
`https://www.alphadomain.space/api/oracle/commands`) every ~2s. Your site
returns queued button presses and Oracle echoes back the highest processed id
as `?after=` so you can dequeue:

```
GET /api/oracle/commands?user=alice&after=12
Authorization: Bearer <ORACLE_CONTROL_PASSPHRASE>

200 OK
{ "commands": [ { "id": 13, "command": "set_config", "settings": { "risk_pct": 0.5 } } ] }
```

### Commands

| `command` | Effect |
| --- | --- |
| `pause` / `resume` | Halt or arm signal processing (real time). |
| `flat` | Flatten the whole portfolio (MT5 + NT8). |
| `report` | Generate & send the EOD dossier. |
| `status` | Return a live snapshot (pause state, regime, settings, PnL). |
| `set_config` | Merge a `settings` object into live config — applied on the next worker loop. |

`set_config` only accepts an **allowlist** of trading/routing knobs (`risk_pct`,
`max_port_risk`, `trade_chop`, `route_mt5`, `route_external`, `data_relay_url`,
`control_poll_url`, …). Auth secrets (webhook/control passphrases, Telegram
token) are **deliberately not remotely changeable**, so a compromised button
page can never rotate Oracle's credentials. Both transports require the control
passphrase; the push endpoint fails closed (HTTP 503) when it isn't configured.

## Webhook signal format

POST JSON to `http://<host>:<port>/webhook`:

```json
{
  "key": "choose-a-long-random-string",
  "symbol": "NAS100",
  "action": "buy",
  "tv_price": 20000.0,
  "sl_price": 19950.0,
  "tp_price": 20100.0,
  "lot_mult": 1.0
}
```

`action` may be `buy`, `sell`, `close`, or `partial` (with a `pct` field).

## Building the Windows .exe (desktop icon)

End users don't need Python — ship them a single clickable `OracleAI.exe`.

### Option A — GitHub Actions (no local Windows box needed)

The repo includes `.github/workflows/build-oracle-windows.yml`, which builds the
exe **and** the Desktop-icon installer on a Windows runner:

- Run it manually from the **Actions** tab (*Build Oracle Windows EXE* →
  *Run workflow*) and download the `OracleAI-exe` / `OracleAI-Setup` artifacts.
- Or push a tag like `oracle-v24.7` to publish them on a GitHub **Release** that
  users can download directly.

### Option B — build locally on Windows

PyInstaller can't cross-compile, so **build on Windows**:

```bat
build_windows.bat
```

This produces `dist\OracleAI.exe` — a windowed, single-file app with the Oracle
icon (`assets/oracle.ico`) and a startup splash.

To give users a proper install experience with a **Desktop icon + Start Menu
entry**, compile the bundled [Inno Setup](https://jrsoftware.org/isinfo.php)
script after building the exe:

```bat
iscc installer.iss
```

That outputs `Output\OracleAI-Setup.exe`. When a user runs it, Oracle installs
to Program Files, a **Desktop shortcut** and **Start Menu** entry appear, and
they can launch Oracle from the desktop and connect the auto-trader (Accounts →
ADD ACCOUNT → SET AS PRIMARY).

Already have the bare exe and just want the shortcut? Run
`create_desktop_shortcut.ps1`.

> Note: the webhook binds port 80 by default, which needs admin rights on
> Windows (the installer requests elevation). Launch with a shortcut argument
> like `OracleAI.exe --port 8080` to use a non-privileged port.

## Runtime state files

Written next to the executable; they hold credentials and trading history and
are gitignored — **never commit them**:

- `oracle_users.json`
- `oracle_settings_<user>.json`, `oracle_accounts_<user>.json`, `oracle_memory_<user>.json`
- the legacy unsuffixed `oracle_settings.json` / `oracle_accounts.json` / `oracle_memory.json`
- `rlhf_dataset_<user>.json`
