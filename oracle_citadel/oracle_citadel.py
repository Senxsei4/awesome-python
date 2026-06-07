import os
import shutil
import MetaTrader5 as mt5
from flask import Flask, request, jsonify
import requests
import datetime
import json
import threading
import queue
import time
import logging
import tkinter as tk
import customtkinter as ctk
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# =============================================================================
# 🔑 ENVIRONMENT LOADER (dependency-free .env support)
# =============================================================================
def _load_env_file(path=None):
    """Populate os.environ from a .env file sitting next to this script.

    Existing environment variables always win (os.environ.setdefault), so a
    real VPS environment can override the file. Silently no-ops if absent.
    """
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, val)
    except Exception:
        pass

_load_env_file()

# =============================================================================
# 🚀 HYPERVISOR OVERHAUL: LAZY LOADING ML MODULES
# =============================================================================
pd, np, KMeans, StandardScaler, ML_ACTIVE = None, None, None, None, False

def background_ml_loader():
    global pd, np, KMeans, StandardScaler, ML_ACTIVE
    try:
        import pandas as pd_temp
        import numpy as np_temp
        from sklearn.cluster import KMeans as KMeans_temp
        from sklearn.preprocessing import StandardScaler as StandardScaler_temp
        pd, np, KMeans, StandardScaler = pd_temp, np_temp, KMeans_temp, StandardScaler_temp
        ML_ACTIVE = True
        log_to_gui("🧠 Neural Weights (scikit-learn) loaded successfully.", "blue")
    except ImportError:
        ML_ACTIVE = False
        log_to_gui("⚠️ ML modules missing. Regime detection offline.", "red")

# =============================================================================
# ⚡ ASYNC TELEGRAM MESSENGER (NON-BLOCKING)
# =============================================================================
telegram_queue = queue.Queue()

def telegram_worker():
    while True:
        try:
            message_data = telegram_queue.get()
            if message_data is None: break
            msg, color = message_data
            cfg = load_settings()
            token = cfg.get("tg_token", TELEGRAM_TOKEN)
            chat_id = cfg.get("tg_chat", TELEGRAM_CHAT_ID)

            if not token or not chat_id:
                log_to_gui("⚠️ Telegram credentials not configured. Message dropped.", "red")
                telegram_queue.task_done()
                continue

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}

            requests.post(url, json=payload, timeout=5)

            clean_msg = msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", "")
            log_to_gui(f"📡 Dispatched: {clean_msg.replace(chr(10), ' | ')}", color)
            telegram_queue.task_done()
        except Exception as e:
            log_to_gui(f"⚠️ Telegram Dispatch Error: {str(e)}", "red")

threading.Thread(target=telegram_worker, daemon=True).start()

def send_telegram(message, color="white"):
    telegram_queue.put((message, color))

# =============================================================================
# 🔐 VAULT CONFIGURATION, AUTHENTICATION, & GLOBAL STATE
# =============================================================================
# Secrets are sourced from the environment (or a local .env file). Never commit
# real values — see oracle_citadel/README.md for the required variables.
TELEGRAM_TOKEN = os.environ.get("ORACLE_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("ORACLE_TELEGRAM_CHAT_ID", "")
WEBHOOK_PASSPHRASE = os.environ.get("ORACLE_WEBHOOK_PASSPHRASE", "")

# Optional bootstrap admin account. If unset, no default user is seeded and an
# operator must register one through the GUI "CREATE CLEARANCE" flow.
DEFAULT_ADMIN_USER = os.environ.get("ORACLE_ADMIN_USER", "")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ORACLE_ADMIN_PASSWORD", "")

SYSTEM_PAUSED = False
LAST_TRADE_DATA = None
LATEST_REGIME = "CALCULATING..."
ASSET_HEATMAP = {"NAS100": {"score": 0, "color": "#6B7A90", "reg": "SCANNING"},
                 "US30": {"score": 0, "color": "#6B7A90", "reg": "SCANNING"},
                 "XAUUSD": {"score": 0, "color": "#6B7A90", "reg": "SCANNING"}}

PERFORMANCE_DATA = {
    "net_pnl": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "total_trades": 0,
    "best": 0.0, "worst": 0.0, "journal": [], "today_closed_pnl": 0.0
}

CURRENT_USER = None
USERS_FILE = "oracle_users.json"

app = Flask(__name__)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

def load_users():
    if not os.path.exists(USERS_FILE):
        # Only seed a default account when bootstrap credentials are supplied
        # via the environment. Otherwise start empty and require GUI signup.
        default_users = {}
        if DEFAULT_ADMIN_USER and DEFAULT_ADMIN_PASSWORD:
            default_users = {DEFAULT_ADMIN_USER: DEFAULT_ADMIN_PASSWORD}
        with open(USERS_FILE, "w") as f:
            json.dump(default_users, f, indent=4)
        return default_users
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

def migrate_legacy_files(username):
    legacy_files = {
        "oracle_settings.json": f"oracle_settings_{username}.json",
        "oracle_accounts.json": f"oracle_accounts_{username}.json",
        "oracle_memory.json": f"oracle_memory_{username}.json"
    }
    for legacy, new_file in legacy_files.items():
        if os.path.exists(legacy) and not os.path.exists(new_file):
            try: shutil.copy(legacy, new_file)
            except Exception: pass

def get_settings_file(): return f"oracle_settings_{CURRENT_USER}.json" if CURRENT_USER else "oracle_settings.json"
def get_accounts_file(): return f"oracle_accounts_{CURRENT_USER}.json" if CURRENT_USER else "oracle_accounts.json"
def get_memory_file(): return f"oracle_memory_{CURRENT_USER}.json" if CURRENT_USER else "oracle_memory.json"

# =============================================================================
# 🎛️ LOCAL SETTINGS & LEDGER MANAGERS
# =============================================================================
def get_default_nt8_path():
    try:
        return os.path.join(os.path.expanduser('~'), 'Documents', 'NinjaTrader 8', 'incoming')
    except Exception:
        return ""

def load_settings():
    default_settings = {
        "risk_pct": 1.0,
        "max_spread_ratio": 0.15,
        "max_port_risk": 15.0,
        "kelly_active": False,
        "grid_active": False,
        "covar_guard": False,
        "anti_hedge": True,
        "trade_trend": True,
        "trade_chop": True,
        "trade_extreme": True,
        "tg_token": TELEGRAM_TOKEN,
        "tg_chat": TELEGRAM_CHAT_ID,
        "wh_pass": WEBHOOK_PASSPHRASE,
        "route_mt5": True,
        "route_nt8": False,
        "nt8_incoming_path": get_default_nt8_path()
    }
    s_file = get_settings_file()
    if os.path.exists(s_file):
        try:
            with open(s_file, "r") as f:
                loaded = json.load(f)
                default_settings.update(loaded)
        except Exception: pass
    return default_settings

def save_settings(new_settings):
    with open(get_settings_file(), "w") as f:
        json.dump(new_settings, f, indent=4)

def load_accounts():
    a_file = get_accounts_file()
    if os.path.exists(a_file):
        try:
            with open(a_file, "r") as f:
                data = json.load(f)
                if data and "login" not in data[0]: return []
                return data
        except Exception: pass
    return []

def save_accounts(accs):
    with open(get_accounts_file(), "w") as f:
        json.dump(accs, f, indent=4)

def load_oracle_memory():
    m_file = get_memory_file()
    if os.path.exists(m_file):
        try:
            with open(m_file, "r") as f: return json.load(f)
        except Exception: pass
    return {"probation": [], "momentum": [], "blacklist": [], "high_watermark": 50.0}

def save_oracle_memory(memory_data):
    with open(get_memory_file(), "w") as f:
        json.dump(memory_data, f, indent=4)

# =============================================================================
# 🎨 UI COLOR PALETTE & GUI QUEUE
# =============================================================================
BG_COLOR = "#0A0E17"
PANEL_COLOR = "#121A2F"
QUANT_BLUE = "#00E5FF"
ALERT_RED = "#FF3366"
TEXT_WHITE = "#E0E6ED"
MUTED_TEXT = "#6B7A90"
CHART_COLORS = [QUANT_BLUE, "#B200FF", "#00FFAA", "#FF9900", "#FF3366"]

gui_log_queue = queue.Queue()

def log_to_gui(message, color_tag="white"):
    gui_log_queue.put((message, color_tag))

# =============================================================================
# 🛡️ THE MT5 THREAD SAFEGUARD
# =============================================================================
def mt5_safeguard():
    if mt5.terminal_info() is None: mt5.initialize()

def get_tradable_symbol(tv_symbol):
    clean_symbol = tv_symbol.split(':')[-1].replace('-', '').replace('/', '')
    search_terms = {"NAS100": ["US100", "USTEC", "NDX", "NAS100"], "US30": ["DJI", "WS30", "US30"], "XAUUSD": ["GOLD", "XAUUSD"]}.get(clean_symbol, [clean_symbol])
    for term in search_terms:
        info = mt5.symbol_info(term)
        if info and info.trade_mode != mt5.SYMBOL_TRADE_MODE_DISABLED: mt5.symbol_select(term, True); return term
    return clean_symbol

# =============================================================================
# 📡 TELEGRAM COMMAND & CONTROL (INBOUND)
# =============================================================================
def log_rlhf_feedback(feedback):
    global LAST_TRADE_DATA
    if not LAST_TRADE_DATA: return
    dataset = []
    if os.path.exists("rlhf_dataset.json"):
        with open("rlhf_dataset.json", "r") as f: dataset = json.load(f)
    dataset.append({"trade": LAST_TRADE_DATA, "feedback": feedback, "timestamp": str(datetime.datetime.now())})
    with open("rlhf_dataset.json", "w") as f: json.dump(dataset, f, indent=4)
    send_telegram(f"🧠 <b>RLHF LOGGED:</b> Execution marked as {feedback}.", "blue")
    LAST_TRADE_DATA = None

def telegram_listener_worker():
    global SYSTEM_PAUSED
    mt5_safeguard()
    last_update_id = 0
    while True:
        cfg = load_settings()
        token = cfg.get('tg_token', TELEGRAM_TOKEN)
        if not token:
            time.sleep(5)
            continue
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        mt5_safeguard()
        try:
            res = requests.get(f"{url}?offset={last_update_id}&timeout=10", timeout=15)
            if res.status_code == 200:
                data = res.json()
                for update in data.get("result", []):
                    last_update_id = update["update_id"] + 1
                    message = update.get("message", {})
                    if str(message.get("chat", {}).get("id", "")) != cfg.get("tg_chat", TELEGRAM_CHAT_ID): continue
                    text = message.get("text", "").upper().strip()
                    if text == "FLAT":
                        send_telegram("🛑 <b>DIRECTIVE RECEIVED:</b> Flattening entire portfolio...", "red")
                        if cfg.get("route_mt5", True):
                            positions = mt5.positions_get()
                            if positions:
                                for p in positions: close_position_safely(p)
                        if cfg.get("route_nt8", False):
                            threading.Thread(target=forward_close_to_nt8, args=("ALL",), daemon=True).start()
                        send_telegram("✅ <b>Portfolio is FLAT.</b>", "blue")
                    elif text == "PAUSE":
                        SYSTEM_PAUSED = True
                        send_telegram("⏸️ <b>SYSTEM PAUSED:</b> Ignoring all incoming signals.", "red")
                    elif text == "RESUME":
                        SYSTEM_PAUSED = False
                        send_telegram("▶️ <b>SYSTEM RESUMED:</b> Vault is armed.", "blue")
                    elif text == "REPORT":
                        generate_and_send_eod_report()
                    elif text in ["GOOD", "BAD"]:
                        log_rlhf_feedback(text)
        except Exception: pass
        time.sleep(1.5)

# =============================================================================
# 🧠 AI REGIME DETECTION & LIVE SCANNER
# =============================================================================
def detect_market_regime(symbol):
    global LATEST_REGIME
    if not ML_ACTIVE: return "UNKNOWN"
    mt5_safeguard()
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 1000)
    if rates is None or len(rates) < 100: return "UNKNOWN"

    df = pd.DataFrame(rates)
    df['volatility'] = df['high'] - df['low']
    df['momentum'] = abs(df['close'] - df['open'])
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(df[['volatility', 'momentum']].values)

    kmeans = KMeans(n_clusters=3, random_state=42, n_init=10)
    df['cluster'] = kmeans.fit_predict(X_scaled)
    cluster_means = df.groupby('cluster')[['volatility', 'momentum']].mean()
    cluster_means['score'] = cluster_means['volatility'] + cluster_means['momentum']

    current_cluster = df['cluster'].iloc[-1]
    if current_cluster == cluster_means['score'].idxmin(): return "CHOP"
    elif current_cluster == cluster_means['score'].idxmax(): return "EXTREME"
    else: return "TREND"

