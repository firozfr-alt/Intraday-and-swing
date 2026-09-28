"""
End-of-Day scanner to identify high-probability intraday setups for TOMORROW.
Supports both CASH (MIS) and F&O stocks with built-in liquidity and circuit safeguards:
  1. LONG Momentum Continuation: Day % >= +2.0%, Vol >= 1.4x SMA20, Close in top 25% of range,
     Close > EMA20, and not ATR-exhausted (ATR Ratio <= 2.2x).
  2. SHORT Breakdown Continuation: Day % <= -2.0%, Vol >= 1.4x SMA20, Close in bottom 25% of range,
     Close < EMA20, and ATR Ratio <= 2.5x. (Tradable via MIS in Cash or Futures/Puts in F&O).
  3. NR7 + Tight CPR / Inside Day (ID/NR7) Volatility Contraction: Coiled breakout setups.
  4. Floor Pivots + Central Pivot Range (CPR) + PDH/PDL + Auto Position Sizing.
"""
import pandas as pd
import numpy as np

MIN_CASH_PRICE = 50.0
MIN_CASH_DAILY_TURNOVER = 20_00_00_000  # Rs. 20 Crore minimum daily turnover for Cash stocks


def scan_for_tomorrow_intraday(
    df: pd.DataFrame,
    is_fno: bool = True,
    risk_per_trade: float = 1000.0,
) -> dict:
    if df is None or len(df) < 25:
        return {"intraday_setup": "INSUFFICIENT_DATA"}

    df = df.copy()
    df["avg_vol20"] = df["volume"].rolling(20).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["day_range"] = df["high"] - df["low"]
    df["range_pos"] = np.where(
        df["day_range"] > 0, (df["close"] - df["low"]) / df["day_range"], 0.5
    )
    df["min_range_7"] = df["day_range"].rolling(7).min()

    # 14-period Average True Range (ATR) & Exhaustion Ratio
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    row = df.iloc[-1]
    prev = df.iloc[-2]

    H, L, C = float(row["high"]), float(row["low"]), float(row["close"])
    prev_H, prev_L, prev_C = float(prev["high"]), float(prev["low"]), float(prev["close"])

    day_pct = ((C - prev_C) / prev_C) * 100.0 if prev_C > 0 else 0.0
    vol_mult = float(row["volume"] / row["avg_vol20"]) if row["avg_vol20"] > 0 else 0.0
    atr14 = float(row["atr14"]) if pd.notna(row["atr14"]) and row["atr14"] > 0 else float(row["day_range"])
    atr_ratio = float(row["day_range"] / atr14) if atr14 > 0 else 1.0

    is_nr7 = bool(row["day_range"] <= row["min_range_7"])
    is_inside_day = bool(H <= prev_H and L >= prev_L)

    # Next-Day Classic Floor Pivots + Central Pivot Range (CPR)
    pivot = (H + L + C) / 3.0
    bc = (H + L) / 2.0
    tc = pivot + (pivot - bc)
    top_cpr, bot_cpr = max(tc, bc), min(tc, bc)
    cpr_width_pct = (abs(top_cpr - bot_cpr) / pivot) * 100.0 if pivot > 0 else 0.0

    r1 = (2.0 * pivot) - L
    s1 = (2.0 * pivot) - H
    r2 = pivot + (H - L)
    s2 = pivot - (H - L)

    segment_short = "F&O" if is_fno else "CASH"
    segment_label = "F&O" if is_fno else "CASH (MIS Only)"
    date_str = (
        row["timestamp"].date().isoformat()
        if hasattr(row["timestamp"], "date")
        else str(row["timestamp"])[:10]
    )

    # Liquidity check for Cash-only stocks to avoid circuit traps (returns full dict to avoid NaN rows)
    avg_turnover = float(row["avg_vol20"] * C) if pd.notna(row["avg_vol20"]) else 0.0
    if not is_fno and (C < MIN_CASH_PRICE or avg_turnover < MIN_CASH_DAILY_TURNOVER):
        return {
            "intraday_setup": "NONE",
            "segment": segment_short,
            "close": round(C, 2),
            "day_pct": round(day_pct, 2),
            "close_strength_%": round(float(row["range_pos"]) * 100.0, 1),
            "vol_mult": round(vol_mult, 2),
            "atr_ratio": round(atr_ratio, 2),
            "cpr_width_%": round(cpr_width_pct, 3),
            "nr7_day": is_nr7,
            "suggested_qty": 0,
            "PDH": round(H, 2),
            "PDL": round(L, 2),
            "tomorrow_pivot": round(pivot, 2),
            "tomorrow_TC": round(top_cpr, 2),
            "tomorrow_BC": round(bot_cpr, 2),
            "tomorrow_R1": round(r1, 2),
            "tomorrow_R2": round(r2, 2),
            "tomorrow_S1": round(s1, 2),
            "tomorrow_S2": round(s2, 2),
            "atr_14": round(atr14, 2),
            "execution_plan": "Skipped: Below Cash liquidity/price threshold",
            "date": date_str,
        }

    setup = "NONE"
    execution_plan = "-"
    sl_distance = max(abs(H - pivot), C * 0.005)

    # 1. Bullish Momentum Continuation (Trend Aligned + Not ATR Exhausted)
    if (
        day_pct >= 2.0
        and row["range_pos"] >= 0.75
        and vol_mult >= 1.4
        and C > row["ema20"]
        and atr_ratio <= 2.2
    ):
        sl_level = max(top_cpr, pivot)
        sl_distance = max(H - sl_level, C * 0.005)
        setup = f"LONG: Momentum Continuation ({segment_label})"
        execution_plan = (
            f"Buy > 15m ORB High & PDH ({H:.2f}) if > VWAP | "
            f"SL: {sl_level:.2f} | Tgt1: {r1:.2f} | Tgt2: {r2:.2f}"
        )

    # 2. Bearish Breakdown Continuation (Trend Aligned + Not ATR Exhausted)
    elif (
        day_pct <= -2.0
        and row["range_pos"] <= 0.25
        and vol_mult >= 1.4
        and C < row["ema20"]
        and atr_ratio <= 2.5
    ):
        action_note = "Short MIS (Exit by 3:10 PM)" if not is_fno else "Short Futures / Buy Put"
        sl_level = min(bot_cpr, pivot)
        sl_distance = max(sl_level - L, C * 0.005)
        setup = f"SHORT: Breakdown Continuation ({segment_label})"
        execution_plan = (
            f"{action_note} < 15m ORB Low & PDL ({L:.2f}) if < VWAP | "
            f"SL: {sl_level:.2f} | Tgt1: {s1:.2f} | Tgt2: {s2:.2f}"
        )

    # 3A. A+ Coiled Breakout: NR7 + Narrow CPR (<= 0.30%) or Inside-Day NR7 (ID/NR7)
    elif is_nr7 and (cpr_width_pct <= 0.30 or is_inside_day) and C >= MIN_CASH_PRICE:
        tag = "ID+NR7" if is_inside_day else "NR7+Tight CPR"
        sl_distance = max((H - L) * 0.5, C * 0.005)
        setup = f"A+ COILED: {tag} Breakout ({segment_label})"
        execution_plan = (
            f"Buy > {H:.2f} (SL {pivot:.2f}, Tgt {r1:.2f}/{r2:.2f}) OR "
            f"Short < {L:.2f} (SL {pivot:.2f}, Tgt {s1:.2f}/{s2:.2f})"
        )

    # 3B. Standard NR7 Volatility Compression Breakout
    elif is_nr7 and C >= MIN_CASH_PRICE:
        sl_distance = max((H - L) * 0.6, C * 0.005)
        setup = f"BOTH SIDES: NR7 Breakout ({segment_label})"
        execution_plan = (
            f"Buy > {H:.2f} (SL {pivot:.2f}, Tgt {r1:.2f}) OR "
            f"Short < {L:.2f} (SL {pivot:.2f}, Tgt {s1:.2f})"
        )

    suggested_qty = int(max(1, risk_per_trade // sl_distance)) if setup != "NONE" else 0

    return {
        "intraday_setup": setup,
        "segment": segment_short,
        "close": round(C, 2),
        "day_pct": round(day_pct, 2),
        "close_strength_%": round(float(row["range_pos"]) * 100.0, 1),
        "vol_mult": round(vol_mult, 2),
        "atr_ratio": round(atr_ratio, 2),
        "cpr_width_%": round(cpr_width_pct, 3),
        "nr7_day": is_nr7,
        "suggested_qty": suggested_qty,
        "PDH": round(H, 2),
        "PDL": round(L, 2),
        "tomorrow_pivot": round(pivot, 2),
        "tomorrow_TC": round(top_cpr, 2),
        "tomorrow_BC": round(bot_cpr, 2),
        "tomorrow_R1": round(r1, 2),
        "tomorrow_R2": round(r2, 2),
        "tomorrow_S1": round(s1, 2),
        "tomorrow_S2": round(s2, 2),
        "atr_14": round(atr14, 2),
        "execution_plan": execution_plan,
        "date": date_str,
    }
