# Oracle Project Structure

Two ways to set this project up on your own machine:

1. **Run the generator (recommended).** One self-contained file recreates the
   entire tree below — no git, no copy-paste — and can walk you through creating
   your `.env` files:

   ```bash
   python build_oracle_project.py            # writes files, then offers guided setup
   python build_oracle_project.py --dest oracle   # …or into ./oracle
   python build_oracle_project.py --force    # overwrite existing files
   python build_oracle_project.py --configure # ONLY run the guided .env setup
   python build_oracle_project.py --no-setup  # write files, skip the wizard
   ```

   It only uses the Python standard library, and skips files that already exist
   unless you pass `--force`. The **guided setup** prompts for your secrets,
   auto-generates strong passphrases (just press Enter), and writes both
   `.env` files with the shared control passphrase + relay token kept identical
   (required for the bot and panel to talk). In a non-interactive shell it skips
   the wizard instead of hanging.

2. **Build the folders by hand** using the map below, then paste each file's
   contents from the repo.

## Folder tree

```
.
├── build_oracle_project.py        # the one-file scaffolder (run to recreate everything)
├── STRUCTURE.md                   # this file
│
├── oracle_citadel/                # the MT5 / NinjaTrader auto-trader (the bot)
│   ├── oracle_citadel.py          # main app: webhook server, GUI, workers, ML, relays
│   ├── requirements.txt           # python deps for the bot
│   ├── README.md                  # setup, headless mode, relay, remote control, exe build
│   ├── .gitignore                 # keeps secrets + per-user state + build output out of git
│   ├── oracle_citadel.spec        # PyInstaller spec -> OracleAI.exe (windowed, icon, splash)
│   ├── build_windows.bat          # one-click Windows build script
│   ├── installer.iss              # Inno Setup installer -> Desktop + Start Menu shortcuts
│   ├── create_desktop_shortcut.ps1# makes a desktop shortcut for the bare exe
│   └── assets/
│       ├── oracle.ico             # app icon (multi-resolution)
│       └── oracle.png             # icon source / splash image
│
├── control_panel/                 # the website half (deploy on www.alphadomain.space)
│   ├── app.py                     # Flask service: /api/oracle/* and /api/panel/* endpoints
│   ├── templates/
│   │   └── index.html             # the operator button page (dark Oracle theme)
│   ├── requirements.txt           # flask + gunicorn
│   ├── README.md                  # endpoints, config, Docker deploy, prebuilt image
│   ├── Dockerfile                 # panel image (python + gunicorn)
│   ├── docker-compose.yml         # panel + Caddy (automatic HTTPS) — one-command deploy
│   ├── Caddyfile                  # TLS-terminating reverse proxy config
│   ├── .dockerignore
│   └── .gitignore
│
└── .github/
    └── workflows/
        └── build-oracle-windows.yml   # CI: build the exe + publish the panel image on a tag
```

## What you create yourself (never committed)

The guided setup (above) creates the two `.env` files for you. If you skip it
(`--no-setup`) or build by hand, create them yourself — they hold secrets / live
state and are intentionally gitignored. See each README for the exact keys:

| File | Where | Holds |
| --- | --- | --- |
| `oracle_citadel/.env` | bot | Telegram token, webhook + control passphrases, relay/control URLs |
| `control_panel/.env` | panel | `ORACLE_CONTROL_PASSPHRASE`, `PANEL_PASSWORD`, `PANEL_DOMAIN` |
| `oracle_*_<user>.json`, `rlhf_dataset_<user>.json` | bot (auto-created at runtime) | per-user settings, accounts, memory, feedback |

## After scaffolding

```bash
# The bot (desktop)
cd oracle_citadel && pip install -r requirements.txt && python oracle_citadel.py

# The bot (headless VPS)
python oracle_citadel.py --headless --user alice --port 8080

# The control panel (one command + auto HTTPS)
cd control_panel && docker compose up -d --build
```
