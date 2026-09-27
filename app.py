"""
Streamlit dashboard for two independent EOD strategies powered by live Upstox data:
  1. Strategy 1 (Swing): Weekly Breakout + EMA20 (BUY all stocks, SHORT F&O stocks only)
  2. Strategy 2 (Next-Day Intraday): Top Gainers/Losers Continuation, NR7 & Floor Pivots

Run locally with:  streamlit run app.py
"""
import time
from datetime import date
import streamlit as st
import pandas as pd

import config
import upstox_auth
import instruments
import data_fetch
import strategy
import intraday_strategy

st.set_page_config(page_title="NSE Swing & Intraday Scanner (Upstox)", layout="wide")
st.title("📊 NSE EOD Scanner: Swing Strategy & Next-Day Intraday")
st.caption("Live data via Upstox API v2. Separate EOD engines for Multi-Day Swing and Tomorrow's Intraday.")

# ---------------- Sidebar: Credentials, Login & Watchlist ----------------
with st.sidebar:
    st.header("1. Upstox Authentication")
    st.write("Access tokens expire daily. Authenticate once before running your EOD scan.")

    with st.expander("🔑 Broker API Keys (Optional if in Secrets/.env)", expanded=not config.credentials_present()):
        ui_client_id = st.text_input("UPSTOX_CLIENT_ID", value=config.CLIENT_ID, type="password")
        ui_client_secret = st.text_input("UPSTOX_CLIENT_SECRET", value=config.CLIENT_SECRET, type="password")
        ui_redirect_uri = st.text_input("UPSTOX_REDIRECT_URI", value=config.REDIRECT_URI)
        if st.button("Apply API Keys"):
            config.set_runtime_credentials(ui_client_id, ui_client_secret, ui_redirect_uri)
            st.success("API keys updated for this session.")

    if config.credentials_present():
        login_url = upstox_auth.build_login_url()
        st.markdown(f"**[Step 1: Click here to log in to Upstox]({login_url})**")
        st.write("Step 2: Copy the `code=` value from the redirected URL and paste below.")
        auth_code = st.text_input("Paste auth code here", type="password")
        if st.button("Exchange code for access token"):
            try:
                result = upstox_auth.exchange_code_for_token(auth_code.strip())
                if result.get("access_token"):
                    st.success("Logged in! Access token active for today.")
                else:
                    st.error(f"No access_token in response: {result}")
            except Exception as e:
                st.error(f"Login failed: {e}")
    else:
        st.warning("Enter your UPSTOX_CLIENT_ID & SECRET above, or paste a Direct Access Token below.")

    st.markdown("**OR paste an existing Access Token directly:**")
    manual_token = st.text_input("Direct Access Token", type="password")
    if manual_token:
        config.save_access_token(manual_token.strip())
        st.success("Direct Access Token saved in session.")

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
        height=120,
        disabled=(universe_mode != "Custom Symbol List"),
    )

    bear_mode = st.checkbox(
        "Downtrend Market Mode (Relax 200-DMA for Swing Buys)",
        value=True,
        help="When checked, Swing BUY setups require price > 50 DMA & 20 EMA (skipping 200 DMA) to catch early relative-strength breakouts in a correcting market.",
    )
    force_master_refresh = st.checkbox("Force refresh NSE Instrument Master", value=False)

    run_scan = st.button("🚀 Run Both EOD Scanners", type="primary", use_container_width=True)

