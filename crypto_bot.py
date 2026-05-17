import os
import yfinance as yf
import pandas as pd
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# =========================
# TELEGRAM SETTINGS
# =========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================
# STRATEGY SETTINGS
# =========================

WATCHLIST_FILE = "watchlist_crypto.txt"

PERIOD = "90d"
INTERVAL = "4h"

RSI_MIN = 50
RSI_MAX = 70

VOLUME_SPIKE_MULTIPLIER = 1.5
ATR_STOP_MULTIPLIER = 1.5

MIN_RISK_REWARD = 1.5


# =========================
# TELEGRAM FUNCTION
# =========================

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")


# =========================
# INDICATORS
# =========================

def fix_yfinance_columns(data):
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


def calculate_rsi(data, period=14):
    delta = data["Close"].diff()

    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_atr(data, period=14):
    high_low = data["High"] - data["Low"]
    high_close = abs(data["High"] - data["Close"].shift())
    low_close = abs(data["Low"] - data["Close"].shift())

    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()

    return atr


def add_indicators(data):
    data["EMA9"] = data["Close"].ewm(span=9, adjust=False).mean()
    data["EMA20"] = data["Close"].ewm(span=20, adjust=False).mean()
    data["EMA50"] = data["Close"].ewm(span=50, adjust=False).mean()

    data["RSI"] = calculate_rsi(data)
    data["ATR"] = calculate_atr(data)

    data["Volume_MA20"] = data["Volume"].rolling(20).mean()

    # Previous resistance breakout level
    data["Breakout_Level"] = data["High"].rolling(20).max().shift(1)

    return data


# =========================
# BTC MARKET FILTER
# =========================

def is_btc_market_ok():
    print("🔍 Checking BTC market filter...")
    btc = yf.download(
        "BTC-USD",
        period=PERIOD,
        interval=INTERVAL,
        progress=False,
        auto_adjust=True
    )

    btc = fix_yfinance_columns(btc)

    if btc.empty or len(btc) < 60:
        print("❌ BTC market filter: Insufficient data")
        return False

    btc = add_indicators(btc)
    latest = btc.iloc[-1]

    btc_ok = (
        latest["Close"] > latest["EMA50"] and
        latest["EMA9"] > latest["EMA20"] > latest["EMA50"] and
        latest["RSI"] >= 45
    )

    if btc_ok:
        print(f"✅ BTC market filter: PASS - RSI={latest['RSI']:.2f}, Price above EMA50")
    else:
        print(f"❌ BTC market filter: FAIL - RSI={latest['RSI']:.2f}")
    
    return btc_ok


# =========================
# STRATEGY LOGIC
# =========================

def check_crypto_signal(symbol):
    print(f"  📍 Analyzing {symbol}...")
    data = yf.download(
        symbol,
        period=PERIOD,
        interval=INTERVAL,
        progress=False,
        auto_adjust=True
    )

    data = fix_yfinance_columns(data)

    if data.empty or len(data) < 60:
        print(f"  ❌ {symbol}: Insufficient data")
        return

    data = add_indicators(data)
    data = data.dropna()

    if data.empty:
        print(f"  ❌ {symbol}: No valid data after indicator calculation")
        return

    latest = data.iloc[-1]
    previous = data.iloc[-2]

    entry = latest["Close"]
    atr = latest["ATR"]

    stop_loss = entry - (atr * ATR_STOP_MULTIPLIER)
    risk = entry - stop_loss

    target_1 = entry + (risk * 1.5)
    target_2 = entry + (risk * 2)

    trend_ok = (
        latest["Close"] > latest["EMA50"] and
        latest["EMA9"] > latest["EMA20"] > latest["EMA50"]
    )

    rsi_ok = RSI_MIN <= latest["RSI"] <= RSI_MAX

    volume_ok = latest["Volume"] > latest["Volume_MA20"] * VOLUME_SPIKE_MULTIPLIER

    breakout_ok = latest["Close"] > latest["Breakout_Level"]

    candle_strength_ok = latest["Close"] > latest["Open"]

    not_too_extended = latest["Close"] < latest["EMA9"] + (atr * 2)

    signal = (
        trend_ok and
        rsi_ok and
        volume_ok and
        breakout_ok and
        candle_strength_ok and
        not_too_extended
    )

    if signal:
        print(f"  ✅ {symbol}: SIGNAL FOUND!")
        message = f"""
🚀 CRYPTO SWING TRADE ALERT

Symbol: {symbol}
Timeframe: 4H

Entry Zone: {entry:.4f}
Stop Loss: {stop_loss:.4f}

Target 1: {target_1:.4f}
Target 2: {target_2:.4f}

RSI: {latest['RSI']:.2f}
ATR: {atr:.4f}

Strategy Confirmations:
EMA 9 > EMA 20 > EMA 50 ✅
Price above EMA 50 ✅
RSI between 50 and 70 ✅
Volume spike ✅
20-candle breakout ✅
BTC market filter ✅

Risk Note:
Use small position size.
Risk only 0.5% to 1% per trade.
"""
        send_telegram_message(message)
    else:
        print(f"  ⚠️ {symbol}: No signal (Trend:{trend_ok} RSI:{rsi_ok} Vol:{volume_ok} BO:{breakout_ok})")


# =========================
# MAIN BOT
# =========================

def run_bot():
    print(f"\n{'='*70}")
    print(f"🤖 CRYPTO BOT START - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    if not is_btc_market_ok():
        print("⛔ BTC market filter failed - aborting scan")
        message = """
⚠️ Crypto Market Filter

BTC trend is weak right now.
No new crypto swing trades suggested.
"""
        send_telegram_message(message)
        return

    print("✅ BTC market filter passed - starting scan")
    with open(WATCHLIST_FILE) as f:
        tickers = [line.strip().upper() for line in f if line.strip()]
    
    print(f"📋 Scanning {len(tickers)} crypto pairs: {', '.join(tickers)}\n")
    
    signal_count = 0
    for symbol in tickers:
        try:
            check_crypto_signal(symbol)
            
        except Exception as e:
            print(f"  ❌ Error checking {symbol}: {e}")

    print(f"\n{'='*70}")
    print(f"✅ CRYPTO BOT COMPLETE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    run_bot()