"""
Standalone EOD CLI Scanner for Swing Trading & Next-Day Intraday Watchlists.
Reads credentials/token from .env via config.py (no Streamlit required).

Usage examples:
  1. Scan default liquid watchlist:
     python eod_next_day_watchlist.py

  2. Scan ALL ~180 NSE F&O stocks automatically:
     python eod_next_day_watchlist.py --all-fno

  3. Scan custom symbols with relaxed 200-DMA bear-market filter:
     python eod_next_day_watchlist.py --symbols RELIANCE,TCS,DIXON,CDSL --bear-mode

  4. Interactive CLI login (if today's access token is not yet in .env):
     python eod_next_day_watchlist.py --login
"""
import argparse
import os
import sys
import time
from datetime import date
import pandas as pd

import config
import upstox_auth
import instruments
import data_fetch
import strategy
import intraday_strategy

DEFAULT_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN",
    "TATAMOTORS", "BAJFINANCE", "LT", "AXISBANK", "SUNPHARMA",
    "TRENT", "DIXON", "BEL", "HAL", "CDSL", "BSE", "TATASTEEL"
]


def cli_login_flow() -> None:
    """Interactive OAuth2 login from the terminal if token is missing or expired."""
    if not config.credentials_present():
        print("ERROR: Missing UPSTOX_CLIENT_ID / UPSTOX_CLIENT_SECRET in .env.")
        sys.exit(1)

    login_url = upstox_auth.build_login_url()
    print("\n--- Upstox Daily CLI Login ---")
    print(f"1. Open this URL in your browser and log in:\n   {login_url}\n")
    auth_code = input("2. Paste the 'code=' value from the redirected URL: ").strip()
    if not auth_code:
        print("Aborted: No authorization code provided.")
        sys.exit(1)

    try:
        payload = upstox_auth.exchange_code_for_token(auth_code)
        token = payload.get("access_token", "")
        if token:
            print("Success! Access token saved to .env for today.\n")
        else:
            print(f"Login failed: {payload}")
            sys.exit(1)
    except Exception as e:
        print(f"Error exchanging code for token: {e}")
        sys.exit(1)


def resolve_watchlist(args, master: pd.DataFrame) -> list[str]:
    """Determines which NSE symbols to scan based on CLI flags."""
    if args.all_fno:
        if "is_fno" in master.columns:
            fno_list = master[master["is_fno"] == True]["trading_symbol"].dropna().unique().tolist()
            print(f"Loaded {len(fno_list)} NSE F&O stocks from instrument master.")
            return sorted(fno_list)
        else:
            print("Warning: 'is_fno' column missing in cached master. Refreshing master...")
            master = instruments.load_instrument_master(force_refresh=True)
            return sorted(master[master["is_fno"] == True]["trading_symbol"].dropna().unique().tolist())

    if args.symbols:
        return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    return DEFAULT_SYMBOLS


