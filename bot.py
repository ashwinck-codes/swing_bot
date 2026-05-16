import os
import requests
import yfinance as yf
import pandas as pd
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": message})


def analyze_stock(ticker: str):
    df = yf.download(ticker, period="6mo", interval="1d", progress=False)

    if df.empty or len(df) < 60:
        return None

    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA50"] = df["Close"].rolling(50).mean()
    df["VOL20"] = df["Volume"].rolling(20).mean()
    df["RSI"] = RSIIndicator(df["Close"], window=14).rsi()
    df["ATR"] = AverageTrueRange(
        high=df["High"],
        low=df["Low"],
        close=df["Close"],
        window=14
    ).average_true_range()

    df["HIGH20_PREV"] = df["High"].rolling(20).max().shift(1)

    latest = df.iloc[-1]

    buy_signal = (
        latest["Close"] > latest["MA20"] and
        latest["MA20"] > latest["MA50"] and
        50 <= latest["RSI"] <= 70 and
        latest["Volume"] > 1.5 * latest["VOL20"] and
        latest["Close"] > latest["HIGH20_PREV"]
    )

    if not buy_signal:
        return None

    entry = latest["Close"]
    stop = entry - (2 * latest["ATR"])
    risk = entry - stop
    target = entry + (2 * risk)

    return {
        "ticker": ticker,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "rsi": round(latest["RSI"], 2),
        "volume_ratio": round(latest["Volume"] / latest["VOL20"], 2)
    }


def run_scan():
    with open("watchlist.txt") as f:
        tickers = [line.strip().upper() for line in f if line.strip()]

    alerts = []

    for ticker in tickers:
        try:
            result = analyze_stock(ticker)
            if result:
                alerts.append(result)
        except Exception as e:
            print(f"Error analyzing {ticker}: {e}")

    if not alerts:
        send_telegram("Swing Bot: No valid setups today.")
        return

    for a in alerts:
        msg = f"""
📈 Swing Trade Alert

Ticker: {a['ticker']}
Entry: ${a['entry']}
Stop Loss: ${a['stop']}
Target: ${a['target']}
RSI: {a['rsi']}
Volume vs Avg: {a['volume_ratio']}x

Plan:
Buy only near entry.
Risk max 1% of account.
Do not chase if price gaps too high.
"""
        send_telegram(msg)


if __name__ == "__main__":
    run_scan()