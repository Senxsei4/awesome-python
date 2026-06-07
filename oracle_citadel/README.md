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

# Optional: forward trading data to an external website (see below)
ORACLE_ROUTE_EXTERNAL=false
ORACLE_DATA_RELAY_URL=
ORACLE_DATA_RELAY_TOKEN=
```

| Variable | Required | Purpose |
| --- | --- | --- |
| `ORACLE_TELEGRAM_TOKEN` / `ORACLE_TELEGRAM_CHAT_ID` | for alerts | Telegram bot + destination chat. Absent ⇒ Telegram messages dropped. |
| `ORACLE_WEBHOOK_PASSPHRASE` | yes | Shared secret on every `/webhook` POST. Unset ⇒ all webhooks rejected. |
| `ORACLE_WEBHOOK_HOST` / `ORACLE_WEBHOOK_PORT` | no | Bind address (default `0.0.0.0:80`). |
| `ORACLE_ADMIN_USER` / `ORACLE_ADMIN_PASSWORD` | no | Optional bootstrap account, first run only. |
| `ORACLE_ROUTE_EXTERNAL` / `ORACLE_DATA_RELAY_URL` / `ORACLE_DATA_RELAY_TOKEN` | no | External data relay (default off). |

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
