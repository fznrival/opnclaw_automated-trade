import MetaTrader5 as mt5
import pandas as pd
import json
import os
import requests
from datetime import datetime

# 1. Load Konfigurasi
config_path = os.path.expanduser("~/.openclaw/trade_config.json")
try:
    with open(config_path, 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    print("Error: trade_config.json tidak ditemukan di ~/.openclaw/")
    quit()

TRADING_CONF = config.get("trading", {})
SYMBOL = TRADING_CONF.get("symbol", "USTECm")
LOT_SIZE = TRADING_CONF.get("lot_size", 0.1)
RR_RATIO = TRADING_CONF.get("risk_reward_ratio", 2.0)
MAGIC = TRADING_CONF.get("magic_number", 2022001)
DEV = TRADING_CONF.get("slippage_dev", 20)
BOT_TOKEN = config.get("telegram_bot_token")
CHAT_ID = config.get("telegram_chat_id")

# Konversi Timeframe
tf_map = {5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15, 60: mt5.TIMEFRAME_H1}
TIMEFRAME = tf_map.get(TRADING_CONF.get("timeframe", 15), mt5.TIMEFRAME_M15)

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"})

def run_ict_scanner():
    if not mt5.initialize():
        msg = f"❌ Gagal konek ke MT5. Error: {mt5.last_error()}"
        print(msg)
        send_telegram(msg)
        return

    if not mt5.symbol_select(SYMBOL, True):
        print(f"Simbol {SYMBOL} tidak ditemukan.")
        mt5.shutdown()
        return

    # Ambil Data
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, 50)
    df = pd.DataFrame(rates)
    
    # Setup Market Structure
    current_price = df['close'].iloc[-1]
    swing_low = df['low'].tail(20).min()
    swing_high = df['high'].tail(20).max()
    
    # Deteksi FVG 3 Candle Terakhir
    fvg = None
    if df['low'].iloc[-2] > df['high'].iloc[-4]:
        fvg = {'type': 'bullish', 'top': df['low'].iloc[-2], 'bottom': df['high'].iloc[-4]}
    elif df['high'].iloc[-2] < df['low'].iloc[-4]:
        fvg = {'type': 'bearish', 'top': df['low'].iloc[-4], 'bottom': df['high'].iloc[-2]}

    if not fvg:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {SYMBOL} - NEUTRAL (Menunggu FVG)")
        mt5.shutdown()
        return

    # Hitung OTE (Optimal Trade Entry 0.62 - 0.79)
    range_dist = swing_high - swing_low
    if fvg['type'] == 'bullish':
        ote_upper = swing_high - (range_dist * 0.62)
        ote_lower = swing_high - (range_dist * 0.79)
    else:
        ote_lower = swing_low + (range_dist * 0.62)
        ote_upper = swing_low + (range_dist * 0.79)

    in_ote = ote_lower <= current_price <= ote_upper

    # Logika Eksekusi
    tick = mt5.symbol_info_tick(SYMBOL)
    trade_type = None
    
    if fvg['type'] == 'bullish' and current_price <= fvg['top'] and in_ote:
        trade_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
        sl = swing_low - 5.0
        tp = price + (abs(price - sl) * RR_RATIO)
        action_str = "BUY"
        
    elif fvg['type'] == 'bearish' and current_price >= fvg['bottom'] and in_ote:
        trade_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
        sl = swing_high + 5.0
        tp = price - (abs(price - sl) * RR_RATIO)
        action_str = "SELL"

    if trade_type is not None:
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": float(LOT_SIZE),
            "type": trade_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": DEV,
            "magic": MAGIC,
            "comment": "ICT_OpenClaw",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            msg = f"⚡ *AUTO-TRADE EXECUTED* ⚡\n\n📌 *Asset:* {SYMBOL}\n🛒 *Action:* {action_str}\n💰 *Entry:* {price}\n🛑 *SL:* {sl:.2f}\n🎯 *TP:* {tp:.2f}\n⚖️ *Lot:* {LOT_SIZE}\n\n_Reason: {fvg['type'].upper()} FVG + OTE Confirmed_"
            print("Order Sukses!")
            send_telegram(msg)
        else:
            print(f"Order Gagal: {result.comment}")
    else:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {SYMBOL} - WAIT (Harga belum masuk zona eksekusi OTE)")

    mt5.shutdown()

if __name__ == "__main__":
    run_ict_scanner()
