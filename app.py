import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

st.set_page_config(page_title="Dual-Engine Screener (Swing & Intraday)", layout="wide")

# ==========================================
# 1. UPSTOX DATA ENGINE
# ==========================================
def fetch_upstox_candles(instrument_key, token, days=80):
    to_date = datetime.today().strftime('%Y-%m-%d')
    from_date = (datetime.today() - timedelta(days=days)).strftime('%Y-%m-%d')
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/day/{to_date}/{from_date}"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            raw = r.json().get("data", {}).get("candles", [])
            if not raw:
                return pd.DataFrame()
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            return df.sort_values("timestamp").reset_index(drop=True)
    except Exception:
        pass
    return pd.DataFrame()

def get_technical_indicators(df):
    df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["Vol_SMA20"] = df["volume"].rolling(20).mean()
    
    # RSI 14
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))
    
    df["Daily_Range"] = df["high"] - df["low"]
    df["NR7"] = df["Daily_Range"] == df["Daily_Range"].rolling(7).min()
    return df

# ==========================================
# 2. INDEPENDENT STRATEGY 1: SWING ENGINE
# ==========================================
def run_swing_scanner(df, nifty_ret_20d, symbol, is_fno):
    if len(df) < 50:
        return None
    latest = df.iloc[-1]
    prev_20 = df.iloc[-21]
    
    stock_ret_20d = ((latest["close"] - prev_20["close"]) / prev_20["close"]) * 100
    relative_strength = stock_ret_20d - nifty_ret_20d
    vol_ratio = latest["volume"] / latest["Vol_SMA20"] if latest["Vol_SMA20"] > 0 else 0
    
    # SWING BUY (Cash & F&O)
    if (latest["close"] > latest["EMA20"] > latest["EMA50"]) and (relative_strength >= 3.0) and (55 <= latest["RSI"] <= 70) and (vol_ratio >= 1.2):
        return {
            "Symbol": symbol,
            "Type": "SWING BUY",
            "Instrument": "Cash / F&O",
            "CMP": round(latest["close"], 2),
            "Buy Above": round(latest["high"] * 1.001, 2),
            "Stop Loss (20 EMA)": round(latest["EMA20"], 2),
            "20D vs Nifty (%)": f"+{relative_strength:.1f}%",
            "RSI": round(latest["RSI"], 1),
            "Vol Multiple": f"{vol_ratio:.1f}x"
        }
    
    # SWING SHORT (F&O Only)
    if is_fno and (latest["close"] < latest["EMA20"] < latest["EMA50"]) and (relative_strength <= -3.0) and (latest["RSI"] <= 42) and (vol_ratio >= 1.1):
        return {
            "Symbol": symbol,
            "Type": "SWING SHORT",
            "Instrument": "F&O Only",
            "CMP": round(latest["close"], 2),
            "Sell Below": round(latest["low"] * 0.999, 2),
            "Stop Loss (20 EMA)": round(latest["EMA20"], 2),
            "20D vs Nifty (%)": f"{relative_strength:.1f}%",
            "RSI": round(latest["RSI"], 1),
            "Vol Multiple": f"{vol_ratio:.1f}x"
        }
    return None

# ==========================================
# 3. INDEPENDENT STRATEGY 2: INTRADAY ENGINE
# ==========================================
def run_intraday_scanner(df, symbol, is_fno):
    if len(df) < 10 or not is_fno:
        return None  # Intraday scanner prioritizes high-liquidity F&O stocks
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    day_pct = ((latest["close"] - prev["close"]) / prev["close"]) * 100
    daily_range = latest["Daily_Range"]
    clv = (latest["close"] - latest["low"]) / daily_range if daily_range > 0 else 0.5
    vol_ratio = latest["volume"] / latest["Vol_SMA20"] if latest["Vol_SMA20"] > 0 else 0

    # Setup A: Momentum Follow-through
    if day_pct >= 2.5 and clv >= 0.80 and vol_ratio >= 1.5:
        return {
            "Symbol": symbol,
            "Intraday Bias": "LONG (Momentum Continuation)",
            "Trigger Condition": "Break of 15m ORB High + Above VWAP",
            "Today %": f"+{day_pct:.2f}%",
            "Volume Surge": f"{vol_ratio:.1f}x",
            "Key Level to Watch": latest["high"]
        }
    if day_pct <= -2.5 and clv <= 0.20 and vol_ratio >= 1.5:
        return {
            "Symbol": symbol,
            "Intraday Bias": "SHORT (Selloff Continuation)",
            "Trigger Condition": "Break of 15m ORB Low + Below VWAP",
            "Today %": f"{day_pct:.2f}%",
            "Volume Surge": f"{vol_ratio:.1f}x",
            "Key Level to Watch": latest["low"]
        }
        
    # Setup B: NR7 Volatility Contraction
    if latest["NR7"] and vol_ratio < 1.1:
        return {
            "Symbol": symbol,
            "Intraday Bias": "NEUTRAL (NR7 Compression Breakout)",
            "Trigger Condition": "Trade ORB in Direction of 9:30 AM Breakout",
            "Today %": f"{day_pct:.2f}%",
            "Volume Surge": f"{vol_ratio:.1f}x (Dry Volume)",
            "Key Level to Watch": f"H: {latest['high']} | L: {latest['low']}"
        }
    return None

