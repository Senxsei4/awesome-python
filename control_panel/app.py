"""Oracle Control Panel — the website half of the remote-control pathway.

Runs on your domain (e.g. www.alphadomain.space) and provides:

  GET  /api/oracle/commands   <- Oracle long-polls this for queued button presses
  POST /api/oracle/events     <- Oracle relays trading data / control acks here
  POST /api/panel/enqueue     <- the button page queues a command
  GET  /api/panel/state       <- the button page reads recent events + queue depth
  GET  /                      <- the operator button page

This is an intentionally small, dependency-light scaffold (in-memory queue +
event ring buffer). For production, put it behind HTTPS (nginx) and consider a
real datastore if you need persistence across restarts.

Config via environment (see README.md):
  ORACLE_CONTROL_PASSPHRASE  shared secret; Oracle sends it as Bearer to /commands
  ORACLE_DATA_RELAY_TOKEN    optional Bearer required on /events (if set)
  PANEL_PASSWORD             gate for the operator page / panel APIs
  PANEL_PORT                 listen port (default 8000)
"""
import os
import threading
import datetime
from collections import deque

from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

CONTROL_PASSPHRASE = os.environ.get("ORACLE_CONTROL_PASSPHRASE", "")
DATA_RELAY_TOKEN = os.environ.get("ORACLE_DATA_RELAY_TOKEN", "")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "")

# Commands Oracle is allowed to receive. set_config carries a "settings" object;
# Oracle independently validates those keys against its own allowlist.
ALLOWED_COMMANDS = {"pause", "resume", "flat", "report", "status", "set_config"}

_lock = threading.Lock()
_seq = 0
_commands = {}                 # user -> list of queued command dicts
_events = deque(maxlen=300)     # most-recent-first ring buffer of relayed events


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _bearer_ok(expected):
    """Validate an Authorization: Bearer <token> header. Fails closed."""
    if not expected:
        return False
    auth = request.headers.get("Authorization", "")
    return auth.startswith("Bearer ") and auth[7:].strip() == expected


def _panel_ok():
    """Gate the operator-facing APIs with the panel password (if configured)."""
    if not PANEL_PASSWORD:
        return True  # open if no password set (dev convenience)
    return request.headers.get("X-Panel-Key", "") == PANEL_PASSWORD


# ---------------------------------------------------------------------------
# Oracle-facing endpoints
# ---------------------------------------------------------------------------
@app.route("/api/oracle/commands", methods=["GET"])
def oracle_commands():
    """Oracle pulls queued button presses for one operator.

    Honors ?user=<name>&after=<id>. Delivered commands are removed (at-most-once)
    so a restarted Oracle never replays old presses.
    """
    if not _bearer_ok(CONTROL_PASSPHRASE):
        return jsonify({"error": "unauthorized"}), 401

    user = request.args.get("user", "")
    try:
        after = int(request.args.get("after", "0"))
    except (TypeError, ValueError):
        after = 0

    with _lock:
        queue = _commands.get(user, [])
        pending = [c for c in queue if c["id"] > after]
        if pending:
            delivered_ids = {c["id"] for c in pending}
            _commands[user] = [c for c in queue if c["id"] not in delivered_ids]
    return jsonify({"commands": pending})


@app.route("/api/oracle/events", methods=["POST"])
def oracle_events():
    """Oracle relays trading events / control acks here."""
    if DATA_RELAY_TOKEN and not _bearer_ok(DATA_RELAY_TOKEN):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    with _lock:
        _events.appendleft({"received": _now(), "payload": data})
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Panel-facing endpoints
# ---------------------------------------------------------------------------
@app.route("/api/panel/enqueue", methods=["POST"])
def panel_enqueue():
    """The button page calls this to queue a command for an operator."""
    if not _panel_ok():
        return jsonify({"error": "unauthorized"}), 401

    global _seq
    data = request.get_json(silent=True) or {}
    user = (data.get("user") or "").strip()
    command = (data.get("command") or "").strip().lower()

    if not user:
        return jsonify({"error": "user required"}), 400
    if command not in ALLOWED_COMMANDS:
        return jsonify({"error": f"unknown command: {command}"}), 400

    entry = {"command": command, "queued": _now()}
    if command == "set_config":
        settings = data.get("settings") or {}
        if not isinstance(settings, dict) or not settings:
            return jsonify({"error": "set_config requires a non-empty settings object"}), 400
        entry["settings"] = settings

    with _lock:
        _seq += 1
        entry["id"] = _seq
        _commands.setdefault(user, []).append(entry)
    return jsonify({"ok": True, "queued": entry})


@app.route("/api/panel/state", methods=["GET"])
def panel_state():
    """The button page polls this to show queue depth + recent events."""
    if not _panel_ok():
        return jsonify({"error": "unauthorized"}), 401
    with _lock:
        depth = {u: len(c) for u, c in _commands.items() if c}
        events = list(_events)[:60]
    return jsonify({"queue_depth": depth, "events": events})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PANEL_PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
