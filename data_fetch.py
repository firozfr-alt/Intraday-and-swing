from datetime import date, timedelta
from urllib.parse import quote
import pandas as pd
import requests
import config


def get_daily_candles(instrument_key: str, lookback_days: int = 400) -> pd.DataFrame:
    encoded_key = quote(instrument_key, safe="")
    to_date = date.today().isoformat()
    from_date = (date.today() - timedelta(days=lookback_days)).isoformat()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {config.get_access_token()}",
    }

    url = f"{config.BASE_URL}/historical-candle/{encoded_key}/day/{to_date}/{from_date}"
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    candles = resp.json().get("data", {}).get("candles", [])

    # If run right after 3:30 PM, Upstox historical endpoint may not yet include today's candle.
    # Fetch today's intraday-day candle and merge if missing.
    intraday_url = f"{config.BASE_URL}/historical-candle/intraday/{encoded_key}/day"
    try:
        intra_resp = requests.get(intraday_url, headers=headers, timeout=10)
        if intra_resp.status_code == 200:
            intra_candles = intra_resp.json().get("data", {}).get("candles", [])
            if intra_candles:
                candles = intra_candles + candles
    except Exception:
        pass

    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    if df.empty:
        return df

    # Strip timezone (+05:30) so pandas weekly resampling works without tz conflicts
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    df["date_only"] = df["timestamp"].dt.date
    df = df.drop_duplicates(subset=["date_only"], keep="first").drop(columns=["date_only"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df