# ==========================================
# 4. STREAMLIT USER INTERFACE
# ==========================================
st.sidebar.title("Configuration")
upstox_token = st.sidebar.text_input("Upstox Access Token", type="password")

# Candidate universe
STOCKS = [
    {"symbol": "RELIANCE", "key": "NSE_EQ|INE002A01018", "is_fno": True},
    {"symbol": "HDFCBANK", "key": "NSE_EQ|INE040A01034", "is_fno": True},
    {"symbol": "INFY", "key": "NSE_EQ|INE009A01021", "is_fno": True},
    {"symbol": "TATASTEEL", "key": "NSE_EQ|INE081A01020", "is_fno": True},
    {"symbol": "SBIN", "key": "NSE_EQ|INE062A01020", "is_fno": True},
    {"symbol": "DIXON", "key": "NSE_EQ|INE935N01020", "is_fno": True},
    {"symbol": "KAYNES", "key": "NSE_EQ|INE918Z01012", "is_fno": False},  # Cash midcap
]

if st.button("Run Daily Analysis"):
    if not upstox_token:
        st.warning("Please supply an Upstox access token in the sidebar.")
    else:
        with st.spinner("Processing EOD candles..."):
            nifty_df = fetch_upstox_candles("NSE_INDEX|Nifty 50", upstox_token)
            nifty_ret = 0.0
            if len(nifty_df) >= 21:
                nifty_ret = ((nifty_df.iloc[-1]["close"] - nifty_df.iloc[-21]["close"]) / nifty_df.iloc[-21]["close"]) * 100
                
            swing_results, intraday_results = [], []
            
            for item in STOCKS:
                df = fetch_upstox_candles(item["key"], upstox_token)
                if not df.empty:
                    df = get_technical_indicators(df)
                    
                    # Run Strategy 1
                    s_res = run_swing_scanner(df, nifty_ret, item["symbol"], item["is_fno"])
                    if s_res:
                        swing_results.append(s_res)
                        
                    # Run Strategy 2
                    i_res = run_intraday_scanner(df, item["symbol"], item["is_fno"])
                    if i_res:
                        intraday_results.append(i_res)
            
            # Display Outputs Separately
            tab_swing, tab_intraday = st.tabs(["📌 Strategy 1: Swing Trading (3-15 Days)", "⚡ Strategy 2: Tomorrow's Intraday Watchlist"])
            
            with tab_swing:
                st.subheader(f"Nifty 50 (20-Day Baseline: {nifty_ret:.2f}%)")
                st.caption("Holds positions over several days. Prioritizes Relative Strength, Volume Expansion, and EMA alignment.")
                if swing_results:
                    st.dataframe(pd.DataFrame(swing_results), use_container_width=True)
                else:
                    st.info("No stocks met the swing trading criteria today.")
                    
            with tab_intraday:
                st.subheader("High-Probability Setups for Tomorrow's Session")
                st.caption("Purely for day trades. Do not hold overnight. Wait for the 9:15-9:30 AM 15-minute range confirmation.")
                if intraday_results:
                    st.dataframe(pd.DataFrame(intraday_results), use_container_width=True)
                else:
                    st.info("No stocks qualified for tomorrow's momentum or NR7 intraday list.")
