"""
Upstox identifies every tradable symbol by an `instrument_key` (e.g. "NSE_EQ|INE002A01018"),
not by its trading symbol. This module downloads Upstox's public instrument master file once
a day, tags each equity with `is_fno` (True if it trades in NSE F&O, False if Cash-only),
and caches it locally.
"""
import gzip
import json
import os
from datetime import date
import pandas as pd
import requests

CACHE_FILE = os.path.join(os.path.dirname(__file__), "instrument_cache.csv")

# Primary URL includes both NSE_EQ and NSE_FO so we can detect F&O stocks automatically.
COMPLETE_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
FALLBACK_NSE_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


def _download_master() -> pd.DataFrame:
    try:
        resp = requests.get(COMPLETE_MASTER_URL, timeout=60)
        resp.raise_for_status()
    except Exception:
        resp = requests.get(FALLBACK_NSE_URL, timeout=60)
        resp.raise_for_status()

    raw = gzip.decompress(resp.content)
    records = json.loads(raw)
    df = pd.DataFrame(records)

    # 1. Extract all underlying symbols that have active NSE F&O contracts
    fno_symbols = set()
    if "segment" in df.columns:
        fo_df = df[df["segment"] == "NSE_FO"]
        if "underlying_symbol" in fo_df.columns:
            fno_symbols.update(fo_df["underlying_symbol"].dropna().astype(str).str.upper().str.strip().unique())
        if "name" in fo_df.columns and not fno_symbols:
            fno_symbols.update(fo_df["name"].dropna().astype(str).str.upper().str.strip().unique())
    elif "instrument_type" in df.columns:
        fo_df = df[df["instrument_type"].isin(["FUT", "CE", "PE", "FUTSTK", "OPTSTK"])]
        if "underlying_symbol" in fo_df.columns:
            fno_symbols.update(fo_df["underlying_symbol"].dropna().astype(str).str.upper().str.strip().unique())

    # 2. Filter to NSE Equity Cash-Market instruments (EQ)
    if "segment" in df.columns and "instrument_type" in df.columns:
        eq_df = df[(df["segment"] == "NSE_EQ") & (df["instrument_type"] == "EQ")].copy()
    elif "instrument_type" in df.columns:
        eq_df = df[df["instrument_type"] == "EQ"].copy()
    else:
        eq_df = df.copy()

    eq_df["trading_symbol"] = eq_df["trading_symbol"].astype(str).str.upper().str.strip()
    eq_df["is_fno"] = eq_df["trading_symbol"].isin(fno_symbols)

    keep_cols = [
        c for c in ["instrument_key", "trading_symbol", "name", "exchange", "is_fno"]
        if c in eq_df.columns
    ]
    eq_df = eq_df[keep_cols].drop_duplicates(subset=["trading_symbol"]).reset_index(drop=True)
    eq_df.to_csv(CACHE_FILE, index=False)
    return eq_df


def load_instrument_master(force_refresh: bool = False) -> pd.DataFrame:
    """
    Loads the daily cached instrument master CSV, or downloads a fresh copy from Upstox
    if today's cache does not exist, is older than today, or lacks the `is_fno` column.
    """
    is_stale = True
    if os.path.exists(CACHE_FILE) and not force_refresh:
        modified = date.fromtimestamp(os.path.getmtime(CACHE_FILE))
        is_stale = modified != date.today()
        if not is_stale:
            try:
                cached_df = pd.read_csv(CACHE_FILE)
                if "is_fno" in cached_df.columns:
                    return cached_df
                is_stale = True  # Re-download if old cache doesn't have the `is_fno` flag
            except Exception:
                is_stale = True

    if is_stale:
        try:
            return _download_master()
        except Exception as e:
            if os.path.exists(CACHE_FILE):
                print(f"Warning: instrument master refresh failed ({e}); using cached file.")
                fallback_df = pd.read_csv(CACHE_FILE)
                if "is_fno" not in fallback_df.columns:
                    fallback_df["is_fno"] = True
                return fallback_df
            raise e

    return pd.read_csv(CACHE_FILE)


def get_instrument_info(trading_symbol: str, master: pd.DataFrame | None = None) -> dict | None:
    """
    Looks up a stock by its NSE trading symbol (e.g. 'RELIANCE') and returns a dict with:
    {'instrument_key': str, 'is_fno': bool, 'name': str}
    """
    master = master if master is not None else load_instrument_master()
    match = master[master["trading_symbol"].astype(str).str.upper() == trading_symbol.strip().upper()]
    if match.empty:
        return None
    row = match.iloc[0]
    return {
        "instrument_key": str(row["instrument_key"]),
        "is_fno": bool(row.get("is_fno", False)),
        "name": str(row.get("name", trading_symbol)),
    }


def get_instrument_key(trading_symbol: str, master: pd.DataFrame | None = None) -> str | None:
    """Backward-compatible helper that returns just the `instrument_key` string."""
    info = get_instrument_info(trading_symbol, master)
    return info["instrument_key"] if info else None


def get_all_fno_symbols(master: pd.DataFrame | None = None) -> list[str]:
    """Returns a sorted list of all NSE F&O underlying trading symbols."""
    master = master if master is not None else load_instrument_master()
    if "is_fno" not in master.columns:
        return []
    fno_df = master[master["is_fno"] == True]
    return sorted(fno_df["trading_symbol"].dropna().astype(str).unique().tolist())