def ai_monitoring_worker():
    global LATEST_REGIME, ASSET_HEATMAP
    while True:
        if ML_ACTIVE and mt5.terminal_info() is not None:
            try:
                positions = mt5.positions_get()
                target_symbol = get_tradable_symbol("NAS100")
                if positions and len(positions) > 0:
                    target_symbol = positions[0].symbol

                if target_symbol:
                    regime = detect_market_regime(target_symbol)
                    if regime != "UNKNOWN":
                        LATEST_REGIME = f"{regime} ({target_symbol})"

                for sym in ASSET_HEATMAP.keys():
                    tsym = get_tradable_symbol(sym)
                    if not tsym: continue
                    rates = mt5.copy_rates_from_pos(tsym, mt5.TIMEFRAME_M15, 0, 100)
                    if rates is not None and len(rates) > 10:
                        df = pd.DataFrame(rates)
                        df['vol'] = df['high'] - df['low']
                        df['mom'] = abs(df['close'] - df['open'])
                        recent_mom = df['mom'].tail(5).mean()
                        recent_vol = df['vol'].tail(5).mean()

                        score = min(100, int((recent_mom / (recent_vol + 0.0001)) * 100))
                        if score > 65: color = QUANT_BLUE; reg = "HIGH"
                        elif score > 35: color = "#00FFAA"; reg = "MID"
                        else: color = MUTED_TEXT; reg = "LOW"

                        ASSET_HEATMAP[sym] = {"score": score, "color": color, "reg": reg}
            except Exception: pass
        time.sleep(60)

# =============================================================================
# 🚀 BACKGROUND PERFORMANCE ENGINE (Prevents GUI Freezing)
# =============================================================================
def performance_worker():
    global PERFORMANCE_DATA
    while True:
        if mt5.terminal_info() is not None:
            mt5_safeguard()
            try:
                now = datetime.datetime.now()
                from_date = now - datetime.timedelta(days=365)
                midnight = datetime.datetime(now.year, now.month, now.day)

                deals_today = mt5.history_deals_get(midnight, now)
                today_closed = sum(d.profit + d.commission + d.swap + d.fee for d in deals_today if d.entry in (1, 2)) if deals_today else 0.0

                deals = mt5.history_deals_get(from_date, now)
                if deals:
                    out_deals = [d for d in deals if d.entry in (1, 2)]
                    if out_deals:
                        gross_profit, gross_loss, wins = 0.0, 0.0, 0
                        best, worst = 0.0, 0.0
                        journal_data = []

                        for d in out_deals:
                            net = d.profit + d.commission + d.swap + d.fee
                            if net > 0:
                                gross_profit += net
                                wins += 1
                                if net > best: best = net
                            else:
                                gross_loss += abs(net)
                                if net < worst: worst = net

                            dt_str = datetime.datetime.fromtimestamp(d.time).strftime('%m-%d %H:%M')
                            action = "CLOSED LONG" if d.type == 1 else "CLOSED SHORT"
                            journal_data.append((dt_str, d.symbol, action, d.volume, net))

                        total = len(out_deals)
                        wr = (wins / total) * 100
                        pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
                        net_pnl = gross_profit - gross_loss

                        journal_data.sort(key=lambda x: x[0], reverse=True)

                        PERFORMANCE_DATA["net_pnl"] = net_pnl
                        PERFORMANCE_DATA["win_rate"] = wr
                        PERFORMANCE_DATA["profit_factor"] = pf
                        PERFORMANCE_DATA["total_trades"] = total
                        PERFORMANCE_DATA["best"] = best
                        PERFORMANCE_DATA["worst"] = worst
                        PERFORMANCE_DATA["journal"] = journal_data[:50]
                PERFORMANCE_DATA["today_closed_pnl"] = today_closed
            except Exception: pass
        time.sleep(10)

# =============================================================================
# 🧬 WALK-FORWARD EVOLUTION ENGINE
# =============================================================================
def execute_walk_forward_tuning():
    mt5_safeguard()
    now = datetime.datetime.now()
    deals = mt5.history_deals_get(now - datetime.timedelta(days=14), now)
    if not deals: return
    out_deals = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT)]
    asset_perf = {}
    for d in out_deals:
        sym, net = d.symbol, d.profit + d.commission + d.swap + d.fee
        if sym not in asset_perf: asset_perf[sym] = {"wins": 0, "losses": 0, "pnl": 0.0}
        asset_perf[sym]["pnl"] += net
        if net > 0: asset_perf[sym]["wins"] += 1
        else: asset_perf[sym]["losses"] += 1

    memory = load_oracle_memory()
    memory["probation"], memory["momentum"], memory["blacklist"] = [], [], []
    for sym, stats in asset_perf.items():
        total = stats["wins"] + stats["losses"]
        if total < 3: continue
        wr = (stats["wins"] / total) * 100
        if wr < 30.0 and stats["pnl"] < 0: memory["blacklist"].append(sym)
        elif wr < 45.0 and stats["pnl"] < 0: memory["probation"].append(sym)
        elif wr >= 60.0 and stats["pnl"] > 0: memory["momentum"].append(sym)
    save_oracle_memory(memory)
    send_telegram("🧬 <b>ORACLE EVOLUTION:</b> Brain parameters saved for Walk-Forward analysis.", "blue")

def evolution_worker():
    mt5_safeguard()
    tuned_this_week = False
    while True:
        mt5_safeguard()
        try:
            now = datetime.datetime.now()
            if now.weekday() == 6: tuned_this_week = False
            if now.weekday() == 4 and now.hour == 17 and now.minute == 5 and not tuned_this_week:
                execute_walk_forward_tuning()
                tuned_this_week = True
        except Exception: pass
        time.sleep(60)

# =============================================================================
# 👁️‍🗨️ OMNISCIENT WATCHER & EOD
# =============================================================================
def mt5_account_watcher():
    mt5_safeguard()
    active_tickets = set()
    while mt5.terminal_info() is None:
        mt5_safeguard(); time.sleep(1)
    if mt5.positions_get(): active_tickets = {p.ticket for p in mt5.positions_get()}

    while True:
        mt5_safeguard()
        time.sleep(1.0)
        positions = mt5.positions_get()
        if positions is None: continue

        current_tickets = {p.ticket for p in positions}
        closed_tickets = active_tickets - current_tickets

        for ticket in closed_tickets:
            deals = mt5.history_deals_get(position=ticket)
            if deals:
                in_deal = next((d for d in deals if d.entry == 0), None)
                out_deals = [d for d in deals if d.entry in (1, 2)]

                if in_deal and out_deals:
                    final_deal = out_deals[-1]
                    symbol = final_deal.symbol
                    lot = final_deal.volume
                    pos_type = "LONG" if in_deal.type == 0 else "SHORT"
                    entry_prc = in_deal.price
                    exit_prc = final_deal.price

                    time_diff = final_deal.time - in_deal.time
                    mins, secs = divmod(time_diff, 60)
                    hrs, mins = divmod(mins, 60)
                    dur_str = f"{int(hrs)}h {int(mins)}m" if hrs > 0 else f"{int(mins)}m {int(secs)}s"

                    gross = sum(d.profit for d in out_deals)
                    fees = sum(d.commission + d.swap + d.fee for d in out_deals)
                    net = gross + fees

                    icon = "🟢" if net > 0 else ("🔴" if net < 0 else "⚪")

                    msg = (f"🏦 <b>AUTOPSY | #{symbol}</b>\n"
                           f"---------------------------\n"
                           f"<b>TYPE:</b> {pos_type} ({lot} Lots)\n"
                           f"<b>ENT/EXT:</b> {entry_prc} / {exit_prc}\n"
                           f"<b>DURATION:</b> {dur_str}\n"
                           f"<b>FEE/SWAP:</b> ${fees:.2f}\n"
                           f"---------------------------\n"
                           f"<b>NET PNL:</b> {icon} <b>{'+' if net > 0 else ''}${net:.2f}</b>")

                    send_telegram(msg, "white")

        active_tickets = current_tickets

def generate_and_send_eod_report():
    mt5_safeguard()
    now = datetime.datetime.now()
    midnight = datetime.datetime(now.year, now.month, now.day)

    deals = mt5.history_deals_get(midnight, now)
    if deals is None: return

    out_deals = [d for d in deals if d.entry in (1, 2)]
    if not out_deals:
        send_telegram(f"📊 <b>EOD DOSSIER | {now.strftime('%m-%d')}</b>\n---------------------------\n<b>TRADES:</b> 0\n<b>STATUS:</b> FLAT ARCHIVE.", "white")
        return

    total_gross = sum(d.profit for d in out_deals)
    total_fees = sum(d.commission + d.swap + d.fee for d in out_deals)
    net_pnl = total_gross + total_fees

    wins = len([d for d in out_deals if (d.profit + d.commission + d.swap + d.fee) > 0])
    losses = len(out_deals) - wins
    win_rate = (wins / len(out_deals)) * 100 if out_deals else 0.0

    icon = "🟢" if net_pnl > 0 else ("🔴" if net_pnl < 0 else "⚪")

    msg = (f"📊 <b>EOD DOSSIER | {now.strftime('%m-%d')}</b>\n"
           f"---------------------------\n"
           f"<b>TRADES:</b> {len(out_deals)}\n"
           f"<b>WIN RATE:</b> {win_rate:.1f}% ({wins}W / {losses}L)\n"
           f"<b>GROSS:</b> ${total_gross:.2f}\n"
           f"<b>FEES:</b> ${total_fees:.2f}\n"
           f"---------------------------\n"
           f"<b>NET PNL:</b> {icon} <b>{'+' if net_pnl > 0 else ''}${net_pnl:.2f}</b>")

    send_telegram(msg, "blue")

