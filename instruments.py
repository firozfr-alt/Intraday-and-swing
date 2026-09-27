import gzip
import json
import os
from datetime import date
import pandas as pd
import requests

CACHE_FILE = os.path.join(os.path.dirname(__file__), "instrument_cache.csv")
INSTRUMENT_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"


def _download_master() -> pd.DataFrame:
    resp = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
    resp.raise_for_status()
    raw = gzip.decompress(resp.content)
    records = json.loads(raw)
    df = pd.DataFrame(records)

    # Identify all underlying symbols that trade in NSE F&O (Futures)
    fno_symbols = set()
    if "segment" in df.columns and "underlying_symbol" in df.columns:
        fo_df = df[df["segment"] == "NSE_FO"]
        fno_symbols = set(fo_df["underlying_symbol"].dropna().str.upper().unique())

    # Keep NSE Equity cash instruments
    if "segment" in df.columns:
        eq_df = df[(df["segment"] == "NSE_EQ") & (df["instrument_type"] == "EQ")].copy()
    else:
        eq_df = df[df["instrument_type"] == "EQ"].copy()

    eq_df["trading_symbol"] = eq_df["trading_symbol"].astype(str).str.upper().str.strip()
    eq_df["is_fno"] = eq_df["trading_symbol"].isin(fno_symbols)

    keep_cols = [c for c in ["instrument_key", "trading_symbol", "name", "exchange", "is_fno"] if c in eq_df.columns]
    eq_df = eq_df[keep_cols].drop_duplicates(subset=["trading_symbol"])
    eq_df.to_csv(CACHE_FILE, index=False)
    return eq_df


def load_instrument_master(force_refresh: bool = False) -> pd.DataFrame:
    is_stale = True
    if os.path.exists(CACHE_FILE) and not force_refresh:
        modified = date.fromtimestamp(os.path.getmtime(CACHE_FILE))
        is_stale = modified != date.today()
    if is_stale:
        try:
            return _download_master()
        except Exception as e:
            if os.path.exists(CACHE_FILE):
                return pd.read_csv(CACHE_FILE)
            raise e
    return pd.read_csv(CACHE_FILE)


def get_instrument_info(trading_symbol: str, master: pd.DataFrame | None = None) -> dict | None:
    master = master if master is not None else load_instrument_master()
    match = master[master["trading_symbol"].str.upper() == trading_symbol.upper()]
    if match.empty:
        return None
    row = match.iloc[0]
    return {
        "instrument_key": row["instrument_key"],
        "is_fno": bool(row.get("is_fno", False)),
        "name": row.get("name", trading_symbol),
    }
