import MetaTrader5 as mt5
import pandas as pd
import json
import os
import requests
from datetime import datetime

# ================= 1. LOAD CONFIG =================
config_path = os.path.expanduser("~/.openclaw/trade_config.json")
try:
    with open(config_path, 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    print("Error: trade_config.json tidak ditemukan.")
    quit()

TRADING_CONF = config.get("trading", {})
SYMBOL = TRADING_CONF.get("symbol", "USTECm")
LOT_SIZE = TRADING_CONF.get("lot_size", 0.1)
RR_RATIO = TRADING_CONF.get("risk_reward_ratio", 2.0)
MAGIC = TRADING_CONF.get("magic_number", 2022001)
DEV = TRADING_CONF.get("slippage_dev", 20)

# ================= 2. FUNGSI KEAMANAN =================
def send_telegram(message):
    try:
        token = config.get("telegram_bot_token")
        chat_id = config.get("telegram_chat_id")
        if not token or not chat_id: return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Gagal mengirim Telegram: {e}")

def is_market_open():
    now = datetime.now().strftime("%H:%M")
    for session, times in config.get("sessions", {}).items():
        if times[0] <= now <= times[1]:
            return session
    return None

# ================= 3. LOGIKA UTAMA =================
def run_ict_scanner():
    session = is_market_open()
    if not session:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Di luar Killzone. Standby.")
        return

    # Inisialisasi & Cek Koneksi Terminal
    if not mt5.initialize() or not mt5.terminal_info().connected:
        print("❌ MT5 tidak terhubung ke broker!")
        return

    # Ambil 40 Candle untuk mencari FVG historis dan Swing Points
    tf_map = {5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15, 60: mt5.TIMEFRAME_H1}
    timeframe = tf_map.get(TRADING_CONF.get("timeframe", 15), mt5.TIMEFRAME_M15)
    rates = mt5.copy_rates_from_pos(SYMBOL, timeframe, 0, 40)
    
    if rates is None:
        print(f"Gagal menarik data {SYMBOL}")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    current_price = df['close'].iloc[-1]
    
    # Deteksi Swing Low/High (Likuiditas) dari 30 candle terakhir
    window = df.iloc[-30:-1] 
    swing_low = window['low'].min()
    swing_high = window['high'].max()

    # SCANNING FVG: Mencari FVG valid dalam 10 candle terakhir
    # Range dihitung mundur, menghindari current active candle (iloc[-1])
    active_fvg = None
    for i in range(len(df)-10, len(df)-1):
        if df['low'].iloc[i] > df['high'].iloc[i-2]: # Bullish FVG
            active_fvg = {'type': 'bullish', 'top': df['low'].iloc[i], 'bottom': df['high'].iloc[i-2]}
        elif df['high'].iloc[i] < df['low'].iloc[i-2]: # Bearish FVG
            active_fvg = {'type': 'bearish', 'top': df['low'].iloc[i-2], 'bottom': df['high'].iloc[i]}
            
    if not active_fvg:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Tidak ada FVG valid.")
        mt5.shutdown()
        return

    # Kalkulasi Optimal Trade Entry (OTE 0.62 - 0.79)
    range_dist = swing_high - swing_low
    if active_fvg['type'] == 'bullish':
        ote_upper = swing_high - (range_dist * 0.62)
        ote_lower = swing_high - (range_dist * 0.79)
    else:
        ote_lower = swing_low + (range_dist * 0.62)
        ote_upper = swing_low + (range_dist * 0.79)

    in_ote = ote_lower <= current_price <= ote_upper
    
    # Cek Kondisi Eksekusi
    tick = mt5.symbol_info_tick(SYMBOL)
    trade_type = None

    if active_fvg['type'] == 'bullish' and current_price <= active_fvg['top'] and in_ote:
        trade_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
        sl = swing_low - 5.0
        tp = price + (abs(price - sl) * RR_RATIO)
        action_str = "BUY"
        
    elif active_fvg['type'] == 'bearish' and current_price >= active_fvg['bottom'] and in_ote:
        trade_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
        sl = swing_high + 5.0
        tp = price - (abs(price - sl) * RR_RATIO)
        action_str = "SELL"

    # Proses Eksekusi Order
    if trade_type is not None:
        request = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": float(LOT_SIZE),
            "type": trade_type, "price": price, "sl": sl, "tp": tp, "deviation": DEV,
            "magic": MAGIC, "comment": "ICT_OpenClaw", 
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            msg = f"⚡ *{session.upper()} EXECUTION* ⚡\n\n📌 Asset: {SYMBOL}\n🛒 Action: {action_str}\n💰 Entry: {price}\n🛑 SL: {sl:.2f}\n🎯 TP: {tp:.2f}\n⚖️ Lot: {LOT_SIZE}\n\n_Reason: {active_fvg['type'].upper()} FVG + OTE_"
            print("Order Sukses!")
            send_telegram(msg)
        else:
            print(f"Eksekusi ditolak MT5: {result.comment}")

    mt5.shutdown()

if __name__ == "__main__":
    run_ict_scanner()
