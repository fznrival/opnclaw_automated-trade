import MetaTrader5 as mt5
import pandas as pd
import json
import os
from datetime import datetime
import requests

# =========================
# LOAD CONFIG
# =========================
CONFIG_PATH = "config.json"

if not os.path.exists(CONFIG_PATH):
    raise FileNotFoundError("config.json tidak ditemukan")

with open(CONFIG_PATH, "r") as f:
    config = json.load(f)

SYMBOL = config["trading"]["symbol"]
TIMEFRAME = config["trading"]["timeframe"]
LOT = config["trading"]["lot_size"]

# Mapping timeframe ke MT5
TIMEFRAME_MAP = {
    1: mt5.TIMEFRAME_M1,
    5: mt5.TIMEFRAME_M5,
    15: mt5.TIMEFRAME_M15,
    30: mt5.TIMEFRAME_M30,
    60: mt5.TIMEFRAME_H1
}

MT5_TF = TIMEFRAME_MAP.get(TIMEFRAME, mt5.TIMEFRAME_M15)

# =========================
# TELEGRAM
# =========================
def send_telegram(message):
    token = config.get("telegram_bot_token")
    chat_id = config.get("telegram_chat_id")

    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        requests.post(url, data={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        })
    except Exception as e:
        print("Telegram Error:", e)

# =========================
# ICT SESSION CHECK
# =========================
def is_market_open():
    now = datetime.now().strftime("%H:%M")
    sessions = config.get("sessions", {})

    for session_name, time_range in sessions.items():
        start, end = time_range

        if start <= now <= end:
            return session_name

    return None

# =========================
# SIMPLE FVG DETECTION
# =========================
def detect_fvg(df):
    for i in range(2, len(df)):
        prev = df.iloc[i-2]
        curr = df.iloc[i]

        # Bullish FVG
        if prev['high'] < curr['low']:
            return "buy"

        # Bearish FVG
        if prev['low'] > curr['high']:
            return "sell"

    return None

# =========================
# ORDER EXECUTION
# =========================
def execute_trade(signal):
    price = mt5.symbol_info_tick(SYMBOL).ask if signal == "buy" else mt5.symbol_info_tick(SYMBOL).bid

    sl = price - 100 * mt5.symbol_info(SYMBOL).point if signal == "buy" else price + 100 * mt5.symbol_info(SYMBOL).point
    tp = price + 200 * mt5.symbol_info(SYMBOL).point if signal == "buy" else price - 200 * mt5.symbol_info(SYMBOL).point

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": LOT,
        "type": mt5.ORDER_TYPE_BUY if signal == "buy" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": config["trading"]["slippage_dev"],
        "magic": config["trading"]["magic_number"],
        "comment": "ICT_BOT",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    return result

# =========================
# MAIN LOGIC
# =========================
def run_ict_scanner():
    active_session = is_market_open()

    if not active_session:
        print("Bukan jam ICT Killzone")
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Session: {active_session}")

    if not mt5.initialize():
        print("MT5 gagal initialize")
        return

    rates = mt5.copy_rates_from_pos(SYMBOL, MT5_TF, 0, 100)

    if rates is None:
        print("Gagal ambil data")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)

    signal = detect_fvg(df)

    if signal:
        result = execute_trade(signal)

        msg = f"""
⚡ *ICT {active_session.upper()} EXECUTION*
📌 Symbol: {SYMBOL}
📊 Signal: {signal.upper()}
🕒 Time: {datetime.now().strftime('%H:%M:%S')}
"""

        print(msg)
        send_telegram(msg)
    else:
        print("Tidak ada setup")

    mt5.shutdown()

# =========================
# RUN
# =========================
if __name__ == "__main__":
    run_ict_scanner()
