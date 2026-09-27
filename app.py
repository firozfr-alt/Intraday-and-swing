"""
Streamlit dashboard for two independent EOD strategies powered by live Upstox data:
  1. Strategy 1 (Swing): Weekly Breakout + EMA20 (BUY all stocks, SHORT F&O stocks only)
  2. Strategy 2 (Next-Day Intraday): Top Gainers/Losers Continuation, NR7 & Floor Pivots
"""
import json
import os
import time
from datetime import date
from urllib.parse import urlencode
import inspect
import requests
import streamlit as st
import pandas as pd
import numpy as np

import config
import instruments
import data_fetch
import strategy

# Optional import for intraday_strategy with built-in fallback
try:
    import intraday_strategy
except ModuleNotFoundError:
    class intraday_strategy:
        @staticmethod
        def scan_for_tomorrow_intraday(df: pd.DataFrame, is_fno: bool = True) -> dict:
            if len(df) < 25:
                return {"intraday_setup": "INSUFFICIENT_DATA"}
            df = df.copy()
            df["avg_vol20"] = df["volume"].rolling(20).mean()
            df["day_range"] = df["high"] - df["low"]
            df["range_pos"] = np.where(df["day_range"] > 0, (df["close"] - df["low"]) / df["day_range"], 0.5)
            df["min_range_7"] = df["day_range"].rolling(7).min()

            high_low = df["high"] - df["low"]
            high_close = (df["high"] - df["close"].shift(1)).abs()
            low_close = (df["low"] - df["close"].shift(1)).abs()
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            df["atr14"] = tr.rolling(14).mean()

            row = df.iloc[-1]
            prev = df.iloc[-2]
            day_pct = ((row["close"] - prev["close"]) / prev["close"]) * 100
            vol_mult = row["volume"] / row["avg_vol20"] if row["avg_vol20"] > 0 else 0
            is_nr7 = row["day_range"] <= row["min_range_7"]

            pivot = (row["high"] + row["low"] + row["close"]) / 3.0
            r1 = (2 * pivot) - row["low"]
            s1 = (2 * pivot) - row["high"]

            setup = "NONE"
            execution_plan = "-"
            if day_pct >= 2.5 and row["range_pos"] >= 0.75 and vol_mult >= 1.4:
                setup = "LONG: Top Gainer Continuation"
                execution_plan = f"Buy above 15m ORB High or {row['high']:.2f} | SL: {pivot:.2f} | Tgt: {r1:.2f}"
            elif day_pct <= -2.5 and row["range_pos"] <= 0.25 and vol_mult >= 1.4:
                setup = "SHORT: Top Loser Continuation"
                execution_plan = f"Sell below 15m ORB Low or {row['low']:.2f} | SL: {pivot:.2f} | Tgt: {s1:.2f}"
            elif is_nr7 and row["close"] > 100:
                setup = "BOTH SIDES: NR7 Breakout"
                execution_plan = f"Buy > {row['high']:.2f} (Tgt {r1:.2f}) OR Short < {row['low']:.2f} (Tgt {s1:.2f})"

            return {
                "intraday_setup": setup,
                "segment": "F&O" if is_fno else "CASH",
                "close": round(row["close"], 2),
                "day_pct": round(day_pct, 2),
                "close_strength_%": round(row["range_pos"] * 100, 1),
                "vol_mult": round(vol_mult, 2),
                "nr7_day": bool(is_nr7),
                "tomorrow_pivot": round(pivot, 2),
                "tomorrow_R1": round(r1, 2),
                "tomorrow_S1": round(s1, 2),
                "atr_14": round(row["atr14"], 2),
                "execution_plan": execution_plan,
                "date": row["timestamp"].date().isoformat(),
            }

# ---------------- Persistent Server Cache (Survives Browser Redirects) ----------------
CREDS_CACHE_FILE = "/tmp/upstox_runtime_creds.json"
DEFAULT_CLIENT_ID = "70ae350e-d2e3-449c-a810-db2ea377744d"
DEFAULT_REDIRECT_URI = "https://intraday-and-swing-ff4te4ohzqic6shtna5f6k.streamlit.app"


