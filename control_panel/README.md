# Oracle Control Panel

The **website half** of Oracle's remote-control pathway. Deploy this on your
domain (`www.alphadomain.space`) and its buttons will drive Oracle on the VPS in
real time, while Oracle's trading data streams into the event feed.

It pairs with `oracle_citadel/` — see *Remote control* and *External data relay*
in that project's README for the Oracle side.

## Endpoints

| Method & path | Caller | Purpose |
| --- | --- | --- |
| `GET /api/oracle/commands` | **Oracle (pull)** | Returns queued button presses for `?user=`, after `?after=<id>`. Auth: `Bearer ORACLE_CONTROL_PASSPHRASE`. Delivered commands are removed (at-most-once). |
| `POST /api/oracle/events` | **Oracle (relay)** | Receives trading events + control acks. Auth: `Bearer ORACLE_DATA_RELAY_TOKEN` (only enforced if that var is set). |
| `POST /api/panel/enqueue` | Button page | Queues a command for an operator. Auth: `X-Panel-Key: PANEL_PASSWORD`. |
| `GET /api/panel/state` | Button page | Queue depth + recent events. Auth: `X-Panel-Key`. |
| `GET /` | Operator | The control button page. |
| `GET /healthz` | — | Health check. |

These paths match Oracle's defaults
(`ORACLE_CONTROL_POLL_URL=https://www.alphadomain.space/api/oracle/commands`,
`ORACLE_DATA_RELAY_URL=https://www.alphadomain.space/api/oracle/events`).

## Configuration

Set these as real environment variables (or a `.env` you export). The control
passphrase here **must equal** Oracle's `ORACLE_CONTROL_PASSPHRASE`.

| Variable | Purpose |
| --- | --- |
| `ORACLE_CONTROL_PASSPHRASE` | Shared secret; Oracle sends it as Bearer when polling `/api/oracle/commands`. |
| `ORACLE_DATA_RELAY_TOKEN` | Optional. If set, required as Bearer on `/api/oracle/events`. Must match Oracle's token. |
| `PANEL_PASSWORD` | Gate for the operator page + panel APIs. Leave empty only for local dev. |
| `PANEL_PORT` | Listen port (default `8000`). |

## Deploy with Docker (one command + automatic HTTPS)

The bundled `docker-compose.yml` runs the panel behind **Caddy**, which
auto-provisions and renews a Let's Encrypt TLS certificate — no certbot needed.

1. Point your domain's DNS **A record** at the server, and open ports **80** and
   **443**.
2. Create a `.env` file next to `docker-compose.yml` (it's gitignored):

   ```dotenv
   PANEL_DOMAIN=www.alphadomain.space
   ORACLE_CONTROL_PASSPHRASE=the-same-secret-oracle-uses
   PANEL_PASSWORD=a-panel-login-secret
   # ORACLE_DATA_RELAY_TOKEN=optional-if-you-set-one-on-oracle
   ```

3. Bring it up:

   ```bash
   docker compose up -d --build
   ```

That's it — the panel is live at `https://www.alphadomain.space`. Caddy gets the
cert on first start (needs the DNS + open ports above) and renews it
automatically; issued certs persist in the `caddy_data` volume across restarts.

```bash
docker compose logs -f      # watch startup / cert issuance
docker compose down         # stop
```

> Local test without a real domain? Set `PANEL_DOMAIN=localhost` and add a
> `tls internal` line to the `Caddyfile` to use a self-signed cert.

### Using the prebuilt image

The *Build & Publish Oracle* workflow pushes this panel to GitHub Container
Registry on every `oracle-v*` tag, so you can skip the local build. Either pull
it directly:

```bash
docker pull ghcr.io/<owner>/oracle-control-panel:latest
```

…or swap the `build: .` line in `docker-compose.yml` for
`image: ghcr.io/<owner>/oracle-control-panel:latest` and just
`docker compose up -d`.

## Run without Docker

```bash
pip install -r requirements.txt
export ORACLE_CONTROL_PASSPHRASE="the-same-secret-oracle-uses"
export PANEL_PASSWORD="a-panel-login-secret"

python app.py                                  # dev
gunicorn -w 2 -b 0.0.0.0:8000 app:app          # production (add your own TLS proxy)
```

The control passphrase travels in the `Authorization` header, so **always serve
over HTTPS in production** — the Docker setup above handles that for you.

## How the loop works

1. Operator opens `/`, enters their **user** (e.g. `alice`) and **panel key**.
2. A button press → `POST /api/panel/enqueue` → command added to `alice`'s queue.
3. Oracle (running `--user alice`, with control polling enabled) long-polls
   `/api/oracle/commands?user=alice&after=<id>`, applies the command, and
   relays a `control_ack` (plus all trade events) to `/api/oracle/events`.
4. The page polls `/api/panel/state` every 3s and shows the event feed + acks.

> Scaffold notes: the queue and event feed are in-memory (reset on restart) and
> single-process. For durability or multiple gunicorn workers, back them with
> Redis or a database. `set_config` settings are validated again on the Oracle
> side against its allowlist, so the panel can't change auth secrets.
