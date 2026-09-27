"""
End-of-Day scanner to select stocks for NEXT DAY Intraday trading.
Identifies three distinct setups:
1. Momentum Continuation (Top Gainers closing in top 20% of range with volume surge)
2. Breakdown Continuation (Top Losers closing in bottom 20% of range with volume surge)
3. NR7 Contraction Breakout (Narrowest daily range of last 7 days -> coiled spring for ORB)
"""
import pandas as pd
import numpy as np


def scan_for_tomorrow_intraday(df: pd.DataFrame, is_fno: bool = True) -> dict:
    if len(df) < 25:
        return {"intraday_setup": "INSUFFICIENT_DATA"}

    df = df.copy()
    df["avg_vol20"] = df["volume"].rolling(20).mean()
    df["day_range"] = df["high"] - df["low"]
    df["range_pos"] = np.where(df["day_range"] > 0, (df["close"] - df["low"]) / df["day_range"], 0.5)
    df["min_range_7"] = df["day_range"].rolling(7).min()

    # ATR(14) for realistic intraday stop-loss and targets
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

    # Next-day Floor Pivot Points
    pivot = (row["high"] + row["low"] + row["close"]) / 3.0
    r1 = (2 * pivot) - row["low"]
    s1 = (2 * pivot) - row["high"]

    setup = "NONE"
    execution_plan = "-"

    # 1. Bullish Momentum Continuation (Strong Gainer + Strong Close + High Volume)
    if day_pct >= 2.5 and row["range_pos"] >= 0.75 and vol_mult >= 1.4:
        setup = "LONG: Top Gainer Continuation"
        execution_plan = f"Buy above 15m ORB High or {row['high']:.2f} | SL: {pivot:.2f} | Tgt: {r1:.2f}"

    # 2. Bearish Breakdown Continuation (Strong Loser + Weak Close + High Volume)
    elif day_pct <= -2.5 and row["range_pos"] <= 0.25 and vol_mult >= 1.4:
        setup = "SHORT: Top Loser Continuation"
        execution_plan = f"Sell below 15m ORB Low or {row['low']:.2f} | SL: {pivot:.2f} | Tgt: {s1:.2f}"

    # 3. NR7 Volatility Compression (Trade breakout either side tomorrow)
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