# ---------------- Main: Run Both Scanners ----------------
if run_scan:
    if not config.get_access_token():
        st.error("No access token found. Complete the login step or paste your token in the sidebar first.")
        st.stop()

    with st.spinner("Loading NSE & F&O instrument master..."):
        try:
            master = instruments.load_instrument_master(force_refresh=force_master_refresh)
        except Exception as e:
            st.error(f"Could not load instrument master: {e}")
            st.stop()

    if universe_mode == "All NSE F&O Stocks (~180 Liquid Stocks)":
        symbols = instruments.get_all_fno_symbols(master)
        if not symbols:
            st.warning("No F&O symbols detected in cache; forcing a fresh download of the master list...")
            master = instruments.load_instrument_master(force_refresh=True)
            symbols = instruments.get_all_fno_symbols(master)
        st.info(f"Loaded **{len(symbols)}** NSE F&O stocks from instrument master.")
    else:
        symbols = [s.strip().upper() for s in watchlist_text.split(",") if s.strip()]

    if not symbols:
        st.warning("Watchlist is empty. Enter at least one symbol.")
        st.stop()

    swing_rows = []
    intraday_rows = []
    progress = st.progress(0.0)
    status_text = st.empty()
    total = len(symbols)

    for i, sym in enumerate(symbols):
        status_text.text(f"Scanning [{i + 1}/{total}]: {sym}...")
        info = instruments.get_instrument_info(sym, master)

        if not info:
            swing_rows.append({"symbol": sym, "segment": "UNKNOWN", "signal": "SYMBOL_NOT_FOUND"})
            intraday_rows.append({"symbol": sym, "segment": "UNKNOWN", "intraday_setup": "SYMBOL_NOT_FOUND"})
        else:
            try:
                candles = data_fetch.get_daily_candles(info["instrument_key"])

                # 1. Evaluate Swing Strategy (BUY all stocks, SELL F&O stocks only)
                s_sig = strategy.latest_signal(
                    candles,
                    is_fno=info["is_fno"],
                    bear_market_mode=bear_mode,
                )
                s_sig["symbol"] = sym
                swing_rows.append(s_sig)

                # 2. Evaluate Next-Day Intraday Strategy (Top Gainers/Losers, NR7, Pivots)
                i_sig = intraday_strategy.scan_for_tomorrow_intraday(
                    candles,
                    is_fno=info["is_fno"],
                )
                i_sig["symbol"] = sym
                intraday_rows.append(i_sig)

                # Light rate-limit delay when scanning the full F&O universe
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

    # Reorder `symbol` and `segment` to be the first columns
    if not df_swing.empty and "symbol" in df_swing.columns:
        lead_cols = [c for c in ["symbol", "segment", "signal"] if c in df_swing.columns]
        other_cols = [c for c in df_swing.columns if c not in lead_cols]
        df_swing = df_swing[lead_cols + other_cols]

    if not df_intra.empty and "symbol" in df_intra.columns:
        lead_cols = [c for c in ["symbol", "segment", "intraday_setup"] if c in df_intra.columns]
        other_cols = [c for c in df_intra.columns if c not in lead_cols]
        df_intra = df_intra[lead_cols + other_cols]

    # ---------------- Display Separate Strategy Tabs ----------------
    tab_swing, tab_intraday = st.tabs([
        "📈 Strategy 1: Swing Trading (Weekly Breakout + EMA20)",
        "⚡ Strategy 2: Next-Day Intraday (Top Gainers/Losers & NR7)",
    ])

    # ================= TAB 1: SWING TRADING =================
    with tab_swing:
        st.subheader("Multi-Day Swing Setups (Evaluated at EOD)")
        st.caption("Rules: **BUY** signals apply to all stocks (Cash & F&O). **SELL/SHORT** signals are strictly restricted to F&O stocks.")

        if not df_swing.empty and "signal" in df_swing.columns:
            buys = df_swing[df_swing["signal"].astype(str).str.startswith("BUY")]
            sells = df_swing[df_swing["signal"].astype(str).str.startswith("SELL")]

            col1, col2 = st.columns(2)
            with col1:
                st.success(f"🟢 SWING BUY Signals — Cash & F&O ({len(buys)})")
                if not buys.empty:
                    st.dataframe(buys, use_container_width=True, hide_index=True)
                else:
                    st.write("No Swing Buy breakouts triggered today.")

            with col2:
                st.error(f"🔴 SWING SHORT Signals — F&O Only ({len(sells)})")
                if not sells.empty:
                    st.dataframe(sells, use_container_width=True, hide_index=True)
                else:
                    st.write("No Swing Short breakdowns triggered today.")

            st.download_button(
                label="📥 Download Swing Watchlist CSV",
                data=df_swing.to_csv(index=False).encode("utf-8"),
                file_name=f"swing_watchlist_{today_str}.csv",
                mime="text/csv",
            )

            with st.expander("Full Swing Scan Output (All Scanned Symbols)"):
                st.dataframe(df_swing, use_container_width=True, hide_index=True)

    # ================= TAB 2: NEXT-DAY INTRADAY =================
    with tab_intraday:
        st.subheader("Tomorrow's Intraday Watchlist & Market Movers")
        st.caption("Identifies today's strongest Gainers, weakest Losers, and NR7 coiled setups with pre-calculated Floor Pivots for tomorrow morning.")

        valid_intra = (
            df_intra[pd.notna(df_intra.get("day_pct"))].copy()
            if not df_intra.empty and "day_pct" in df_intra.columns
            else pd.DataFrame()
        )

        if not valid_intra.empty:
            st.markdown("#### 1. Today's Top Market Movers (Momentum Context)")
            g_col, l_col = st.columns(2)
            mover_cols = [
                c for c in ["symbol", "segment", "close", "day_pct", "vol_mult", "close_strength_%"]
                if c in valid_intra.columns
            ]

            with g_col:
                st.markdown("**🔥 Top 10 Gainers Today**")
                top_gainers = valid_intra.sort_values("day_pct", ascending=False).head(10)
                st.dataframe(top_gainers[mover_cols], use_container_width=True, hide_index=True)

            with l_col:
                st.markdown("**❄️ Top 10 Losers Today**")
                top_losers = valid_intra.sort_values("day_pct", ascending=True).head(10)
                st.dataframe(top_losers[mover_cols], use_container_width=True, hide_index=True)

            st.divider()
            st.markdown("#### 2. Actionable Intraday Setups for Tomorrow (9:15 AM – 3:15 PM)")
            actionable_intra = valid_intra[
                ~valid_intra["intraday_setup"].isin(["NONE", "INSUFFICIENT_DATA", "SYMBOL_NOT_FOUND"])
            ]

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
    st.info("👈 Authenticate in the sidebar, choose your watchlist universe, and click **Run Both EOD Scanners**.")