def eod_report_worker():
    mt5_safeguard()
    report_sent_today = False
    while True:
        mt5_safeguard()
        try:
            now = datetime.datetime.now()
            if now.hour == 0: report_sent_today = False
            if now.hour == 17 and now.minute == 0 and not report_sent_today:
                generate_and_send_eod_report(); report_sent_today = True
        except Exception: pass
        time.sleep(30)

# =============================================================================
# 🛡️ QUANT ENGINES & EXECUTION
# =============================================================================
def get_kelly_multiplier():
    now = datetime.datetime.now()
    deals = mt5.history_deals_get(now - datetime.timedelta(days=30), now)
    if not deals or len(deals) < 10: return 1.0
    profits = [d.profit for d in deals if d.profit != 0]
    if not profits: return 1.0
    wins, losses = [p for p in profits if p > 0], [abs(p) for p in profits if p < 0]
    if not losses or not wins: return 1.0
    win_rate = len(wins) / len(profits)
    reward_ratio = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    kelly_pct = win_rate - ((1 - win_rate) / reward_ratio)
    return max(0.25, min(kelly_pct * 0.5, 2.0))

def apply_covariance_guard(new_symbol, new_action, lot_size):
    open_positions = mt5.positions_get()
    if not open_positions or "USD" not in new_symbol: return lot_size
    usd_exposure = sum(
        1 if (pos.symbol.startswith("USD") and pos.type == 0) or (pos.symbol.endswith("USD") and pos.type == 1) else (-1 if (pos.symbol.startswith("USD") and pos.type == 1) or (pos.symbol.endswith("USD") and pos.type == 0) else 0)
        for pos in open_positions
    )
    new_usd_dir = 1 if (new_action == "buy" and new_symbol.startswith("USD")) or (new_action == "sell" and new_symbol.endswith("USD")) else -1
    if usd_exposure != 0 and new_usd_dir != 0 and (usd_exposure * new_usd_dir) > 0:
        return max(round(lot_size / 2, 2), mt5.symbol_info(new_symbol).volume_min)
    return lot_size

def calculate_dynamic_lot(symbol, entry_price, sl_price, action, regime):
    cfg = load_settings()
    sym_info = mt5.symbol_info(symbol)
    if sym_info is None:
        send_telegram(f"🛡️ <b>ORACLE VETO:</b> #{symbol}\n<b>Reason:</b> Asset missing in MT5.\n<b>Status:</b> STAND DOWN.", "red")
        return 0.0

    if sl_price == 0.0:
        send_telegram(f"🛡️ <b>ORACLE VETO:</b> #{symbol}\n<b>Reason:</b> SL distance unquantifiable.\n<b>Status:</b> STAND DOWN.", "red")
        return 0.0

    if "CHOP" in regime and not cfg.get("trade_chop", True):
        send_telegram(f"🛡️ <b>ORACLE VETO:</b> #{symbol}\n<b>Reason:</b> CHOP configuration restriction.\n<b>Status:</b> STAND DOWN.", "red")
        return 0.0
    if "TREND" in regime and not cfg.get("trade_trend", True):
        send_telegram(f"🛡️ <b>ORACLE VETO:</b> #{symbol}\n<b>Reason:</b> TREND configuration restriction.\n<b>Status:</b> STAND DOWN.", "red")
        return 0.0
    if "EXTREME" in regime and not cfg.get("trade_extreme", True):
        send_telegram(f"🛡️ <b>ORACLE VETO:</b> #{symbol}\n<b>Reason:</b> EXTREME configuration restriction.\n<b>Status:</b> STAND DOWN.", "red")
        return 0.0

    active_risk = float(cfg["risk_pct"])
    account = mt5.account_info()
    current_equity = account.equity

    memory = load_oracle_memory()
    high_watermark = memory.get("high_watermark", current_equity)
    if current_equity > high_watermark:
        memory["high_watermark"] = current_equity
        save_oracle_memory(memory)
        high_watermark = current_equity

    drawdown_pct = ((high_watermark - current_equity) / high_watermark) * 100
    if drawdown_pct <= 1.0: active_risk *= 1.5
    elif drawdown_pct >= 10.0: active_risk *= 0.25
    elif drawdown_pct >= 5.0: active_risk *= 0.5

    if "CHOP" in regime: active_risk *= 0.5
    if symbol in memory.get("blacklist", []):
        send_telegram(f"🛡️ <b>ORACLE VETO:</b> #{symbol}\n<b>Reason:</b> Asset Blacklisted via Evolution Cycle.\n<b>Status:</b> STAND DOWN.", "red")
        return 0.0
    if symbol in memory.get("probation", []): active_risk *= 0.5
    if symbol in memory.get("momentum", []): active_risk *= 1.5
    if cfg.get("kelly_active", False): active_risk *= get_kelly_multiplier()

    sl_ticks = abs(entry_price - sl_price) / sym_info.trade_tick_size
    if sl_ticks <= 0 or sym_info.trade_tick_value <= 0:
        send_telegram(f"🛡️ <b>ORACLE VETO:</b> #{symbol}\n<b>Reason:</b> Calculation anomaly on ticks.\n<b>Status:</b> STAND DOWN.", "red")
        return 0.0

    calculated_lot = (current_equity * (active_risk / 100.0)) / (sl_ticks * sym_info.trade_tick_value)
    final_lot = round(calculated_lot / sym_info.volume_step) * sym_info.volume_step

    if final_lot < sym_info.volume_min: final_lot = sym_info.volume_min

    return apply_covariance_guard(symbol, action, final_lot) if cfg.get("covar_guard", False) else round(final_lot, 2)