def _load_cached_creds() -> dict:
    if os.path.exists(CREDS_CACHE_FILE):
        try:
            with open(CREDS_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cached_creds(client_id: str = None, client_secret: str = None, redirect_uri: str = None, access_token: str = None) -> dict:
    data = _load_cached_creds()
    if client_id is not None:
        data["client_id"] = client_id.strip()
    if client_secret is not None:
        data["client_secret"] = client_secret.strip()
    if redirect_uri is not None:
        data["redirect_uri"] = redirect_uri.strip()
    if access_token is not None:
        data["access_token"] = access_token.strip()
        data["token_date"] = date.today().isoformat()
    try:
        with open(CREDS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass
    return data


# Sync cached credentials into config on every page load
_cached = _load_cached_creds()
config.CLIENT_ID = _cached.get("client_id") or getattr(config, "CLIENT_ID", "") or DEFAULT_CLIENT_ID
config.CLIENT_SECRET = _cached.get("client_secret") or getattr(config, "CLIENT_SECRET", "")
_raw_redirect = _cached.get("redirect_uri") or getattr(config, "REDIRECT_URI", "")
if not _raw_redirect or "127.0.0.1" in _raw_redirect:
    config.REDIRECT_URI = DEFAULT_REDIRECT_URI
else:
    config.REDIRECT_URI = _raw_redirect

if _cached.get("access_token") and _cached.get("token_date") == date.today().isoformat():
    config.ACCESS_TOKEN = _cached["access_token"]
    st.session_state["UPSTOX_ACCESS_TOKEN"] = _cached["access_token"]


def _build_login_url() -> str:
    params = {
        "response_type": "code",
        "client_id": config.CLIENT_ID.strip(),
        "redirect_uri": config.REDIRECT_URI.strip(),
    }
    return f"{config.BASE_URL}/login/authorization/dialog?{urlencode(params)}"


def _exchange_code(auth_code: str) -> dict:
    url = f"{config.BASE_URL}/login/authorization/token"
    headers = {
        "accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "code": auth_code.strip(),
        "client_id": config.CLIENT_ID.strip(),
        "client_secret": config.CLIENT_SECRET.strip(),
        "redirect_uri": config.REDIRECT_URI.strip(),
        "grant_type": "authorization_code",
    }
    resp = requests.post(url, headers=headers, data=data, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Upstox HTTP {resp.status_code}: {resp.text}")
    payload = resp.json()
    token = payload.get("access_token", "")
    if token:
        _save_cached_creds(access_token=token)
        config.ACCESS_TOKEN = token
        st.session_state["UPSTOX_ACCESS_TOKEN"] = token
        if hasattr(config, "save_access_token"):
            try:
                config.save_access_token(token)
            except Exception:
                pass
    return payload


# ---------------- Page Configuration ----------------
st.set_page_config(page_title="NSE Swing & Intraday Scanner (Upstox)", layout="wide")
st.title("📊 NSE EOD Scanner: Swing Strategy & Next-Day Intraday")
st.caption("Live data via Upstox API v2. Separate EOD engines for Multi-Day Swing and Tomorrow's Intraday.")

# ---------------- Auto-Handle ?code= from URL Redirect ----------------
url_code = st.query_params.get("code", "")
if url_code and not config.ACCESS_TOKEN:
    if config.CLIENT_SECRET:
        try:
            res = _exchange_code(url_code)
            if res.get("access_token"):
                st.success("✅ Automatically exchanged URL `?code=` for today's Upstox Access Token!")
                st.query_params.clear()
        except Exception as e:
            st.warning(f"URL code detected (`{url_code}`), but auto-exchange failed: {e}")
    else:
        st.info(f"🔑 URL Auth Code `{url_code}` detected! Enter your **UPSTOX_CLIENT_SECRET** in the sidebar and click **Save & Exchange Code**.")

# ---------------- Sidebar: Credentials, Login & Watchlist ----------------
with st.sidebar:
    st.header("1. Upstox Authentication")

    if config.ACCESS_TOKEN:
        st.success("✅ Access Token is ACTIVE for today!")
        if st.button("Clear / Reset Token"):
            _save_cached_creds(access_token="")
            config.ACCESS_TOKEN = ""
            st.session_state["UPSTOX_ACCESS_TOKEN"] = ""
            st.rerun()

    with st.expander("🔑 Step A: Save Broker API Keys (Once)", expanded=not bool(config.CLIENT_SECRET)):
        ui_client_id = st.text_input("UPSTOX_CLIENT_ID", value=config.CLIENT_ID)
        ui_client_secret = st.text_input("UPSTOX_CLIENT_SECRET", value=config.CLIENT_SECRET, type="password")
        ui_redirect_uri = st.text_input("UPSTOX_REDIRECT_URI", value=config.REDIRECT_URI)
        if st.button("💾 Save API Keys", use_container_width=True):
            _save_cached_creds(
                client_id=ui_client_id,
                client_secret=ui_client_secret,
                redirect_uri=ui_redirect_uri,
            )
            config.CLIENT_ID = ui_client_id.strip()
            config.CLIENT_SECRET = ui_client_secret.strip()
            config.REDIRECT_URI = ui_redirect_uri.strip()
            st.success("Keys saved to server cache! They will now survive browser redirects.")
            st.rerun()

    if config.CLIENT_ID and config.CLIENT_SECRET:
        login_url = _build_login_url()
        st.markdown(f"**[👉 Step B: Click here to log in to Upstox]({login_url})**")
        st.caption("After login, Upstox redirects back here and activates your token automatically, or you can paste `code=` below:")

        auth_code_input = st.text_input("Auth Code (`code=` from URL)", value=url_code)
        if st.button("Exchange code for access token", use_container_width=True):
            if not auth_code_input.strip():
                st.error("Paste the code from `?code=...` first.")
            else:
                try:
                    result = _exchange_code(auth_code_input.strip())
                    if result.get("access_token"):
                        st.query_params.clear()
                        st.success("Logged in! Access token active for today.")
                        st.rerun()
                except Exception as e:
                    st.error(f"Exchange failed: {e}")

    st.markdown("---")
    st.markdown("**OR paste an Access Token directly from Upstox Portal:**")
    manual_token = st.text_input("Direct Access Token", type="password")
    if st.button("Apply Direct Token", use_container_width=True) and manual_token:
        _save_cached_creds(access_token=manual_token.strip())
        config.ACCESS_TOKEN = manual_token.strip()
        st.session_state["UPSTOX_ACCESS_TOKEN"] = manual_token.strip()
        st.success("Direct Access Token saved and active!")
        st.rerun()

    st.divider()
    st.header("2. Watchlist & Strategy Settings")

    universe_mode = st.radio(
        "Select Watchlist Universe:",
        [
            "Custom Symbol List",
            "All NSE F&O Stocks (~180 Liquid Stocks)",
        ],
    )

    default_watchlist = (
        "RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK, SBIN, TATAMOTORS, "
        "BAJFINANCE, LT, AXISBANK, SUNPHARMA, TRENT, DIXON, BEL, HAL, CDSL, BSE"
    )
    watchlist_text = st.text_area(
        "NSE trading symbols (comma-separated)",
        value=default_watchlist,
        height=110,
        disabled=(universe_mode != "Custom Symbol List"),
    )

    bear_mode = st.checkbox(
        "Downtrend Market Mode (Relax 200-DMA for Swing Buys)",
        value=True,
    )
    force_master_refresh = st.checkbox("Force refresh NSE Instrument Master", value=False)

    run_scan = st.button("🚀 Run Both EOD Scanners", type="primary", use_container_width=True)

# ---------------- Main: Run Both Scanners ----------------
if run_scan:
    if not config.ACCESS_TOKEN:
        st.error("No Access Token active. Either complete Step A & B in the sidebar, or paste a Direct Access Token from your Upstox Developer Portal.")
        st.stop()

    with st.spinner("Loading NSE & F&O instrument master..."):
        try:
            master = instruments.load_instrument_master(force_refresh=force_master_refresh)
        except Exception as e:
            st.error(f"Could not load instrument master: {e}")
            st.stop()

    if universe_mode == "All NSE F&O Stocks (~180 Liquid Stocks)":
        if hasattr(instruments, "get_all_fno_symbols"):
            symbols = instruments.get_all_fno_symbols(master)
        elif "is_fno" in master.columns:
            symbols = sorted(master[master["is_fno"] == True]["trading_symbol"].dropna().astype(str).unique().tolist())
        else:
            symbols = []

        if not symbols:
            master = instruments.load_instrument_master(force_refresh=True)
            if "is_fno" in master.columns:
                symbols = sorted(master[master["is_fno"] == True]["trading_symbol"].dropna().astype(str).unique().tolist())
        st.info(f"Loaded **{len(symbols)}** NSE F&O stocks from instrument master.")
    else:
        symbols = [s.strip().upper() for s in watchlist_text.split(",") if s.strip()]

    swing_rows = []
    intraday_rows = []
    progress = st.progress(0.0)
    status_text = st.empty()
    total = len(symbols)

    sig_params = inspect.signature(strategy.latest_signal).parameters

    for i, sym in enumerate(symbols):
        status_text.text(f"Scanning [{i + 1}/{total}]: {sym}...")

        if hasattr(instruments, "get_instrument_info"):
            info = instruments.get_instrument_info(sym, master)
        else:
            key = instruments.get_instrument_key(sym, master)
            info = {"instrument_key": key, "is_fno": True, "name": sym} if key else None

        if not info:
            swing_rows.append({"symbol": sym, "segment": "UNKNOWN", "signal": "SYMBOL_NOT_FOUND"})
            intraday_rows.append({"symbol": sym, "segment": "UNKNOWN", "intraday_setup": "SYMBOL_NOT_FOUND"})
        else:
            try:
                candles = data_fetch.get_daily_candles(info["instrument_key"])

                if "is_fno" in sig_params and "bear_market_mode" in sig_params:
                    s_sig = strategy.latest_signal(candles, is_fno=info["is_fno"], bear_market_mode=bear_mode)
                else:
                    s_sig = strategy.latest_signal(candles)
                    if s_sig.get("signal") == "SELL" and not info["is_fno"]:
                        s_sig["signal"] = "NONE"

                s_sig["symbol"] = sym
                s_sig.setdefault("segment", "F&O" if info["is_fno"] else "CASH ONLY")
                swing_rows.append(s_sig)

                i_sig = intraday_strategy.scan_for_tomorrow_intraday(candles, is_fno=info["is_fno"])
                i_sig["symbol"] = sym
                intraday_rows.append(i_sig)

                if total > 40:
                    time.sleep(0.08)

            except Exception as e:
                swing_rows.append({"symbol": sym, "segment": "ERROR", "signal": f"ERROR: {e}"})
                intraday_rows.append({"symbol": sym, "segment": "ERROR", "intraday_setup": f"ERROR: {e}"})

        progress.progress((i + 1) / total)

    status_text.empty()
    df_swing = pd.DataFrame(swing_rows)
    df_intra = pd.DataFrame(intraday_rows)
    today_str = date.today().isoformat()

    if not df_swing.empty and "symbol" in df_swing.columns:
        lead_cols = [c for c in ["symbol", "segment", "signal"] if c in df_swing.columns]
        other_cols = [c for c in df_swing.columns if c not in lead_cols]
        df_swing = df_swing[lead_cols + other_cols]

    if not df_intra.empty and "symbol" in df_intra.columns:
        lead_cols = [c for c in ["symbol", "segment", "intraday_setup"] if c in df_intra.columns]
        other_cols = [c for c in df_intra.columns if c not in lead_cols]
        df_intra = df_intra[lead_cols + other_cols]

    tab_swing, tab_intraday = st.tabs([
        "📈 Strategy 1: Swing Trading (Weekly Breakout + EMA20)",
        "⚡ Strategy 2: Next-Day Intraday (Top Gainers/Losers & NR7)",
    ])

    with tab_swing:
        st.subheader("Multi-Day Swing Setups (Evaluated at EOD)")
        if not df_swing.empty and "signal" in df_swing.columns:
            buys = df_swing[df_swing["signal"].astype(str).str.startswith("BUY")]
            sells = df_swing[df_swing["signal"].astype(str).str.startswith("SELL")]

            col1, col2 = st.columns(2)
            with col1:
                st.success(f"🟢 SWING BUY Signals — Cash & F&O ({len(buys)})")
                st.dataframe(buys, use_container_width=True, hide_index=True) if not buys.empty else st.write("No Swing Buy breakouts today.")
            with col2:
                st.error(f"🔴 SWING SHORT Signals — F&O Only ({len(sells)})")
                st.dataframe(sells, use_container_width=True, hide_index=True) if not sells.empty else st.write("No Swing Short breakdowns today.")

            st.download_button(
                label="📥 Download Swing Watchlist CSV",
                data=df_swing.to_csv(index=False).encode("utf-8"),
                file_name=f"swing_watchlist_{today_str}.csv",
                mime="text/csv",
            )
            with st.expander("Full Swing Scan Output (All Scanned Symbols)"):
                st.dataframe(df_swing, use_container_width=True, hide_index=True)

    with tab_intraday:
        st.subheader("Tomorrow's Intraday Watchlist & Market Movers")
        valid_intra = (
            df_intra[pd.notna(df_intra.get("day_pct"))].copy()
            if not df_intra.empty and "day_pct" in df_intra.columns
            else pd.DataFrame()
        )
        if not valid_intra.empty:
            st.markdown("#### 1. Today's Top Market Movers (Momentum Context)")
            g_col, l_col = st.columns(2)
            mover_cols = [c for c in ["symbol", "segment", "close", "day_pct", "vol_mult", "close_strength_%"] if c in valid_intra.columns]
            with g_col:
                st.markdown("**🔥 Top 10 Gainers Today**")
                st.dataframe(valid_intra.sort_values("day_pct", ascending=False).head(10)[mover_cols], use_container_width=True, hide_index=True)
            with l_col:
                st.markdown("**❄️ Top 10 Losers Today**")
                st.dataframe(valid_intra.sort_values("day_pct", ascending=True).head(10)[mover_cols], use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("#### 2. Actionable Intraday Setups for Tomorrow (9:15 AM – 3:15 PM)")
            actionable_intra = valid_intra[~valid_intra["intraday_setup"].isin(["NONE", "INSUFFICIENT_DATA", "SYMBOL_NOT_FOUND"])]
            if not actionable_intra.empty:
                plan_cols = [
                    c for c in [
                        "symbol", "segment", "intraday_setup", "close", "day_pct",
                        "vol_mult", "nr7_day", "execution_plan",
                        "tomorrow_pivot", "tomorrow_R1", "tomorrow_S1", "atr_14"
                    ]
                    if c in actionable_intra.columns
                ]
                st.dataframe(actionable_intra[plan_cols], use_container_width=True, hide_index=True)
            else:
                st.info("No stocks met the strict Intraday Continuation or NR7 criteria today.")

            st.download_button(
                label="📥 Download Intraday Watchlist & Pivots CSV",
                data=df_intra.to_csv(index=False).encode("utf-8"),
                file_name=f"intraday_watchlist_{today_str}.csv",
                mime="text/csv",
            )
            with st.expander("Full Intraday Scan & Pivot Table (All Scanned Symbols)"):
                st.dataframe(df_intra, use_container_width=True, hide_index=True)
        else:
            st.warning("No valid candle data returned to compute intraday metrics.")
else:
    st.info("👈 Save your `UPSTOX_CLIENT_SECRET` once in Step A, click Step B to log in (or paste a Direct Access Token), and click **Run Both EOD Scanners**.")
