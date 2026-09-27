import pandas as pd
import numpy as np

VOL_MULT = 1.5
RANGE_POS_BUY = 0.70
RANGE_POS_SELL = 0.30
MIN_PRICE = 50
MIN_TURNOVER = 50_00_00_000  # Rs. 50 Cr


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["dma50"] = df["close"].rolling(50).mean()
    df["dma200"] = df["close"].rolling(200).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["avg_vol20"] = df["volume"].rolling(20).mean()
    df["day_range"] = df["high"] - df["low"]
    df["range_pos"] = np.where(df["day_range"] > 0, (df["close"] - df["low"]) / df["day_range"], 0.5)

    # Prior COMPLETED week's high/low
    weekly = df.set_index("timestamp").resample("W-FRI").agg(week_high=("high", "max"), week_low=("low", "min"))
    weekly["prior_week_high"] = weekly["week_high"].shift(1)
    weekly["prior_week_low"] = weekly["week_low"].shift(1)
    df["week_key"] = df["timestamp"].dt.to_period("W-FRI")
    weekly["week_key"] = weekly.index.to_period("W-FRI")
    df = df.merge(weekly[["week_key", "prior_week_high", "prior_week_low"]], on="week_key", how="left")
    return df


def latest_signal(df: pd.DataFrame, is_fno: bool = False, bear_market_mode: bool = False) -> dict:
    """
    Returns Swing signal (BUY / SELL / NONE).
    - BUY is allowed on ALL stocks (Cash + F&O).
    - SELL is allowed ONLY on F&O stocks (is_fno=True).
    """
    min_bars = 60 if bear_market_mode else 210
    if len(df) < min_bars:
        return {"signal": "INSUFFICIENT_DATA"}

    df = add_indicators(df)
    row = df.iloc[-1]
    prev = df.iloc[-2]
    day_chg_pct = ((row["close"] - prev["close"]) / prev["close"]) * 100

    # In bear_market_mode, relax 200 DMA requirement to catch short-term relative strength
    uptrend = (row["close"] > row["dma50"]) if bear_market_mode else (row["close"] > row["dma50"] and row["close"] > row["dma200"])
    downtrend = (row["close"] < row["dma50"]) if bear_market_mode else (row["close"] < row["dma50"] and row["close"] < row["dma200"])

    vol_ok = row["volume"] > row["avg_vol20"] * VOL_MULT
    liquid_ok = row["close"] > MIN_PRICE and (row["close"] * row["avg_vol20"]) > MIN_TURNOVER

    buy = (
        uptrend
        and pd.notna(row["prior_week_high"])
        and row["close"] > row["prior_week_high"]
        and row["close"] > row["ema20"]
        and row["range_pos"] > RANGE_POS_BUY
        and vol_ok
        and liquid_ok
    )

    # Shorting overnight requires F&O availability
    sell = (
        is_fno
        and downtrend
        and pd.notna(row["prior_week_low"])
        and row["close"] < row["prior_week_low"]
        and row["close"] < row["ema20"]
        and row["range_pos"] < RANGE_POS_SELL
        and vol_ok
        and liquid_ok
    )

    signal = "BUY (Cash/F&O)" if buy else "SELL (F&O Short)" if sell else "NONE"
    entry = row["high"] if buy else row["low"] if sell else None
    stop = row["low"] if buy else row["high"] if sell else None
    risk = abs(entry - stop) if entry is not None else None
    target = (entry + risk * 2) if buy else (entry - risk * 2) if sell else None

    return {
        "signal": signal,
        "segment": "F&O" if is_fno else "CASH ONLY",
        "close": round(row["close"], 2),
        "day_pct": round(day_chg_pct, 2),
        "trend": "UP" if uptrend else "DOWN" if downtrend else "SIDEWAYS",
        "prior_week_high": round(row["prior_week_high"], 2) if pd.notna(row["prior_week_high"]) else None,
        "prior_week_low": round(row["prior_week_low"], 2) if pd.notna(row["prior_week_low"]) else None,
        "vol_mult": round(row["volume"] / row["avg_vol20"], 2) if row["avg_vol20"] > 0 else 0,
        "entry": round(entry, 2) if entry is not None else None,
        "stop": round(stop, 2) if stop is not None else None,
        "target_2R": round(target, 2) if target is not None else None,
        "date": row["timestamp"].date().isoformat(),
    }