def close_position_safely(position, volume_override=None):
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None: return False
    req = {
        "action": mt5.TRADE_ACTION_DEAL, "position": position.ticket, "symbol": position.symbol,
        "volume": float(volume_override or position.volume), "type": mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
        "price": float(tick.bid if position.type == mt5.POSITION_TYPE_BUY else tick.ask),
        "deviation": 20, "magic": position.magic, "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return mt5.order_send(req) is not None and mt5.order_send(req).retcode == mt5.TRADE_RETCODE_DONE

def close_all_symbol_positions(symbol):
    mt5_safeguard()
    if mt5.positions_get(symbol=symbol): [close_position_safely(pos) for pos in mt5.positions_get(symbol=symbol)]

def execute_partial_close_mt5(symbol, pct=0.5):
    mt5_safeguard()
    positions = mt5.positions_get(symbol=symbol)
    if not positions: return
    sym_info = mt5.symbol_info(symbol)
    if not sym_info: return

    for pos in positions:
        target_vol = pos.volume * pct
        target_vol = round(target_vol / sym_info.volume_step) * sym_info.volume_step
        if target_vol >= sym_info.volume_min:
            close_position_safely(pos, volume_override=target_vol)
            msg = (f"✂️ <b>ORACLE SCALEOUT</b>\n"
                   f"<b>Asset:</b> #{symbol}\n"
                   f"<b>Action:</b> PARTIAL CLOSE ({int(pct*100)}%)\n"
                   f"<b>Closed:</b> <code>{target_vol}</code> Lots\n"
                   f"<i>Securing realized gains.</i>")
            send_telegram(msg, "blue")

def execute_anti_hedge_sweep(symbol, new_trade_direction):
    mt5_safeguard()
    open_positions = mt5.positions_get(symbol=symbol)
    if not open_positions: return
    for pos in open_positions:
        if (new_trade_direction == "buy" and pos.type == mt5.POSITION_TYPE_SELL) or (new_trade_direction == "sell" and pos.type == mt5.POSITION_TYPE_BUY):
            close_position_safely(pos)

def open_market_order(symbol, action, lot_size, sl_price, tp_price, regime="UNKNOWN", lot_multiplier=1.0):
    global LAST_TRADE_DATA
    cfg = load_settings()
    tick = mt5.symbol_info_tick(symbol)
    if not tick: return
    base_type, limit_type = (mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_BUY_LIMIT) if action == 'buy' else (mt5.ORDER_TYPE_SELL, mt5.ORDER_TYPE_SELL_LIMIT)
    cur_price = tick.ask if action == 'buy' else tick.bid

    def fire(vol, prc, o_type):
        return mt5.order_send({"action": mt5.TRADE_ACTION_PENDING if o_type in [mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_SELL_LIMIT] else mt5.TRADE_ACTION_DEAL,
                               "symbol": symbol, "volume": float(vol), "type": o_type, "price": float(prc), "sl": float(sl_price), "tp": float(tp_price),
                               "deviation": 20, "magic": 999999, "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC})

    if not cfg.get("grid_active", False) or lot_size < (mt5.symbol_info(symbol).volume_min * 3):
        res = fire(lot_size, cur_price, base_type)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            msg = (f"⚡ <b>ORACLE ENTRY | #{symbol}</b>\n"
                   f"---------------------------\n"
                   f"<b>DIR:</b> {action.upper()}\n"
                   f"<b>PRICE:</b> {cur_price}\n"
                   f"<b>SL:</b> {sl_price}\n"
                   f"<b>TP:</b> {tp_price}\n"
                   f"<b>LOT:</b> {lot_size} ({lot_multiplier}x)\n"
                   f"---------------------------\n"
                   f"<b>REGIME:</b> {regime}")
            send_telegram(msg, "blue")
            LAST_TRADE_DATA = {"symbol": symbol, "action": action, "lot": lot_size}
        else:
            err = res.retcode if res else "Unknown"
            send_telegram(f"⚠️ <b>MT5 REJECTION:</b> {action.upper()} #{symbol}. Retcode: {err}", "red")
        return

    dist = sl_price - cur_price
    fire(round(lot_size / 3, 2), cur_price, base_type)
    fire(round(lot_size / 3, 2), cur_price + (dist * 0.33), limit_type)
    fire(round(lot_size / 3, 2), cur_price + (dist * 0.66), limit_type)

    msg = (f"🕸️ <b>ORACLE SNIPER GRID</b>\n"
           f"---------------------------\n"
           f"<b>Asset:</b> #{symbol}\n"
           f"<b>Action:</b> {action.upper()} (3-Tiers)\n"
           f"<b>Base Entry:</b> <code>{cur_price}</code>\n"
           f"<b>LOT ARRAY:</b> {round(lot_size/3, 2)}x3 ({lot_multiplier}x)\n"
           f"---------------------------\n"
           f"<b>REGIME:</b> {regime}")
    send_telegram(msg, "blue")
    LAST_TRADE_DATA = {"symbol": symbol, "action": action, "lot": lot_size}

# =============================================================================
# 🥷 NINJATRADER 8 DROP-FOLDER RELAY
# =============================================================================
def forward_to_nt8(symbol, action, lot_size, sl_price, tp_price, tv_price):
    cfg = load_settings()
    nt8_path = cfg.get("nt8_incoming_path", "").strip()

    if not nt8_path or not os.path.exists(nt8_path) or not cfg.get("route_nt8", False): return

    nt8_symbol = symbol.replace("NAS100", "NQ").replace("US100", "NQ").replace("US30", "YM").replace("XAUUSD", "GC")
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"ORACLE_{action.upper()}_{nt8_symbol}_{timestamp}.txt"
    filepath = os.path.join(nt8_path, filename)

    payload = f"ACTION={action.upper()}\nSYMBOL={nt8_symbol}\nQTY={lot_size}\nSL={sl_price}\nTP={tp_price}\nTIMESTAMP={timestamp}"

    try:
        with open(filepath, "w") as f: f.write(payload)
        msg = (f"🥷 <b>NT8 RELAY</b>\n"
               f"<b>Asset:</b> #{nt8_symbol}\n"
               f"<b>Action:</b> {action.upper()}\n"
               f"<b>Signal Price:</b> <code>{tv_price}</code>\n"
               f"<b>Volume:</b> <code>{lot_size}</code>")
        send_telegram(msg, "blue")
    except Exception as e: log_to_gui(f"⚠️ NT8 RELAY ERROR: {str(e)}", "red")

def forward_close_to_nt8(symbol):
    cfg = load_settings()
    nt8_path = cfg.get("nt8_incoming_path", "").strip()
    if not nt8_path or not os.path.exists(nt8_path) or not cfg.get("route_nt8", False): return

    nt8_symbol = symbol.replace("NAS100", "NQ").replace("US100", "NQ").replace("US30", "YM").replace("XAUUSD", "GC") if symbol != "ALL" else "ALL"
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"ORACLE_CLOSE_{nt8_symbol}_{timestamp}.txt"
    filepath = os.path.join(nt8_path, filename)

    payload = f"ACTION=CLOSE\nSYMBOL={nt8_symbol}\nTIMESTAMP={timestamp}"
    try:
        with open(filepath, "w") as f: f.write(payload)
        log_to_gui(f"🥷 NT8 Close Signal Dropped: {nt8_symbol}", "blue")
    except Exception: pass

def forward_partial_to_nt8(symbol, pct):
    cfg = load_settings()
    nt8_path = cfg.get("nt8_incoming_path", "").strip()
    if not nt8_path or not os.path.exists(nt8_path) or not cfg.get("route_nt8", False): return

    nt8_symbol = symbol.replace("NAS100", "NQ").replace("US100", "NQ").replace("US30", "YM").replace("XAUUSD", "GC")
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"ORACLE_PARTIAL_{nt8_symbol}_{timestamp}.txt"
    filepath = os.path.join(nt8_path, filename)

    payload = f"ACTION=PARTIAL\nSYMBOL={nt8_symbol}\nPCT={pct}\nTIMESTAMP={timestamp}"
    try:
        with open(filepath, "w") as f: f.write(payload)
        log_to_gui(f"🥷 NT8 Partial Signal Dropped: {nt8_symbol}", "blue")
    except Exception: pass

# =============================================================================
# 🌐 ASYNC WEBHOOK ROUTER (PARSING PIPELINE STRINGS)
# =============================================================================
def process_webhook_payload(data):
    mt5_safeguard()
    global LATEST_REGIME
    try:
        cfg = load_settings()
        symbol = get_tradable_symbol(data.get("symbol"))
        action = data.get("action")
        tv_price = float(data.get("tv_price", 0.0))

        # HURST INTERACTION MODIFIER
        lot_multiplier = float(data.get("lot_mult", 1.0))

        if mt5.terminal_info() is None and cfg.get("route_mt5", True):
            send_telegram("⚠️ <b>EXECUTION FAILED:</b> Core Interface Disconnected.", "red")
            return

        if action == "partial":
            pct = data.get("pct", 50) / 100.0
            if cfg.get("route_mt5", True): execute_partial_close_mt5(symbol, pct)
            if cfg.get("route_nt8", False): forward_partial_to_nt8(symbol, pct)

        elif action == "close":
            if cfg.get("route_mt5", True): close_all_symbol_positions(symbol)
            if cfg.get("route_nt8", False): forward_close_to_nt8(symbol)

        elif action in ["buy", "sell"]:
            sl_price, tp_price = float(data.get("sl_price", 0.0)), float(data.get("tp_price", 0.0))

            regime = detect_market_regime(symbol)
            LATEST_REGIME = f"{regime} ({symbol})"

            base_lot = calculate_dynamic_lot(symbol, tv_price, sl_price, action, regime)
            target_lot_mt5 = round(base_lot * lot_multiplier, 2)

            if target_lot_mt5 > 0.0 or not cfg.get("route_mt5", True):
                if cfg.get("anti_hedge", True) and cfg.get("route_mt5", True): execute_anti_hedge_sweep(symbol, action)
                if cfg.get("route_mt5", True):
                    open_market_order(symbol, action, target_lot_mt5, sl_price, tp_price, regime, lot_multiplier)
                if cfg.get("route_nt8", False):
                    nt8_qty = max(1, int(target_lot_mt5 * 10)) if target_lot_mt5 > 0 else 1
                    forward_to_nt8(symbol, action, nt8_qty, sl_price, tp_price, tv_price)
    except Exception as e:
        log_to_gui(f"⚠️ ASYNC PROCESS ERROR: {str(e)}", "red")
        send_telegram(f"⚠️ <b>ERROR:</b> {str(e)}", "red")

@app.route('/webhook', methods=['POST'])
def webhook():
    global SYSTEM_PAUSED
    if SYSTEM_PAUSED: return jsonify({"status": "paused"}), 200
    try:
        raw_data = request.get_data(as_text=True)
        try: data = json.loads(raw_data)
        except json.JSONDecodeError: return jsonify({"status": "ignored"}), 400

        cfg = load_settings()
        configured_pass = cfg.get("wh_pass", WEBHOOK_PASSPHRASE)
        # Fail closed: if no passphrase is configured, reject everything rather
        # than accepting unauthenticated signals.
        if not configured_pass:
            log_to_gui("⚠️ WEBHOOK REJECTED: No passphrase configured.", "red")
            return jsonify({"error": "Webhook passphrase not configured"}), 503
        if data.get("key") != configured_pass: return jsonify({"error": "Unauthorized"}), 401

        threading.Thread(target=process_webhook_payload, args=(data,), daemon=True).start()
        return jsonify({"status": "success", "message": "Signal intercepted"}), 200
    except Exception as e:
        log_to_gui(f"⚠️ WEBHOOK ERROR: {str(e)}", "red")
        return jsonify({"error": str(e)}), 500

# =============================================================================
# 🎨 CUSTOMTKINTER GUI & DASHBOARD (MAIN UI ARCHITECTURE)
# =============================================================================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

class OracleDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ORACLE AI: PROJECT CITADEL")
        self.geometry("1100x750")
        self.minsize(1100, 750)
        self.resizable(True, True)
        self.configure(fg_color=BG_COLOR)

        self.last_state = {
            "broker": "", "login": "", "leverage": "", "margin": "", "today_pnl_str": "",
            "regime": "", "usd": "", "lock": "",
            "mom": "", "risk_conf": "", "header_risk": "",
            "exposure_val": -1.0, "managed_accs": "", "total_pos_count": "",
            "today_closed_pnl": 0.0, "donut_data": "{}"
        }

        self.accounts_data = []
        self.pos_pool = []
        self.journal_pool = []
        self.acc_pool = []
        self.heatmap_labels = {}

        try:
            import pyi_splash
            pyi_splash.close()
        except Exception: pass

        self.withdraw()
        self.show_splash_screen()

    def show_splash_screen(self):
        self.splash = ctk.CTkToplevel(self)
        self.splash.geometry("550x380")
        self.splash.configure(fg_color=PANEL_COLOR)
        self.splash.overrideredirect(True)
        x = (self.winfo_screenwidth() / 2) - (550 / 2)
        y = (self.winfo_screenheight() / 2) - (380 / 2)
        self.splash.geometry(f"+{int(x)}+{int(y)}")

        ascii_art = """
 ██████╗ ██████╗  █████╗  ██████╗██╗     ███████╗
██╔═══██╗██╔══██╗██╔══██╗██╔════╝██║     ██╔════╝
██║   ██║██████╔╝███████║██║     ██║     █████╗
██║   ██║██╔══██╗██╔══██║██║     ██║     ██╔══╝
╚██████╔╝██║  ██║██║  ██║╚██████╗███████╗███████╗
 ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚══════╝╚══════╝
        """
        lbl = ctk.CTkLabel(self.splash, text=ascii_art, font=("Courier New", 14, "bold"), text_color=QUANT_BLUE, justify="center")
        lbl.pack(pady=(30, 15))

        ctk.CTkLabel(self.splash, text="PROJECT CITADEL v24.7", font=("Arial", 12, "bold"), text_color=TEXT_WHITE).pack(pady=(0, 15))
        self.progress = ctk.CTkProgressBar(self.splash, width=350, progress_color=QUANT_BLUE, fg_color=BG_COLOR)
        self.progress.pack()
        self.progress.set(0)
        self.status_label = ctk.CTkLabel(self.splash, text="Loading Core Assets...", font=("Courier New", 11), text_color=MUTED_TEXT)
        self.status_label.pack(pady=10)

        threading.Thread(target=self.initialize_backend_systems).start()

    def initialize_backend_systems(self):
        self.update_splash(0.5, "Initializing Omni-Router Bridge & ML Cores...")
        mt5.initialize()
        threading.Thread(target=background_ml_loader, daemon=True).start()

        self.update_splash(1.0, "AWAITING AUTHENTICATION...")
        time.sleep(0.4)

        self.after(0, self.splash.destroy)
        self.after(0, self.deiconify)
        self.after(0, self.build_login_screen)

    def update_splash(self, val, text):
        self.progress.set(val)
        self.status_label.configure(text=text)
        time.sleep(0.3)

    def start_background_workers(self):
        threading.Thread(target=ai_monitoring_worker, daemon=True).start()
        threading.Thread(target=performance_worker, daemon=True).start()
        threading.Thread(target=mt5_account_watcher, daemon=True).start()
        threading.Thread(target=eod_report_worker, daemon=True).start()
        threading.Thread(target=evolution_worker, daemon=True).start()
        threading.Thread(target=telegram_listener_worker, daemon=True).start()
        threading.Thread(target=lambda: app.run(host='0.0.0.0', port=80, debug=False, use_reloader=False), daemon=True).start()

    def build_login_screen(self):
        self.login_frame = ctk.CTkFrame(self, width=400, height=450, fg_color=PANEL_COLOR, corner_radius=15)
        self.login_frame.place(relx=0.5, rely=0.5, anchor="center")

        self.login_title = ctk.CTkLabel(self.login_frame, text="ORACLE AI", font=("Courier New", 28, "bold"), text_color=QUANT_BLUE)
        self.login_title.pack(pady=(40, 10))

        self.login_sub = ctk.CTkLabel(self.login_frame, text="CITADEL PROTOCOL GATEWAY", font=("Arial", 12, "bold"), text_color=MUTED_TEXT)
        self.login_sub.pack(pady=(0, 30))

        self.inp_user = ctk.CTkEntry(self.login_frame, width=250, placeholder_text="Username")
        self.inp_user.pack(pady=10)

        self.inp_pass = ctk.CTkEntry(self.login_frame, width=250, placeholder_text="Password", show="*")
        self.inp_pass.pack(pady=10)

        self.err_lbl = ctk.CTkLabel(self.login_frame, text="", text_color=ALERT_RED, font=("Arial", 10))
        self.err_lbl.pack(pady=5)

        self.btn_action = ctk.CTkButton(self.login_frame, text="AUTHENTICATE", fg_color=QUANT_BLUE, text_color="black", font=("Arial", 14, "bold"), command=self.attempt_login)
        self.btn_action.pack(pady=(10, 10))

        self.btn_switch = ctk.CTkButton(self.login_frame, text="CREATE CLEARANCE", fg_color=BG_COLOR, border_color=QUANT_BLUE, border_width=1, command=self.switch_to_create)
        self.btn_switch.pack(pady=(5, 20))

        self.mode = "login"

    def switch_to_create(self):
        if self.mode == "login":
            self.mode = "create"
            self.btn_action.configure(text="REGISTER VAULT")
            self.btn_switch.configure(text="BACK TO LOGIN")
            self.login_sub.configure(text="NEW PROTOCOL INITIALIZATION")
            self.err_lbl.configure(text="")
        else:
            self.mode = "login"
            self.btn_action.configure(text="AUTHENTICATE")
            self.btn_switch.configure(text="CREATE CLEARANCE")
            self.login_sub.configure(text="CITADEL PROTOCOL GATEWAY")
            self.err_lbl.configure(text="")

    def attempt_login(self):
        user = self.inp_user.get().strip()
        pwd = self.inp_pass.get().strip()

        if not user or not pwd:
            self.err_lbl.configure(text="Please enter credentials.", text_color=ALERT_RED)
            return

        users = load_users()

        if self.mode == "create":
            if user in users: self.err_lbl.configure(text="Username already exists.", text_color=ALERT_RED)
            else:
                users[user] = pwd
                save_users(users)
                self.err_lbl.configure(text="Vault registered. Please login.", text_color=QUANT_BLUE)
                self.switch_to_create()
        else:
            if user in users and users[user] == pwd:
                global CURRENT_USER
                CURRENT_USER = user
                migrate_legacy_files(user)
                self.login_frame.destroy()

                self.accounts_data = load_accounts()
                self.start_background_workers()
                self.build_main_interface()

                boot_msg = (f"🟢 <b>SYSTEM ONLINE</b>\n"
                            f"<b>User:</b> {CURRENT_USER}\n"
                            f"<b>Module:</b> Oracle AI: Project Citadel v24.7\n"
                            f"<i>Awaiting webhook signals.</i>")
                send_telegram(boot_msg, "blue")
            else:
                self.err_lbl.configure(text="ACCESS DENIED. Invalid Credentials.", text_color=ALERT_RED)

    def trigger_killswitch(self):
        global SYSTEM_PAUSED
        SYSTEM_PAUSED = True
        log_to_gui("🛑 KILLSWITCH ACTIVATED: Liquidating Portfolio & Locking Vault...", "red")
        send_telegram("🛑 <b>KILLSWITCH TRIPPED FROM DESKTOP TERMINAL</b>\n<i>All incoming signals are now locked out.</i>", "red")
        cfg = load_settings()
        if cfg.get("route_mt5", True):
            mt5_safeguard()
            positions = mt5.positions_get()
            if positions:
                for p in positions: close_position_safely(p)
        if cfg.get("route_nt8", False):
            threading.Thread(target=forward_close_to_nt8, args=("ALL",), daemon=True).start()

    def open_expanded_console(self):
        if hasattr(self, 'expanded_win') and self.expanded_win.winfo_exists():
            self.expanded_win.focus()
            return

        self.expanded_win = ctk.CTkToplevel(self)
        self.expanded_win.title("ORACLE AI: FULL TELEMETRY LOG")
        self.expanded_win.geometry("900x600")
        self.expanded_win.configure(fg_color=BG_COLOR)

        self.expanded_console = ctk.CTkTextbox(self.expanded_win, fg_color=PANEL_COLOR, text_color=TEXT_WHITE, font=("Courier New", 12))
        self.expanded_console.pack(fill="both", expand=True, padx=20, pady=20)
        self.expanded_console.tag_config("blue", foreground=QUANT_BLUE)
        self.expanded_console.tag_config("red", foreground=ALERT_RED)
        self.expanded_console.insert("end", self.console.get("1.0", "end"))
        self.expanded_console.see("end")

    def build_main_interface(self):
        header_frame = ctk.CTkFrame(self, height=70, fg_color=PANEL_COLOR, corner_radius=0)
        header_frame.pack(fill="x", side="top", pady=(0, 10))
        ctk.CTkLabel(header_frame, text=" ORACLE AI", font=("Courier New", 24, "bold"), text_color=QUANT_BLUE).pack(side="left", padx=20, pady=10)

        self.header_risk_lbl = ctk.CTkLabel(header_frame, text="PEAK: $0.00  |  DD: 0.0%  |  MULTI: 1.0x", font=("Courier New", 14, "bold"), text_color=TEXT_WHITE)
        self.header_risk_lbl.pack(side="right", padx=20, pady=15)

        self.tabview = ctk.CTkTabview(self, fg_color="transparent", text_color=TEXT_WHITE, segmented_button_selected_color=QUANT_BLUE, segmented_button_unselected_color=PANEL_COLOR)
        self.tabview.pack(fill="both", expand=True, padx=20)

        self.tab_accounts = self.tabview.add("Accounts")
        self.tab_cfg = self.tabview.add("Configuration")
        self.tab_perf = self.tabview.add("Performance")

        self.build_accounts_tab()
        self.build_configuration_tab()
        self.build_performance_tab()

        console_frame = ctk.CTkFrame(self, height=180, fg_color=PANEL_COLOR, corner_radius=10)
        console_frame.pack(fill="x", side="bottom", padx=20, pady=15)

        console_header = ctk.CTkFrame(console_frame, fg_color="transparent")
        console_header.pack(fill="x", padx=15, pady=(10, 0))

        ctk.CTkLabel(console_header, text="💻 TERMINAL CONSOLE", font=("Arial", 12, "bold"), text_color=MUTED_TEXT).pack(side="left")
        ctk.CTkButton(console_header, text="🗖 EXPAND", width=80, height=24, fg_color=BG_COLOR, border_color=QUANT_BLUE, border_width=1, font=("Arial", 10, "bold"), command=self.open_expanded_console).pack(side="right")

        self.console = ctk.CTkTextbox(console_frame, height=120, fg_color=BG_COLOR, text_color=TEXT_WHITE, font=("Courier New", 12))
        self.console.pack(fill="x", padx=15, pady=10)
        self.console.tag_config("blue", foreground=QUANT_BLUE)
        self.console.tag_config("red", foreground=ALERT_RED)

        self.process_gui_queue()
        self.refresh_telemetry()
        self.refresh_performance()

    def build_accounts_tab(self):
        left_wing = ctk.CTkFrame(self.tab_accounts, width=280, fg_color=PANEL_COLOR, corner_radius=10)
        left_wing.pack(side="left", fill="y", padx=(0, 10))

        ctk.CTkLabel(left_wing, text="🏦 ORACLE SUMMARY", font=("Arial", 16, "bold"), text_color=TEXT_WHITE).pack(pady=(15, 10))
        self.acc_broker_val = self.create_info_row(left_wing, "Broker:", "Loading...", TEXT_WHITE)
        self.acc_id_val = self.create_info_row(left_wing, "Login ID:", "Loading...", QUANT_BLUE)
        self.acc_lev_val = self.create_info_row(left_wing, "Leverage:", "Loading...", TEXT_WHITE)
        self.acc_margin_val = self.create_info_row(left_wing, "Active Margin:", "Loading...", TEXT_WHITE)
        self.acc_today_pnl_val = self.create_info_row(left_wing, "Today's PnL:", "Loading...", QUANT_BLUE)

        ctk.CTkLabel(left_wing, text="🌐 NETWORK SCOPE", font=("Arial", 12, "bold"), text_color=MUTED_TEXT).pack(pady=(15, 5))
        self.managed_accs_val = self.create_info_row(left_wing, "Managed Vaults:", "Loading...", TEXT_WHITE)
        self.total_pos_val = self.create_info_row(left_wing, "Active Positions:", "Loading...", QUANT_BLUE)

        ctk.CTkLabel(left_wing, text="🧠 AI SUMMARY", font=("Arial", 12, "bold"), text_color=MUTED_TEXT).pack(pady=(15, 5))
        self.regime_val = self.create_info_row(left_wing, "Market Regime:", LATEST_REGIME, QUANT_BLUE)
        self.risk_conf_val = self.create_info_row(left_wing, "Risk Confidence:", "CALCULATING...", QUANT_BLUE)
        self.mom_val = self.create_info_row(left_wing, "Momentum List:", "Awaiting Data", QUANT_BLUE)
        self.usd_covar_val = self.create_info_row(left_wing, "Net USD Exp:", "0 Flat", TEXT_WHITE)
        self.lock_val = self.create_info_row(left_wing, "System Lock:", "DISENGAGED", QUANT_BLUE)

        self.exposure_bar = ctk.CTkProgressBar(left_wing, width=230, progress_color=QUANT_BLUE, fg_color=BG_COLOR)
        self.exposure_bar.pack(pady=(20, 10))
        self.exposure_bar.set(0.0)

        center_wing = ctk.CTkFrame(self.tab_accounts, width=300, fg_color=PANEL_COLOR, corner_radius=10)
        center_wing.pack(side="left", fill="y", padx=(0, 10))

        ctk.CTkLabel(center_wing, text="💼 PORTFOLIO LEDGER", font=("Arial", 16, "bold"), text_color=TEXT_WHITE).pack(pady=(15, 10))
        self.add_acc_btn = ctk.CTkButton(center_wing, text="➕ ADD ACCOUNT", fg_color=QUANT_BLUE, text_color="black", font=("Arial", 12, "bold"), command=self.add_portfolio_account)
        self.add_acc_btn.pack(fill="x", padx=20, pady=(0, 10))

        self.accounts_scroll = ctk.CTkScrollableFrame(center_wing, fg_color="transparent", width=260)
        self.accounts_scroll.pack(fill="both", expand=True, padx=10, pady=5)

        right_wing = ctk.CTkFrame(self.tab_accounts, fg_color=PANEL_COLOR, corner_radius=10)
        right_wing.pack(side="left", fill="both", expand=True)

        self.killswitch = ctk.CTkButton(right_wing, text="🛑 FLATTEN PORTFOLIO & PAUSE", fg_color=ALERT_RED, hover_color="#8B0000", text_color="white", font=("Arial", 14, "bold"), command=self.trigger_killswitch)
        self.killswitch.pack(fill="x", padx=20, pady=(15, 10))

        analytics_frame = ctk.CTkFrame(right_wing, fg_color="transparent", height=130)
        analytics_frame.pack(fill="x", padx=10, pady=5)

        donut_container = ctk.CTkFrame(analytics_frame, fg_color=BG_COLOR, corner_radius=10, width=150)
        donut_container.pack(side="left", fill="y", expand=True, padx=(0, 5))
        ctk.CTkLabel(donut_container, text="Asset Exposure", font=("Arial", 10, "bold"), text_color=MUTED_TEXT).pack(pady=(5,0))

        # 🚀 MATPLOTLIB INTEGRATION
        self.fig = Figure(figsize=(2, 2), dpi=100, facecolor=BG_COLOR)
        self.fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.ax = self.fig.add_subplot(111)
        self.donut_canvas = FigureCanvasTkAgg(self.fig, master=donut_container)
        self.donut_canvas.get_tk_widget().pack(pady=5, fill="both", expand=True)

        heatmap_container = ctk.CTkFrame(analytics_frame, fg_color=BG_COLOR, corner_radius=10)
        heatmap_container.pack(side="right", fill="both", expand=True, padx=(5, 0))
        ctk.CTkLabel(heatmap_container, text="Live Asset Heatmap", font=("Arial", 10, "bold"), text_color=MUTED_TEXT).pack(pady=(5,5))

        for sym in ["NAS100", "US30", "XAUUSD"]:
            row = ctk.CTkFrame(heatmap_container, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(row, text=sym, font=("Arial", 11, "bold"), text_color=TEXT_WHITE).pack(side="left")
            lbl = ctk.CTkLabel(row, text="SCANNING", font=("Courier New", 11, "bold"), text_color=MUTED_TEXT)
            lbl.pack(side="right")
            self.heatmap_labels[sym] = lbl

        ctk.CTkLabel(right_wing, text="📈 ACTIVE TACTICAL POSITIONS", font=("Arial", 14, "bold"), text_color=TEXT_WHITE).pack(pady=(15, 5))
        self.positions_scroll = ctk.CTkScrollableFrame(right_wing, fg_color="transparent")
        self.positions_scroll.pack(fill="both", expand=True, padx=10, pady=5)
        self.no_pos_lbl = ctk.CTkLabel(self.positions_scroll, text="NO TACTICAL POSITIONS OPEN", font=("Arial", 12, "bold"), text_color=MUTED_TEXT)
        self.no_pos_lbl.pack(pady=20)

        self.draw_donut_chart({})

    def draw_donut_chart(self, data_dict):
        self.ax.clear()
        self.ax.axis('equal')

        if not data_dict:
            self.ax.pie([1], colors=[PANEL_COLOR], wedgeprops=dict(width=0.3, edgecolor=MUTED_TEXT, linewidth=1.5))
            self.ax.text(0, 0, "FLAT", ha='center', va='center', fontsize=10, fontweight='bold', color=MUTED_TEXT)
        else:
            labels = list(data_dict.keys())
            sizes = list(data_dict.values())
            colors = [CHART_COLORS[i % len(CHART_COLORS)] for i in range(len(labels))]
            self.ax.pie(sizes, colors=colors, startangle=90, wedgeprops=dict(width=0.3, edgecolor=BG_COLOR, linewidth=1))
            self.ax.text(0, 0, "LIVE", ha='center', va='center', fontsize=10, fontweight='bold', color=TEXT_WHITE)

        self.donut_canvas.draw()

    def add_portfolio_account(self):
        add_win = ctk.CTkToplevel(self)
        add_win.title("Add MT5 Account")
        add_win.geometry("350x420")
        add_win.configure(fg_color=PANEL_COLOR)
        add_win.grab_set()

        ctk.CTkLabel(add_win, text="Link New Sub-Account", font=("Arial", 16, "bold"), text_color=QUANT_BLUE).pack(pady=(20,10))
        ctk.CTkLabel(add_win, text="Login (Account Number):").pack(pady=(10,0))
        inp_login = ctk.CTkEntry(add_win, width=200)
        inp_login.pack(pady=(0,10))

        ctk.CTkLabel(add_win, text="Password:").pack(pady=(5,0))
        inp_pass = ctk.CTkEntry(add_win, width=200, show="*")
        inp_pass.pack(pady=(0,10))

        ctk.CTkLabel(add_win, text="Server:").pack(pady=(5,0))
        inp_server = ctk.CTkEntry(add_win, width=200)
        inp_server.pack(pady=(0,10))

        ctk.CTkLabel(add_win, text="Nickname (Optional):").pack(pady=(5,0))
        inp_nick = ctk.CTkEntry(add_win, width=200)
        inp_nick.pack(pady=(0,10))

        def save_acc():
            try:
                log_val = int(inp_login.get().strip())
                self.accounts_data.append({
                    "login": log_val, "password": inp_pass.get().strip(),
                    "server": inp_server.get().strip(), "nickname": inp_nick.get().strip(), "pnl": 0.0
                })
                save_accounts(self.accounts_data)
                log_to_gui(f"💼 Account {log_val} securely added to ledger.", "blue")
                add_win.destroy()
            except ValueError: log_to_gui("⚠️ Login must be a valid number.", "red")

        ctk.CTkButton(add_win, text="SAVE CREDENTIALS", fg_color=QUANT_BLUE, text_color="black", font=("Arial", 12, "bold"), command=save_acc).pack(pady=15)

    def open_edit_dialog(self, idx):
        if idx < 0 or idx >= len(self.accounts_data): return
        acc = self.accounts_data[idx]
        login_id = acc.get("login", "Unknown")

        edit_win = ctk.CTkToplevel(self)
        edit_win.title(f"Manage {login_id}")
        edit_win.geometry("300x420")
        edit_win.configure(fg_color=PANEL_COLOR)
        edit_win.grab_set()

        ctk.CTkLabel(edit_win, text="Nickname:").pack(pady=(15,0))
        inp_nick = ctk.CTkEntry(edit_win, width=200)
        inp_nick.pack(pady=(0,10))
        if acc.get("nickname"): inp_nick.insert(0, acc["nickname"])

        ctk.CTkLabel(edit_win, text="Password:").pack(pady=(5,0))
        inp_pass = ctk.CTkEntry(edit_win, width=200, show="*")
        inp_pass.pack(pady=(0,10))
        if acc.get("password"): inp_pass.insert(0, acc["password"])

        ctk.CTkLabel(edit_win, text="Server:").pack(pady=(5,0))
        inp_server = ctk.CTkEntry(edit_win, width=200)
        inp_server.pack(pady=(0,15))
        if acc.get("server"): inp_server.insert(0, acc["server"])

        def save_edit():
            acc["nickname"] = inp_nick.get().strip()
            p_val = inp_pass.get().strip()
            if p_val: acc["password"] = p_val
            s_val = inp_server.get().strip()
            if s_val: acc["server"] = s_val
            save_accounts(self.accounts_data)
            log_to_gui(f"✏️ Account {login_id} updated.", "blue")
            edit_win.destroy()

        def set_primary():
            pwd = acc.get("password")
            srv = acc.get("server")
            if not pwd or not srv:
                log_to_gui(f"⚠️ Missing parameters for {login_id}.", "red")
                return
            log_to_gui(f"🔄 Interfacing MT5... Switching to {login_id}.", "blue")
            threading.Thread(target=self.mt5_login_thread, args=(login_id, pwd, srv)).start()
            edit_win.destroy()

        def delete_acc():
            self.accounts_data.pop(idx)
            save_accounts(self.accounts_data)
            self.last_state["managed_accs"] = ""
            log_to_gui(f"🗑️ Account {login_id} purged.", "red")
            edit_win.destroy()

        ctk.CTkButton(edit_win, text="SAVE CONFIGURATION", fg_color=BG_COLOR, border_color=QUANT_BLUE, border_width=1, command=save_edit).pack(pady=5)
        ctk.CTkButton(edit_win, text="SET AS PRIMARY", fg_color=QUANT_BLUE, text_color="black", font=("Arial", 12, "bold"), command=set_primary).pack(pady=5)
        ctk.CTkButton(edit_win, text="DELETE ACCOUNT", fg_color=ALERT_RED, hover_color="#8B0000", command=delete_acc).pack(pady=15)

    def mt5_login_thread(self, login, password, server):
        mt5_safeguard()
        if mt5.login(login=login, password=password, server=server):
            log_to_gui(f"✅ Authenticated into: {login}", "blue")
        else: log_to_gui(f"❌ Handshake failed for {login}.", "red")

    def test_telegram_connection(self):
        token = self.inp_tg_token.get().strip()
        chat_id = self.inp_tg_chat.get().strip()
        if not token or not chat_id:
            log_to_gui("⚠️ Credentials parameters missing.", "red")
            return
        def ping():
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": chat_id, "text": "🟢 <b>ORACLE AI:</b> Channel Linked.", "parse_mode": "HTML"}
            try:
                if requests.post(url, json=payload, timeout=5).status_code == 200: log_to_gui("✅ Tele-Ping Delivered.", "blue")
                else: log_to_gui("❌ API Connection anomaly.", "red")
            except Exception as e: log_to_gui(f"⚠️ Error: {str(e)}", "red")
        threading.Thread(target=ping, daemon=True).start()

    def build_configuration_tab(self):
        cfg = load_settings()
        self.cfg_tabs = ctk.CTkTabview(self.tab_cfg, fg_color=PANEL_COLOR, text_color=TEXT_WHITE, segmented_button_selected_color=QUANT_BLUE, segmented_button_unselected_color=BG_COLOR)
        self.cfg_tabs.pack(fill="both", expand=True, padx=20, pady=(10, 20))

        tab_regime = self.cfg_tabs.add("Regimes")
        tab_entry = self.cfg_tabs.add("Entry")
        tab_risk = self.cfg_tabs.add("Risk")
        tab_network = self.cfg_tabs.add("Network")
        tab_routing = self.cfg_tabs.add("Routing")

        ctk.CTkLabel(tab_regime, text="AI Regime Permissions", font=("Arial", 16, "bold"), text_color=TEXT_WHITE).pack(pady=(10, 20))
        self.sw_trend = ctk.CTkSwitch(tab_regime, text="Allow Trading in TREND Regime", progress_color=QUANT_BLUE)
        self.sw_trend.pack(pady=15, anchor="center")
        if cfg.get("trade_trend", True): self.sw_trend.select()

        self.sw_chop = ctk.CTkSwitch(tab_regime, text="Allow Trading in CHOP Regime", progress_color=QUANT_BLUE)
        self.sw_chop.pack(pady=15, anchor="center")
        if cfg.get("trade_chop", True): self.sw_chop.select()

        self.sw_extreme = ctk.CTkSwitch(tab_regime, text="Allow Trading in EXTREME Regime", progress_color=QUANT_BLUE)
        self.sw_extreme.pack(pady=15, anchor="center")
        if cfg.get("trade_extreme", True): self.sw_extreme.select()

        ctk.CTkLabel(tab_entry, text="Execution Architecture", font=("Arial", 16, "bold"), text_color=TEXT_WHITE).pack(pady=(10, 20))
        self.sw_grid = ctk.CTkSwitch(tab_entry, text="Sniper Grid Mode (3 Limit Tiers)", progress_color=QUANT_BLUE)
        self.sw_grid.pack(pady=15, anchor="center")
        if cfg.get("grid_active", False): self.sw_grid.select()

        self.sw_kelly = ctk.CTkSwitch(tab_entry, text="Kelly Criterion Compounding", progress_color=QUANT_BLUE)
        self.sw_kelly.pack(pady=15, anchor="center")
        if cfg.get("kelly_active", False): self.sw_kelly.select()

        ctk.CTkLabel(tab_risk, text="Capital Preservation", font=("Arial", 16, "bold"), text_color=TEXT_WHITE).pack(pady=(10, 10))
        r_frame = ctk.CTkFrame(tab_risk, fg_color="transparent")
        r_frame.pack(pady=10)

        ctk.CTkLabel(r_frame, text="Base Risk %:").grid(row=0, column=0, padx=20, pady=10, sticky="e")
        self.inp_risk = ctk.CTkEntry(r_frame, width=100)
        self.inp_risk.grid(row=0, column=1, padx=20, pady=10)
        self.inp_risk.insert(0, str(cfg.get("risk_pct", 1.0)))

        ctk.CTkLabel(r_frame, text="Max Port Risk %:").grid(row=1, column=0, padx=20, pady=10, sticky="e")
        self.inp_port = ctk.CTkEntry(r_frame, width=100)
        self.inp_port.grid(row=1, column=1, padx=20, pady=10)
        self.inp_port.insert(0, str(cfg.get("max_port_risk", 15.0)))

        self.sw_covar = ctk.CTkSwitch(tab_risk, text="Covariance Guard (USD Sync)", progress_color=QUANT_BLUE)
        self.sw_covar.pack(pady=10, anchor="center")
        if cfg.get("covar_guard", False): self.sw_covar.select()

        self.sw_hedge = ctk.CTkSwitch(tab_risk, text="Anti-Hedge Sweep", progress_color=QUANT_BLUE)
        self.sw_hedge.pack(pady=10, anchor="center")
        if cfg.get("anti_hedge", True): self.sw_hedge.select()

        ctk.CTkLabel(tab_network, text="C2 Server Parameters", font=("Arial", 16, "bold"), text_color=TEXT_WHITE).pack(pady=(10, 10))
        n_frame = ctk.CTkFrame(tab_network, fg_color="transparent")
        n_frame.pack(pady=10)

        ctk.CTkLabel(n_frame, text="Telegram Token:").grid(row=0, column=0, padx=20, pady=10, sticky="e")
        self.inp_tg_token = ctk.CTkEntry(n_frame, width=300)
        self.inp_tg_token.grid(row=0, column=1, padx=20, pady=10)
        self.inp_tg_token.insert(0, cfg.get("tg_token", TELEGRAM_TOKEN))

        ctk.CTkLabel(n_frame, text="Telegram Chat ID:").grid(row=1, column=0, padx=20, pady=10, sticky="e")
        self.inp_tg_chat = ctk.CTkEntry(n_frame, width=300)
        self.inp_tg_chat.grid(row=1, column=1, padx=20, pady=10)
        self.inp_tg_chat.insert(0, cfg.get("tg_chat", TELEGRAM_CHAT_ID))

        ctk.CTkLabel(n_frame, text="Webhook Pass:").grid(row=2, column=0, padx=20, pady=10, sticky="e")
        self.inp_wh_pass = ctk.CTkEntry(n_frame, width=300)
        self.inp_wh_pass.grid(row=2, column=1, padx=20, pady=10)
        self.inp_wh_pass.insert(0, cfg.get("wh_pass", WEBHOOK_PASSPHRASE))

        self.btn_test_tg = ctk.CTkButton(n_frame, text="📡 SEND TEST PING", fg_color=BG_COLOR, border_color=QUANT_BLUE, border_width=1, command=self.test_telegram_connection)
        self.btn_test_tg.grid(row=3, column=0, columnspan=2, pady=(15, 0))

        ctk.CTkLabel(tab_routing, text="Omni-Router Execution (Prop Firms)", font=("Arial", 16, "bold"), text_color=TEXT_WHITE).pack(pady=(10, 10))
        self.sw_mt5 = ctk.CTkSwitch(tab_routing, text="Route to MT5 Native", progress_color=QUANT_BLUE)
        self.sw_mt5.pack(pady=15, anchor="center")
        if cfg.get("route_mt5", True): self.sw_mt5.select()

        self.sw_nt8 = ctk.CTkSwitch(tab_routing, text="Relay to NinjaTrader 8 Drop-Folder", progress_color=QUANT_BLUE)
        self.sw_nt8.pack(pady=15, anchor="center")
        if cfg.get("route_nt8", False): self.sw_nt8.select()

        ctk.CTkLabel(tab_routing, text="NinjaTrader 8 Incoming Folder Path:").pack(pady=(10, 0))
        self.inp_nt8_path = ctk.CTkEntry(tab_routing, width=350)
        self.inp_nt8_path.pack(pady=(5, 10))
        self.inp_nt8_path.insert(0, cfg.get("nt8_incoming_path", ""))

        save_btn = ctk.CTkButton(self.tab_cfg, text="SAVE & APPLY SETTINGS", fg_color=QUANT_BLUE, text_color="black", font=("Arial", 14, "bold"), command=self.save_gui_settings)
        save_btn.pack(pady=(0, 15))

    def build_performance_tab(self):
        left_wing = ctk.CTkFrame(self.tab_perf, width=300, fg_color=PANEL_COLOR, corner_radius=10)
        left_wing.pack(side="left", fill="y", padx=(0, 10), pady=(0, 10))

        ctk.CTkLabel(left_wing, text="📊 ACCOUNT STATS", font=("Arial", 16, "bold"), text_color=TEXT_WHITE).pack(pady=(15, 10))
        self.perf_net_val = self.create_info_row(left_wing, "Net PnL:", "Loading...", QUANT_BLUE)
        self.perf_wr_val = self.create_info_row(left_wing, "Win Rate:", "Loading...", TEXT_WHITE)
        self.perf_pf_val = self.create_info_row(left_wing, "Profit Factor:", "Loading...", TEXT_WHITE)
        self.perf_trades_val = self.create_info_row(left_wing, "Total Trades:", "Loading...", TEXT_WHITE)

        ctk.CTkLabel(left_wing, text="EXTREMES", font=("Arial", 12, "bold"), text_color=MUTED_TEXT).pack(pady=(25, 5))
        self.perf_best_val = self.create_info_row(left_wing, "Best Trade:", "Loading...", QUANT_BLUE)
        self.perf_worst_val = self.create_info_row(left_wing, "Worst Trade:", "Loading...", ALERT_RED)

        right_wing = ctk.CTkFrame(self.tab_perf, fg_color=PANEL_COLOR, corner_radius=10)
        right_wing.pack(side="left", fill="both", expand=True, pady=(0, 10))

        ctk.CTkLabel(right_wing, text="📓 TRADE JOURNAL (LAST 50)", font=("Arial", 14, "bold"), text_color=TEXT_WHITE).pack(pady=(15, 5))
        self.journal_scroll = ctk.CTkScrollableFrame(right_wing, fg_color="transparent")
        self.journal_scroll.pack(fill="both", expand=True, padx=10, pady=5)

    def save_gui_settings(self):
        new_cfg = {
            "risk_pct": float(self.inp_risk.get() or 1.0), "max_spread_ratio": 0.15, "max_port_risk": float(self.inp_port.get() or 15.0),
            "kelly_active": bool(self.sw_kelly.get()), "grid_active": bool(self.sw_grid.get()), "covar_guard": bool(self.sw_covar.get()),
            "anti_hedge": bool(self.sw_hedge.get()), "trade_trend": bool(self.sw_trend.get()), "trade_chop": bool(self.sw_chop.get()),
            "trade_extreme": bool(self.sw_extreme.get()), "tg_token": self.inp_tg_token.get(), "tg_chat": self.inp_tg_chat.get(),
            "wh_pass": self.inp_wh_pass.get(), "route_mt5": bool(self.sw_mt5.get()), "route_nt8": bool(self.sw_nt8.get()), "nt8_incoming_path": self.inp_nt8_path.get()
        }
        save_settings(new_cfg)
        log_to_gui(f"⚙️ Parametric bounds set dynamically.", "blue")

    def create_info_row(self, parent, title, value, val_color):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=3)
        ctk.CTkLabel(row, text=title, font=("Arial", 12), text_color=TEXT_WHITE).pack(side="left")
        lbl = ctk.CTkLabel(row, text=value, font=("Courier New", 14, "bold"), text_color=val_color)
        lbl.pack(side="right")
        return lbl

    def process_gui_queue(self):
        try:
            while True:
                msg, color = gui_log_queue.get_nowait()
                timestamp = datetime.datetime.now().strftime("[%H:%M:%S]")
                formatted_msg = f"{timestamp} {msg}\n"
                self.console.insert("end", formatted_msg, color)
                self.console.see("end")
                if hasattr(self, 'expanded_console') and self.expanded_console.winfo_exists():
                    self.expanded_console.insert("end", formatted_msg, color)
                    self.expanded_console.see("end")
        except queue.Empty: pass
        self.after(100, self.process_gui_queue)

    def refresh_performance(self):
        net_pnl = PERFORMANCE_DATA.get("net_pnl", 0.0)
        self.perf_net_val.configure(text=f"{'+' if net_pnl >=0 else ''}${net_pnl:.2f}", text_color=QUANT_BLUE if net_pnl >= 0 else ALERT_RED)
        self.perf_wr_val.configure(text=f"{PERFORMANCE_DATA.get('win_rate', 0.0):.1f}%")
        pf = PERFORMANCE_DATA.get("profit_factor", 0.0)
        self.perf_pf_val.configure(text=f"{pf:.2f}" if pf != float('inf') else "MAX")
        self.perf_trades_val.configure(text=str(PERFORMANCE_DATA.get("total_trades", 0)))
        best = PERFORMANCE_DATA.get("best", 0.0)
        worst = PERFORMANCE_DATA.get("worst", 0.0)
        self.perf_best_val.configure(text=f"+${best:.2f}")
        self.perf_worst_val.configure(text=f"-${abs(worst):.2f}")

        journal_data = PERFORMANCE_DATA.get("journal", [])
        while len(self.journal_pool) < len(journal_data):
            f = ctk.CTkFrame(self.journal_scroll, fg_color=BG_COLOR, corner_radius=5)
            l = ctk.CTkLabel(f, text="", font=("Courier New", 12, "bold"))
            l.pack(pady=10)
            self.journal_pool.append({"frame": f, "label": l})

        for i, (dt_str, sym, action, vol, net) in enumerate(journal_data):
            item = self.journal_pool[i]
            item["frame"].pack(fill="x", pady=5)
            c = QUANT_BLUE if net > 0 else ALERT_RED
            pfx = "+" if net > 0 else ""
            txt = f"[{dt_str}] {sym} | {action} | {vol} Lots | {pfx}${net:.2f}"
            if item["label"].cget("text") != txt: item["label"].configure(text=txt, text_color=c)

        for i in range(len(journal_data), len(self.journal_pool)): self.journal_pool[i]["frame"].pack_forget()
        self.after(2000, self.refresh_performance)

    def refresh_telemetry(self):
        if mt5.terminal_info() is not None:
            account = mt5.account_info()
            if account:
                new_broker = str(account.company)[:15] + ".." if len(str(account.company)) > 15 else str(account.company)
                if new_broker != self.last_state["broker"]:
                    self.acc_broker_val.configure(text=new_broker)
                    self.last_state["broker"] = new_broker

                new_login = str(account.login)
                if new_login != self.last_state["login"]:
                    self.acc_id_val.configure(text=new_login)
                    self.last_state["login"] = new_login

                new_lev = f"1:{account.leverage}"
                if new_lev != self.last_state["leverage"]:
                    self.acc_lev_val.configure(text=new_lev)
                    self.last_state["leverage"] = new_lev

                new_margin = f"${account.margin:.2f}"
                if new_margin != self.last_state.get("margin"):
                    self.acc_margin_val.configure(text=new_margin)
                    self.last_state["margin"] = new_margin

                today_net = PERFORMANCE_DATA.get("today_closed_pnl", 0.0) + account.profit
                new_today_pnl = f"{'+' if today_net >= 0 else ''}${today_net:.2f}"
                if new_today_pnl != self.last_state.get("today_pnl_str"):
                    self.acc_today_pnl_val.configure(text=new_today_pnl, text_color=QUANT_BLUE if today_net >= 0 else ALERT_RED)
                    self.last_state["today_pnl_str"] = new_today_pnl

                known = False
                for acc in self.accounts_data:
                    if acc.get("login") == account.login:
                        acc["pnl"] = today_net
                        if not acc.get("server"): acc["server"] = account.server
                        known = True
                        break
                if not known and account.login != 0:
                    self.accounts_data.append({
                        "login": account.login, "password": "", "server": account.server,
                        "nickname": account.company, "pnl": today_net
                    })
                    save_accounts(self.accounts_data)

            while len(self.acc_pool) < len(self.accounts_data):
                f = ctk.CTkFrame(self.accounts_scroll, fg_color=BG_COLOR, corner_radius=5)
                top_row = ctk.CTkFrame(f, fg_color="transparent")
                top_row.pack(fill="x", padx=10, pady=(5,0))
                l_name = ctk.CTkLabel(top_row, text="", font=("Arial", 12, "bold"), text_color=TEXT_WHITE)
                l_name.pack(side="left")
                btn_edit = ctk.CTkButton(top_row, text="⚙️", width=30, height=24, fg_color=PANEL_COLOR, hover_color=QUANT_BLUE)
                btn_edit.pack(side="right")
                l_pnl = ctk.CTkLabel(f, text="", font=("Courier New", 12), text_color=QUANT_BLUE)
                l_pnl.pack(anchor="w", padx=10, pady=(0,5))
                self.acc_pool.append({"frame": f, "name": l_name, "pnl": l_pnl, "edit_btn": btn_edit})

            for i, acc in enumerate(self.accounts_data):
                item = self.acc_pool[i]
                item["frame"].pack(fill="x", pady=5)
                login_id = acc.get("login", 0)
                nick = acc.get("nickname") or acc.get("server") or str(login_id)
                display_name = f"{nick} ({login_id})"

                if item["name"].cget("text") != display_name: item["name"].configure(text=display_name)
                item["edit_btn"].configure(command=lambda current_idx=i: self.open_edit_dialog(current_idx))

                pnl_val = acc.get("pnl", 0.0)
                pnl_txt = f"Net PnL: {'+' if pnl_val >= 0 else ''}${pnl_val:.2f}"
                if item["pnl"].cget("text") != pnl_txt: item["pnl"].configure(text=pnl_txt, text_color=QUANT_BLUE if pnl_val >= 0 else ALERT_RED)

            for i in range(len(self.accounts_data), len(self.acc_pool)): self.acc_pool[i]["frame"].pack_forget()

            positions = mt5.positions_get()
            num_pos = len(positions) if positions else 0

            if num_pos > 0:
                asset_vols = {}
                total_vol = sum(p.volume for p in positions)
                for p in positions:
                    base_sym = p.symbol[:6]
                    asset_vols[base_sym] = asset_vols.get(base_sym, 0) + p.volume
                donut_dict = {sym: (v/total_vol) for sym, v in asset_vols.items()}
                if str(donut_dict) != self.last_state.get("donut_data"):
                    self.draw_donut_chart(donut_dict)
                    self.last_state["donut_data"] = str(donut_dict)
            else:
                if "{}" != self.last_state.get("donut_data"):
                    self.draw_donut_chart({})
                    self.last_state["donut_data"] = "{}"

            for sym, lbl in self.heatmap_labels.items():
                data = ASSET_HEATMAP.get(sym, {})
                txt = f"{data.get('score', 0)}% [{data.get('reg', 'SCANNING')}]"
                if lbl.cget("text") != txt: lbl.configure(text=txt, text_color=data.get("color", MUTED_TEXT))

            if str(len(self.accounts_data)) != self.last_state.get("managed_accs"):
                self.managed_accs_val.configure(text=str(len(self.accounts_data)))
                self.last_state["managed_accs"] = str(len(self.accounts_data))

            if str(num_pos) != self.last_state.get("total_pos_count"):
                self.total_pos_val.configure(text=str(num_pos))
                self.last_state["total_pos_count"] = str(num_pos)

            new_lock = "ENGAGED" if SYSTEM_PAUSED else "DISENGAGED"
            if new_lock != self.last_state["lock"]:
                self.lock_val.configure(text=new_lock, text_color=ALERT_RED if SYSTEM_PAUSED else QUANT_BLUE)
                self.last_state["lock"] = new_lock

            if LATEST_REGIME != self.last_state["regime"]:
                self.regime_val.configure(text=LATEST_REGIME)
                self.last_state["regime"] = LATEST_REGIME

            memory = load_oracle_memory()
            new_mom = ", ".join(memory.get("momentum", [])) if memory.get("momentum") else "Awaiting Data"
            if new_mom != self.last_state["mom"]:
                self.mom_val.configure(text=new_mom)
                self.last_state["mom"] = new_mom

            if account and account.equity > 0:
                peak = memory.get("high_watermark", account.equity)
                if account.equity > peak:
                    peak = account.equity
                    memory["high_watermark"] = peak
                    save_oracle_memory(memory)

                dd_pct = ((peak - account.equity) / peak) * 100 if peak > 0 else 0
                mult = 1.0
                if dd_pct <= 1.0: mult = 1.5
                elif dd_pct >= 10.0: mult = 0.25
                elif dd_pct >= 5.0: mult = 0.5

                new_hdr = f"PEAK: ${peak:.2f}  |  DD: -{dd_pct:.1f}%  |  ACE MULTI: {mult}x"
                if new_hdr != self.last_state["header_risk"]:
                    self.header_risk_lbl.configure(text=new_hdr)
                    self.last_state["header_risk"] = new_hdr

                conf_score = 50
                if "TREND" in LATEST_REGIME or "EXTREME" in LATEST_REGIME: conf_score += 30
                elif "CHOP" in LATEST_REGIME: conf_score -= 20
                if dd_pct <= 1.0: conf_score += 20
                elif dd_pct >= 10.0: conf_score -= 40
                elif dd_pct >= 5.0: conf_score -= 20

                conf_score = max(0, min(100, conf_score))
                new_risk_conf = f"{conf_score}% [{'RISK ON' if conf_score >= 60 else 'RISK OFF'}]"
                if new_risk_conf != self.last_state.get("risk_conf"):
                    self.risk_conf_val.configure(text=new_risk_conf, text_color=QUANT_BLUE if conf_score >= 60 else ALERT_RED)
                    self.last_state["risk_conf"] = new_risk_conf

                exposure_pct = account.margin / account.equity
                if abs(exposure_pct - self.last_state["exposure_val"]) > 0.005:
                    self.exposure_bar.set(min(exposure_pct, 1.0))
                    self.exposure_bar.configure(progress_color=ALERT_RED if exposure_pct > 0.15 else QUANT_BLUE)
                    self.last_state["exposure_val"] = exposure_pct

            while len(self.pos_pool) < num_pos:
                frame = ctk.CTkFrame(self.positions_scroll, fg_color=BG_COLOR, corner_radius=5)
                lbl = ctk.CTkLabel(frame, text="", font=("Courier New", 12, "bold"))
                lbl.pack(pady=10)
                self.pos_pool.append({"frame": frame, "label": lbl})

            usd_exposure = 0
            if num_pos > 0:
                self.no_pos_lbl.pack_forget()
                for i, pos in enumerate(positions):
                    if "USD" in pos.symbol:
                        if pos.symbol.startswith("USD") and pos.type == 0: usd_exposure += 1
                        elif pos.symbol.startswith("USD") and pos.type == 1: usd_exposure -= 1
                        elif pos.symbol.endswith("USD") and pos.type == 0: usd_exposure -= 1
                        elif pos.symbol.endswith("USD") and pos.type == 1: usd_exposure += 1

                    pool_item = self.pos_pool[i]
                    pool_item["frame"].pack(fill="x", pady=5)
                    p_type = "BUY" if pos.type == 0 else "SELL"
                    pnl_prefix = "+" if pos.profit > 0 else ""
                    new_txt = f"{pos.symbol} | {p_type} | {pos.volume} Lots | Entry: {pos.price_open} | Float: {pnl_prefix}${pos.profit:.2f}"
                    if pool_item["label"].cget("text") != new_txt:
                        pool_item["label"].configure(text=new_txt, text_color=QUANT_BLUE if pos.profit > 0 else ALERT_RED)
            else: self.no_pos_lbl.pack(pady=20)

            for i in range(num_pos, len(self.pos_pool)): self.pos_pool[i]["frame"].pack_forget()

            new_usd = f"{usd_exposure} {'Long' if usd_exposure > 0 else ('Short' if usd_exposure < 0 else 'Flat')}"
            if new_usd != self.last_state["usd"]:
                self.usd_covar_val.configure(text=new_usd, text_color=ALERT_RED if abs(usd_exposure) > 2 else TEXT_WHITE)
                self.last_state["usd"] = new_usd

        self.after(500, self.refresh_telemetry)

if __name__ == "__main__":
    app_gui = OracleDashboard()
    app_gui.mainloop()