def main():
    parser = argparse.ArgumentParser(description="Upstox EOD Swing & Next-Day Intraday Scanner")
    parser.add_argument("--login", action="store_true", help="Run interactive Upstox OAuth login first")
    parser.add_argument("--token", type=str, default="", help="Pass Upstox access token directly via CLI")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated NSE trading symbols")
    parser.add_argument("--all-fno", action="store_true", help="Scan all NSE F&O stocks automatically")
    parser.add_argument("--bear-mode", action="store_true", default=True, help="Relax 200-DMA filter for Swing Buys")
    parser.add_argument("--risk", type=float, default=1500.0, help="Rupee risk per intraday trade (default: 1500)")
    parser.add_argument("--out-dir", type=str, default="output", help="Folder to save EOD CSV watchlists")
    args = parser.parse_args()

    # 1. Handle Token & Login
    if args.token:
        config.save_access_token(args.token.strip())
    if args.login or not config.get_access_token():
        cli_login_flow()

    # 2. Load Instrument Master
    print("Loading Upstox NSE instrument master...")
    master = instruments.load_instrument_master()
    symbols = resolve_watchlist(args, master)

    swing_results = []
    intraday_results = []
    total = len(symbols)

    print(f"Scanning {total} symbols (Bear Mode: {args.bear_mode} | Intraday Risk: Rs.{args.risk:,.0f})...\n")

    for idx, sym in enumerate(symbols, start=1):
        info = instruments.get_instrument_info(sym, master)
        if not info:
            print(f"[{idx}/{total}] {sym}: Symbol not found in NSE master")
            continue

        try:
            candles = data_fetch.get_daily_candles(info["instrument_key"])
            if candles.empty:
                print(f"[{idx}/{total}] {sym}: No candle data returned")
                continue

            # Strategy 1: Swing Trading (Buy All / Short F&O Only)
            s_sig = strategy.latest_signal(candles, is_fno=info["is_fno"], bear_market_mode=args.bear_mode)
            s_sig["symbol"] = sym
            swing_results.append(s_sig)

            # Strategy 2: Next-Day Intraday (Momentum + NR7/CPR + Floor Pivots)
            i_sig = intraday_strategy.scan_for_tomorrow_intraday(
                candles, is_fno=info["is_fno"], risk_per_trade=args.risk
            )
            i_sig["symbol"] = sym
            intraday_results.append(i_sig)

            print(f"[{idx}/{total}] {sym:<12} | Swing: {s_sig['signal']:<18} | Intraday: {i_sig['intraday_setup']}")

            # Polite rate-limiting when scanning 150+ F&O stocks
            if total > 30:
                time.sleep(0.12)

        except Exception as e:
            print(f"[{idx}/{total}] {sym}: Error fetching/analyzing ({e})")

    df_swing = pd.DataFrame(swing_results)
    df_intra = pd.DataFrame(intraday_results)
    today_str = date.today().isoformat()
    os.makedirs(args.out_dir, exist_ok=True)

    # ==========================================================
    # REPORT 1: SWING TRADING OUTPUT
    # ==========================================================
    print("\n" + "=" * 80)
    print(f"📈 STRATEGY 1: EOD SWING TRADING SETUPS ({today_str})")
    print("=" * 80)
    if not df_swing.empty and "signal" in df_swing.columns:
        actionable_swing = df_swing[df_swing["signal"].astype(str).str.startswith(("BUY", "SELL"))]
        cols = ["symbol", "segment", "signal", "close", "day_pct", "vol_mult", "entry", "stop", "target_2R"]
        cols = [c for c in cols if c in actionable_swing.columns]
        if not actionable_swing.empty:
            print(actionable_swing[cols].to_string(index=False))
        else:
            print("No Swing BUY or F&O SHORT breakouts triggered today.")

        swing_csv = os.path.join(args.out_dir, f"swing_watchlist_{today_str}.csv")
        df_swing.to_csv(swing_csv, index=False)
        print(f"\nSaved full swing scan to: {swing_csv}")

    # ==========================================================
    # REPORT 2: NEXT-DAY INTRADAY OUTPUT
    # ==========================================================
    print("\n" + "=" * 80)
    print(f"⚡ STRATEGY 2: TOMORROW'S INTRADAY WATCHLIST & TOP MOVERS ({today_str})")
    print("=" * 80)
    if not df_intra.empty and "day_pct" in df_intra.columns:
        valid_intra = df_intra[pd.notna(df_intra["day_pct"])].copy()

        print("\n--- 🔥 Today's Top 5 Gainers (day_pct > 0%) ---")
        gainers = valid_intra[valid_intra["day_pct"] > 0].sort_values("day_pct", ascending=False).head(5)
        if not gainers.empty:
            print(gainers[["symbol", "segment", "close", "day_pct", "vol_mult", "close_strength_%", "atr_ratio"]].to_string(index=False))
        else:
            print("No positive gainers today.")

        print("\n--- ❄️ Today's Top 5 Losers (day_pct < 0%) ---")
        losers = valid_intra[valid_intra["day_pct"] < 0].sort_values("day_pct", ascending=True).head(5)
        if not losers.empty:
            print(losers[["symbol", "segment", "close", "day_pct", "vol_mult", "close_strength_%", "atr_ratio"]].to_string(index=False))
        else:
            print("No negative losers today.")

        print("\n--- 🎯 Actionable Intraday Setups for Tomorrow (9:15 AM ORB / CPR / Pivots) ---")
        actionable_intra = valid_intra[~valid_intra["intraday_setup"].isin(["NONE", "INSUFFICIENT_DATA"])]
        intra_cols = [
            "symbol", "segment", "intraday_setup", "close", "day_pct",
            "vol_mult", "cpr_width_%", "suggested_qty", "execution_plan"
        ]
        intra_cols = [c for c in intra_cols if c in actionable_intra.columns]
        if not actionable_intra.empty:
            print(actionable_intra[intra_cols].to_string(index=False))
        else:
            print("No stocks met the strict Intraday Continuation or NR7/CPR criteria today.")

        intra_csv = os.path.join(args.out_dir, f"intraday_watchlist_{today_str}.csv")
        df_intra.to_csv(intra_csv, index=False)
        print(f"\nSaved full intraday pivot table to: {intra_csv}")


if __name__ == "__main__":
    main()
