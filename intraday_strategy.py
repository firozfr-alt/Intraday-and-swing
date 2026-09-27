"""
End-of-Day scanner to identify high-probability intraday setups for TOMORROW.
Supports both CASH (MIS) and F&O stocks with built-in liquidity and circuit safeguards:
  1. LONG Momentum Continuation: Day % >= +2.5%, Volume >= 1.4x 20-day SMA, Close in top 25% of range.
  2. SHORT Breakdown Continuation: Day % <= -2.5%, Volume >= 1.4x 20-day SMA, Close in bottom 25% of range.
     (Tradable via MIS in Cash or Futures/Puts in F&O).
  3. NR7 Volatility Contraction: Narrowest daily range of the past 7 days -> coiled breakout.
  4. Floor Pivot Points: Tomorrow's Pivot, R1, and S1 for objective entry/stop/target planning.
"""
import pandas as pd
import numpy as np

MIN_CASH_PRICE = 50.0
MIN_CASH_DAILY_TURNOVER = 20_00_00_000  # Rs. 20 Crore minimum daily turnover for Cash stocks


def scan_for_tomorrow_intraday(df: pd.DataFrame, is_fno: bool = True) -> dict:
    if len(df) < 25:
        return {"intraday_setup": "INSUFFICIENT_DATA"}

    df = df.copy()
    df["avg_vol20"] = df["volume"].rolling(20).mean()
    df["day_range"] = df["high"] - df["low"]
    df["range_pos"] = np.where(df["day_range"] > 0, (df["close"] - df["low"]) / df["day_range"], 0.5)
    df["min_range_7"] = df["day_range"].rolling(7).min()

    # 14-period Average True Range (ATR)
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

    # Liquidity check for Cash-only stocks to avoid circuit traps
    avg_turnover = row["avg_vol20"] * row["close"]
    if not is_fno:
        if row["close"] < MIN_CASH_PRICE or avg_turnover < MIN_CASH_DAILY_TURNOVER:
            return {"intraday_setup": "NONE"}

    # Next-Day Floor Pivot Points (Classic)
    pivot = (row["high"] + row["low"] + row["close"]) / 3.0
    r1 = (2 * pivot) - row["low"]
    s1 = (2 * pivot) - row["high"]

    setup = "NONE"
    execution_plan = "-"
    segment_label = "F&O" if is_fno else "CASH (MIS Only)"

    # 1. Bullish Momentum Continuation
    if day_pct >= 2.5 and row["range_pos"] >= 0.75 and vol_mult >= 1.4:
        setup = f"LONG: Top Gainer Continuation ({segment_label})"
        execution_plan = f"Buy above 15m ORB High or {row['high']:.2f} | SL: {pivot:.2f} | Tgt: {r1:.2f}"

    # 2. Bearish Breakdown Continuation (Cash short via MIS or F&O Short)
    elif day_pct <= -2.5 and row["range_pos"] <= 0.25 and vol_mult >= 1.4:
        action_note = "Short MIS (Exit by 3:10 PM)" if not is_fno else "Short Futures / Buy Put"
        setup = f"SHORT: Top Loser Continuation ({segment_label})"
        execution_plan = f"{action_note} below 15m ORB Low or {row['low']:.2f} | SL: {pivot:.2f} | Tgt: {s1:.2f}"

    # 3. NR7 Volatility Compression Breakout
    elif is_nr7 and row["close"] >= MIN_CASH_PRICE:
        setup = f"BOTH SIDES: NR7 Breakout ({segment_label})"
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
