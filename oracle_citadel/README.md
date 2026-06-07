# Oracle AI — Project Citadel

A single-file MetaTrader 5 / NinjaTrader 8 trading bot with a CustomTkinter
dashboard. It ingests TradingView-style webhook signals, sizes positions with a
dynamic risk model, detects market regimes with a lazy-loaded scikit-learn
K-Means model, and reports to Telegram. Designed to run on a VPS alongside an
MT5 terminal.

> ⚠️ **For live trading you assume all financial risk.** Test on a demo account
> first.

## Configuration (secrets)

All secrets are read from the environment — nothing is hardcoded. On startup the
app also loads a `.env` file sitting next to `oracle_citadel.py` (real env vars
take precedence). `.env` and all runtime state files are gitignored, so your
credentials never get committed.

Create `oracle_citadel/.env`:

```dotenv
# Telegram bot used for alerts and inbound command & control
ORACLE_TELEGRAM_TOKEN=123456:your-telegram-bot-token
ORACLE_TELEGRAM_CHAT_ID=000000000

# Shared secret required on every inbound webhook. If unset, the webhook
# endpoint fails closed (HTTP 503) and rejects all signals.
ORACLE_WEBHOOK_PASSPHRASE=choose-a-long-random-string

# Optional: seed a bootstrap login the first time the app runs. If omitted,
# register an operator through the GUI "CREATE CLEARANCE" flow instead.
ORACLE_ADMIN_USER=
ORACLE_ADMIN_PASSWORD=
```

| Variable | Required | Purpose |
| --- | --- | --- |
| `ORACLE_TELEGRAM_TOKEN` | for alerts | Telegram bot token. Without it, Telegram messages are dropped. |
| `ORACLE_TELEGRAM_CHAT_ID` | for alerts | Destination chat ID for alerts and command authorization. |
| `ORACLE_WEBHOOK_PASSPHRASE` | yes | Shared secret checked on every `/webhook` POST. Unset ⇒ all webhooks rejected. |
| `ORACLE_ADMIN_USER` / `ORACLE_ADMIN_PASSWORD` | no | Optional bootstrap GUI account, seeded only on first run. |

These can also be exported as real environment variables (e.g. via a systemd
unit) instead of a `.env` file.

## Running

```bash
pip install MetaTrader5 flask requests matplotlib customtkinter pandas numpy scikit-learn
python oracle_citadel.py
```

The Flask webhook listens on `0.0.0.0:80`. Point your TradingView alert at
`http://<vps-ip>/webhook` with a JSON body including your passphrase:

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

## Runtime state files (gitignored)

The app writes per-user JSON state next to the script. These contain
credentials and trading history and must **never** be committed:

- `oracle_users.json`
- `oracle_settings_*.json`, `oracle_accounts_*.json`, `oracle_memory_*.json`
- the legacy unsuffixed `oracle_settings.json` / `oracle_accounts.json` / `oracle_memory.json`
- `rlhf_dataset.json`
