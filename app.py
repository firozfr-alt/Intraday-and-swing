import streamlit as st
import pandas as pd
import config
import upstox_auth
import instruments
import data_fetch
import strategy
import intraday_strategy

st.set_page_config(page_title="Upstox EOD Swing & Intraday Scanner", layout="wide")
st.title("📊 NSE EOD Scanner: Swing Strategy & Next-Day Intraday")
st.caption("Live data via Upstox API v2. Separate EOD engines for Multi-Day Swing and Tomorrow's Intraday.")

# ---------------- Sidebar: Credentials & Login ----------------
with st.sidebar:
    st.header("1. Upstox Authentication")

    with st.expander("🔑 Enter Broker API Keys (Optional if in Secrets/.env)", expanded=not config.credentials_present()):
        ui_client_id = st.text_input("UPSTOX_CLIENT_ID", value=config.CLIENT_ID, type="password")
        ui_client_secret = st.text_input("UPSTOX_CLIENT_SECRET", value=config.CLIENT_SECRET, type="password")
        ui_redirect_uri = st.text_input("UPSTOX_REDIRECT_URI", value=config.REDIRECT_URI)
        if st.button("Apply API Keys"):
            config.set_runtime_credentials(ui_client_id, ui_client_secret, ui_redirect_uri)
            st.success("API keys updated in memory.")

    if config.credentials_present():
        login_url = upstox_auth.build_login_url()
        st.markdown(f"**[Step 1: Click here to log in to Upstox]({login_url})**")
        auth_code = st.text_input("Step 2: Paste `code=` from redirect URL", type="password")
        if st.button("Exchange code for token"):
            try:
                result = upstox_auth.exchange_code_for_token(auth_code.strip())
                if result.get("access_token"):
                    st.success("Token active for today!")
            except Exception as e:
                st.error(f"Login failed: {e}")

    st.markdown("**OR paste an existing Access Token directly:**")
    manual_token = st.text_input("Direct Access Token", type="password")
    if manual_token:
        config.save_access_token(manual_token.strip())
        st.success("Direct Access Token active.")

    st.divider()
    st.header("2. Watchlist & Filters")
    default_watchlist = (
        "RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK, SBIN, TATAMOTORS, "
        "BAJFINANCE, LT, AXISBANK, SUNPHARMA, NTPCBANK, TRENT, DIXON, BEL, HAL, CDSL, BSE"
    )
    watchlist_text = st.text_area("NSE trading symbols (comma-separated)", value=default_watchlist, height=130)
    bear_mode = st.checkbox("Downtrend Market Mode (Relax 200-DMA for Swing Buys)", value=True)
    run_scan = st.button("🚀 Run Both EOD Scanners", type="primary", use_container_width=True)

# ---------------- Main Execution ----------------
if run_scan:
    if not config.get_access_token():
        st.error("No Access Token found. Log in or paste your token in the sidebar first.")
        st.stop()

    symbols = [s.strip().upper() for s in watchlist_text.split(",") if s.strip()]
    with st.spinner("Loading NSE & F&O instrument master..."):
        try:
            master = instruments.load_instrument_master()
        except Exception as e:
            st.error(f"Could not load instrument master: {e}")
            st.stop()

    swing_rows = []
    intraday_rows = []
    progress = st.progress(0.0)

    for i, sym in enumerate(symbols):
        info = instruments.get_instrument_info(sym, master)
        if not info:
            swing_rows.append({"symbol": sym, "signal": "SYMBOL_NOT_FOUND"})
        else:
            try:
                candles = data_fetch.get_daily_candles(info["instrument_key"])
                # Run Strategy 1: Swing
                s_sig = strategy.latest_signal(candles, is_fno=info["is_fno"], bear_market_mode=bear_mode)
                s_sig["symbol"] = sym
                swing_rows.append(s_sig)

                # Run Strategy 2: Next-Day Intraday
                i_sig = intraday_strategy.scan_for_tomorrow_intraday(candles, is_fno=info["is_fno"])
                i_sig["symbol"] = sym
                intraday_rows.append(i_sig)
            except Exception as e:
                swing_rows.append({"symbol": sym, "signal": f"ERROR: {e}"})
        progress.progress((i + 1) / len(symbols))

    df_swing = pd.DataFrame(swing_rows)
    df_intra = pd.DataFrame(intraday_rows)

    tab_swing, tab_intraday = st.tabs([
        "📈 Strategy 1: Swing Trading (Weekly Breakout + EMA20)",
        "⚡ Strategy 2: Next-Day Intraday (Top Gainers/Losers & NR7)"
    ])

    # --- TAB 1: SWING STRATEGY ---
    with tab_swing:
        st.markdown("#### Rules: BUY = All Stocks (Cash + F&O) | SELL = F&O Stocks Only")
        buys = df_swing[df_swing["signal"].astype(str).str.startswith("BUY")] if "signal" in df_swing else pd.DataFrame()
        sells = df_swing[df_swing["signal"].astype(str).str.startswith("SELL")] if "signal" in df_swing else pd.DataFrame()

        c1, c2 = st.columns(2)
        with c1:
            st.success(f"🟢 SWING BUY Setups ({len(buys)})")
            st.dataframe(buys, use_container_width=True) if not buys.empty else st.write("No Swing Buy breakouts today.")
        with c2:
            st.error(f"🔴 SWING SHORT Setups - F&O Only ({len(sells)})")
            st.dataframe(sells, use_container_width=True) if not sells.empty else st.write("No Swing Short breakdowns today.")

        with st.expander("View Full Swing Scan Table"):
            st.dataframe(df_swing, use_container_width=True)

    # --- TAB 2: INTRADAY NEXT-DAY STRATEGY ---
    with tab_intraday:
        if not df_intra.empty and "day_pct" in df_intra.columns:
            st.markdown("#### 1. Today's Market Movers (Context for Tomorrow)")
            g1, g2 = st.columns(2)
            with g1:
                st.markdown("**🔥 Today's Top Gainers**")
                st.dataframe(df_intra.sort_values("day_pct", ascending=False).head(5)[["symbol", "segment", "close", "day_pct", "vol_mult", "close_strength_%"]], use_container_width=True)
            with g2:
                st.markdown("**❄️ Today's Top Losers**")
                st.dataframe(df_intra.sort_values("day_pct", ascending=True).head(5)[["symbol", "segment", "close", "day_pct", "vol_mult", "close_strength_%"]], use_container_width=True)

            st.divider()
            st.markdown("#### 2. Actionable Intraday Watchlist for Tomorrow (9:15 AM - 3:15 PM)")
            actionable = df_intra[~df_intra["intraday_setup"].isin(["NONE", "INSUFFICIENT_DATA"])]
            if not actionable.empty:
                st.dataframe(actionable[["symbol", "segment", "intraday_setup", "close", "day_pct", "vol_mult", "execution_plan", "tomorrow_pivot", "tomorrow_R1", "tomorrow_S1"]], use_container_width=True)
            else:
                st.info("No stocks met the strict Intraday Momentum Continuation or NR7 criteria today.")

            with st.expander("View Full Intraday Metrics & Tomorrow's Pivot Table"):
                st.dataframe(df_intra, use_container_width=True)
else:
    st.info("👈 Authenticate in the sidebar, review your watchlist, and click **Run Both EOD Scanners**.")
