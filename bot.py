import os
import json
import time
import requests
import yfinance as yf
from datetime import datetime
from dotenv import load_dotenv
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


WATCHLIST_FILE = "watchlist.txt"
ALERT_LOG_FILE = "alert_log.json"

RISK_REWARD = 2.0
ATR_MULTIPLIER = 1.5
VOLUME_SPIKE_MULTIPLIER = 1.2
BREAKOUT_LOOKBACK = 20


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": message})


def load_alert_log():
    if not os.path.exists(ALERT_LOG_FILE):
        return {}
    with open(ALERT_LOG_FILE, "r") as f:
        return json.load(f)


def save_alert_log(log):
    with open(ALERT_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def already_alerted_today(ticker):
    log = load_alert_log()
    today = datetime.now().strftime("%Y-%m-%d")
    return log.get(ticker) == today


def mark_alerted_today(ticker):
    log = load_alert_log()
    today = datetime.now().strftime("%Y-%m-%d")
    log[ticker] = today
    save_alert_log(log)


def get_data(ticker, period="8mo"):
    for attempt in range(1, 4):
        try:
            df = yf.download(
                ticker,
                period=period,
                interval="1d",
                progress=False,
                threads=False,
                timeout=10,
                session=None,
            )

            if df is not None and not df.empty:
                if isinstance(df.columns[0], tuple):
                    df.columns = [c[0] for c in df.columns]
                return df.dropna()

            print(f"Attempt {attempt} failed for '{ticker}': empty result")
        except Exception as e:
            print(f"Attempt {attempt} failed for '{ticker}': {e}")

        time.sleep(3)

    print(f"Attempting fallback history download for '{ticker}'")
    try:
        df = yf.Ticker(ticker).history(
            period=period,
            interval="1d",
            auto_adjust=True,
            actions=False,
            prepost=False,
        )

        if df is not None and not df.empty:
            if isinstance(df.columns[0], tuple):
                df.columns = [c[0] for c in df.columns]
            return df.dropna()

        print(f"Fallback history returned empty data for '{ticker}'")
    except Exception as e:
        print(f"Fallback history failed for '{ticker}': {e}")

    print(f"All retries and fallback failed for ticker '{ticker}'")
    return None


def calculate_indicators(df):
    df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    df["RSI"] = RSIIndicator(df["Close"], window=14).rsi()

    df["ATR"] = AverageTrueRange(
        high=df["High"],
        low=df["Low"],
        close=df["Close"],
        window=14
    ).average_true_range()

    df["VOL20"] = df["Volume"].rolling(20).mean()
    df["HIGH20_PREV"] = df["High"].rolling(BREAKOUT_LOOKBACK).max().shift(1)

    return df


def market_is_healthy():
    print("🔍 Checking market health (SPY & QQQ)...")
    spy = get_data("SPY")
    qqq = get_data("QQQ")

    if spy is None or qqq is None:
        print("❌ Market filter: Failed to get data")
        return False

    spy = calculate_indicators(spy)
    qqq = calculate_indicators(qqq)

    spy_latest = spy.iloc[-1]
    qqq_latest = qqq.iloc[-1]

    spy_ok = spy_latest["Close"] > spy_latest["EMA20"] > spy_latest["EMA50"]
    qqq_ok = qqq_latest["Close"] > qqq_latest["EMA20"] > qqq_latest["EMA50"]

    print(f"📊 SPY trend: {'✅ BULLISH' if spy_ok else '❌ WEAK'}")
    print(f"📊 QQQ trend: {'✅ BULLISH' if qqq_ok else '❌ WEAK'}")
    result = spy_ok or qqq_ok
    print(f"🟢 Market filter: {'PASS ✅' if result else 'FAIL ❌'}")
    return result


def analyze_stock(ticker):
    print(f"  📍 Analyzing {ticker}...")
    df = get_data(ticker)

    if df is None or len(df) < 80:
        print(f"  ❌ {ticker}: Insufficient data")
        return None

    df = calculate_indicators(df)
    latest = df.iloc[-1]
    previous = df.iloc[-2]

    close = latest["Close"]
    volume = latest["Volume"]

    ema_bullish = (
        close > latest["EMA9"] >
        latest["EMA20"] >
        latest["EMA50"]
    )

    rsi_good = 50 <= latest["RSI"] <= 70

    volume_spike = volume > VOLUME_SPIKE_MULTIPLIER * latest["VOL20"]

    breakout = close > latest["HIGH20_PREV"]

    bullish_candle = close > latest["Open"]

    continuation = latest["EMA9"] > previous["EMA9"]

    score = 0
    reasons = []

    if ema_bullish:
        score += 25
        reasons.append("EMA 9/20/50 bullish stack")

    if rsi_good:
        score += 20
        reasons.append("RSI healthy, not overbought")

    if volume_spike:
        score += 20
        reasons.append("Volume spike")

    if breakout:
        score += 25
        reasons.append("20-day breakout")

    if bullish_candle:
        score += 5
        reasons.append("Bullish daily candle")

    if continuation:
        score += 5
        reasons.append("EMA9 rising")

    buy_signal = score >= 75 and ema_bullish and rsi_good and volume_spike and breakout

    if not buy_signal:
        print(f"  ⚠️ {ticker}: Score {score}/100 - No signal yet")
        return None
    
    print(f"  ✅ {ticker}: Score {score}/100 - SIGNAL FOUND!")

    entry = close
    stop = entry - (ATR_MULTIPLIER * latest["ATR"])
    risk = entry - stop
    target = entry + (RISK_REWARD * risk)

    if risk <= 0:
        return None

    return {
        "ticker": ticker,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "risk": round(risk, 2),
        "atr": round(latest["ATR"], 2),
        "rsi": round(latest["RSI"], 2),
        "volume_ratio": round(volume / latest["VOL20"], 2),
        "score": score,
        "reasons": reasons
    }


def position_size(account_size, risk_percent, entry, stop):
    risk_amount = account_size * (risk_percent / 100)
    risk_per_share = entry - stop

    if risk_per_share <= 0:
        return 0

    return int(risk_amount / risk_per_share)



def get_top_momentum_stocks():
    """
    Fetch top 100 momentum stocks based on:
    - Price change (3M)
    - Volume
    Then return top 20
    """

    # Broad universe (you can expand later)
    universe = [
        "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AMD","PLTR","AVGO",
        "COST","NFLX","CRM","NOW","SHOP","UBER","PANW","CRWD","SNOW",
        "DDOG","NET","ADBE","INTU","PYPL","COIN","MRNA","LLY","NVO","TSM",
        "ASML","MU","LRCX","KLAC","MELI","SE","ROKU","ZM","DOCU","TEAM",
        "OKTA","ZS","MDB","HUBS","WDAY","FTNT","ANET","CDNS","SNPS","ORCL",
        "QCOM","TXN","ADI","NXPI","AMAT","DELL","HPQ","BABA","JD","PDD",
        "FSLR","ENPH","SEDG","RIVN","LCID","NIO","XPEV","LI","BA","CAT",
        "GE","HON","UPS","FDX","WMT","TGT","HD","LOW","DIS","SBUX",
        "KO","PEP","MCD","NKE","LULU","ABNB","BKNG","EXPE","UBER","LYFT",
        "SPOT","SHOP","CRWD","PANW","DDOG","NET","SNOW","MDB","ZS"
    ]

    momentum_scores = []

    for ticker in set(universe):
        df = get_data(ticker, period="6mo")

        if df is None or len(df) < 60:
            continue

        try:
            # 3-month momentum
            price_now = df["Close"].iloc[-1]
            price_3m = df["Close"].iloc[-60]

            change_pct = (price_now - price_3m) / price_3m

            # avg volume
            avg_vol = df["Volume"].tail(20).mean()
            score = change_pct * avg_vol  # simple momentum score
            momentum_scores.append((ticker, score))

        except:
            continue

    # sort descending
    momentum_scores = sorted(momentum_scores, key=lambda x: x[1], reverse=True)

    # pick top 20
    top_20 = [t[0] for t in momentum_scores[:20]]

    print(f"\n🔥 Top 20 Momentum Stocks: {top_20}\n")

    return top_20

ETF_LIST = ["SMH", "QQQ", "VOO", "BOTZ", "SRVR", "WGMI"]

def analyze_etf_dip(ticker):
    df = get_data(ticker, period="6mo")

    if df is None or len(df) < 50:
        return None

    df = calculate_indicators(df)
    latest = df.iloc[-1]

    close = latest["Close"]
    ema20 = latest["EMA20"]
    ema50 = latest["EMA50"]
    rsi = latest["RSI"]

    # Trend condition
    uptrend = close > ema50

    # Dip condition
    near_ema20 = abs(close - ema20) / ema20 < 0.02
    near_ema50 = abs(close - ema50) / ema50 < 0.03

    # RSI cooling
    rsi_good = 35 <= rsi <= 50

    if uptrend and (near_ema20 or near_ema50) and rsi_good:
        return {
            "ticker": ticker,
            "price": round(close, 2),
            "ema20": round(ema20, 2),
            "ema50": round(ema50, 2),
            "rsi": round(rsi, 2),
            "zone": "EMA20" if near_ema20 else "EMA50"
        }

    return None


def run_etf_dip_scan():
    #send_telegram("📉 ETF Dip Scanner Running...")

    alerts = []
    if not market_is_healthy():
        print("⛔ Market filter failed - aborting scan")
        send_telegram("⚠️ Skipping ETF dip buys - market weak")
        return

    for ticker in ETF_LIST:
        try:
            if already_alerted_today(ticker):
                continue

            result = analyze_etf_dip(ticker)

            if result:
                alerts.append(result)
                mark_alerted_today(ticker)

        except Exception as e:
            print(f"Error analyzing {ticker}: {e}")
    
    print(f"\n📊 Results: Found {len(alerts)} valid setup(s)")


    if not alerts:
        send_telegram("No ETF dip opportunities today.")
        return

    for a in alerts:
        print(f"\n📧 Sending alert for {a['ticker']}")
        msg = f"""
📉 ETF DIP BUY ALERT

Ticker: {a['ticker']}
Price: ${a['price']}

Pullback Zone: {a['zone']}
EMA20: {a['ema20']}
EMA50: {a['ema50']}
RSI: {a['rsi']}

Strategy:
- Buy near current levels
- Add more if it touches EMA50
- Hold for trend continuation

This is a DIP in an UPTREND.
"""
        send_telegram(msg)

def run_scan():
    print(f"\n{'='*70}")
    print(f"🤖 SWING BOT START - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")
    #send_telegram("Swing Bot started scanning...")

    if not market_is_healthy():
        print("⛔ Market filter failed - aborting scan")
        send_telegram(
            "⚠️ Market filter failed.\n\nSPY/QQQ are not in a healthy trend. No new swing trades today."
        )
        return

    print("✅ Market filter passed - starting scan")
    # with open(WATCHLIST_FILE) as f:
    #     tickers = [line.strip().upper() for line in f if line.strip()]
    tickers = get_top_momentum_stocks()

    print(f"📋 Scanning {len(tickers)} tickers: {', '.join(tickers)}\n")
    alerts = []

    for ticker in tickers:
        try:
            if already_alerted_today(ticker):
                print(f"  ⏭️ {ticker}: Already alerted today")
                continue

            result = analyze_stock(ticker)

            if result:
                alerts.append(result)
                mark_alerted_today(ticker)

        except Exception as e:
            print(f"  ❌ Error analyzing {ticker}: {e}")

    print(f"\n📊 Results: Found {len(alerts)} valid setup(s)")
    if not alerts:
        print("🔚 No valid swing-trading setups found")
        #send_telegram("No valid swing-trading setups found today.")
        return

    alerts = sorted(alerts, key=lambda x: x["score"], reverse=True)

    for a in alerts:
        print(f"\n📧 Sending alert for {a['ticker']} (Score: {a['score']}/100)")
        msg = f"""
📈 SWING TRADE ALERT

Ticker: {a['ticker']}
Score: {a['score']}/100

Entry Zone: Around ${a['entry']}
Stop Loss: ${a['stop']}
Target: ${a['target']}

Risk Per Share: ${a['risk']}
ATR: {a['atr']}
RSI: {a['rsi']}
Volume Spike: {a['volume_ratio']}x average

Reasons:
- {chr(10).join(a['reasons'])}

Trade Plan:
Buy only near entry.
Do not chase large gaps.
Risk max 1% of account.
Take partial profit near target.
Move stop to breakeven after +1R.
"""
        send_telegram(msg)
    
    print(f"\n{'='*70}")
    print(f"✅ SWING BOT COMPLETE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    run_scan()
    run_etf_dip_scan() # new ETF dip scanner