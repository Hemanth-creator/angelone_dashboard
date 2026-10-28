"""
Turns the merged live-quote DataFrame (+ historical context) into the actual
ranked screener tables shown on the dashboard.

Every function takes/returns plain pandas DataFrames so they're easy to test,
tweak thresholds on, or reuse outside Streamlit.
"""

from datetime import time as dtime

import numpy as np
import pandas as pd

from ist_time import now_ist

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
TRADING_MINUTES = 375  # 9:15 -> 15:30


def _minutes_elapsed_today() -> int:
    now = now_ist().time()  # IST, independent of the host machine's timezone
    if now < MARKET_OPEN:
        return 1  # avoid div-by-zero pre-open
    if now > MARKET_CLOSE:
        return TRADING_MINUTES
    elapsed = (now.hour * 60 + now.minute) - (MARKET_OPEN.hour * 60 + MARKET_OPEN.minute)
    return max(elapsed, 1)


DISPLAY_COLS = ["name", "ltp", "percentChange", "volume", "turnover", "high", "low", "open", "close"]


def top_gainers(live: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    df = live[live["percentChange"] > 0].sort_values("percentChange", ascending=False)
    return df[DISPLAY_COLS].head(n).reset_index(drop=True)


def top_losers(live: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    df = live[live["percentChange"] < 0].sort_values("percentChange", ascending=True)
    return df[DISPLAY_COLS].head(n).reset_index(drop=True)


def most_active(live: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    """By turnover (price x volume) - the standard 'most active' definition,
    since it reflects actual rupee value traded, not just share count."""
    df = live.sort_values("turnover", ascending=False)
    return df[DISPLAY_COLS].head(n).reset_index(drop=True)


def high_volume(live: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    """By raw share volume."""
    df = live.sort_values("volume", ascending=False)
    return df[DISPLAY_COLS].head(n).reset_index(drop=True)


def volume_surge(live: pd.DataFrame, avg_volume: pd.DataFrame, min_rvol: float = 2.0, n: int = 20) -> pd.DataFrame:
    """
    Relative Volume (RVOL) = today's volume so far / expected volume by this
    point in the day, based on the 20-day average daily volume.
    """
    av = avg_volume[["token", "avg_volume_20d"]].copy()
    av["token"] = av["token"].astype(str)
    av["avg_volume_20d"] = pd.to_numeric(av["avg_volume_20d"], errors="coerce")  # None -> NaN
    merged = live.assign(token=live["token"].astype(str)).merge(av, on="token", how="left")
    frac_of_day = _minutes_elapsed_today() / TRADING_MINUTES
    merged["expected_volume_by_now"] = merged["avg_volume_20d"] * frac_of_day
    merged["rvol"] = merged["volume"] / merged["expected_volume_by_now"].replace(0, np.nan)

    df = merged[merged["rvol"] >= min_rvol].sort_values("rvol", ascending=False)
    cols = ["name", "ltp", "percentChange", "volume", "avg_volume_20d", "rvol"]
    out = df[cols].head(n).reset_index(drop=True)
    out["rvol"] = out["rvol"].round(2)
    return out


def vwap_crossover(live: pd.DataFrame, min_deviation_pct: float = 0.5, n: int = 20):
    """
    Angel One's FULL quote returns `avgPrice` = the day's volume-weighted
    average traded price so far, i.e. VWAP. Splits into two tables
    (above / below).
    """
    df = live.copy()
    df = df[df["avgPrice"] > 0]
    df["vwap_dev_pct"] = (df["ltp"] - df["avgPrice"]) / df["avgPrice"] * 100

    above = df[df["vwap_dev_pct"] >= min_deviation_pct].sort_values("vwap_dev_pct", ascending=False)
    below = df[df["vwap_dev_pct"] <= -min_deviation_pct].sort_values("vwap_dev_pct", ascending=True)

    cols = ["name", "ltp", "avgPrice", "vwap_dev_pct", "percentChange", "volume"]
    above_out = above[cols].head(n).reset_index(drop=True)
    below_out = below[cols].head(n).reset_index(drop=True)
    for out in (above_out, below_out):
        out["vwap_dev_pct"] = out["vwap_dev_pct"].round(2)
    return above_out, below_out


def gap_and_opening_range(live: pd.DataFrame, opening_range: pd.DataFrame,
                           min_gap_pct: float = 2.0, n: int = 20):
    """
    Two related screens built on the same opening-range data:
      - Gap scanner: stocks that opened far from yesterday's close.
      - Opening-range breakout: stocks now trading beyond their first
        15-minute high/low.
    """
    orng = opening_range[["token", "or_high", "or_low", "day_open"]].copy()
    orng["token"] = orng["token"].astype(str)
    for c in ("or_high", "or_low", "day_open"):
        orng[c] = pd.to_numeric(orng[c], errors="coerce")  # None -> NaN (stocks with no candles)
    merged = live.assign(token=live["token"].astype(str)).merge(orng, on="token", how="left")
    # Angel One's FULL quote `close` field is the previous session's closing price
    merged["gap_pct"] = (merged["open"] - merged["close"]) / merged["close"] * 100

    gaps = merged[merged["gap_pct"].abs() >= min_gap_pct].copy()
    gaps["direction"] = np.where(gaps["gap_pct"] > 0, "Gap Up", "Gap Down")
    gaps = gaps.reindex(gaps["gap_pct"].abs().sort_values(ascending=False).index)
    gap_cols = ["name", "direction", "gap_pct", "open", "close", "ltp", "percentChange", "volume"]
    gap_out = gaps[gap_cols].head(n).reset_index(drop=True)
    gap_out["gap_pct"] = gap_out["gap_pct"].round(2)

    breakout_up = merged[(merged["or_high"].notna()) & (merged["ltp"] > merged["or_high"])].copy()
    breakout_up["pct_beyond_range"] = (breakout_up["ltp"] - breakout_up["or_high"]) / breakout_up["or_high"] * 100
    breakout_up = breakout_up.sort_values("pct_beyond_range", ascending=False)

    breakout_down = merged[(merged["or_low"].notna()) & (merged["ltp"] < merged["or_low"])].copy()
    breakout_down["pct_beyond_range"] = (breakout_down["or_low"] - breakout_down["ltp"]) / breakout_down["or_low"] * 100
    breakout_down = breakout_down.sort_values("pct_beyond_range", ascending=False)

    br_cols = ["name", "ltp", "or_high", "or_low", "pct_beyond_range", "volume"]
    breakout_up_out = breakout_up[br_cols].head(n).reset_index(drop=True)
    breakout_down_out = breakout_down[br_cols].head(n).reset_index(drop=True)
    for out in (breakout_up_out, breakout_down_out):
        out["pct_beyond_range"] = out["pct_beyond_range"].round(2)

    return gap_out, breakout_up_out, breakout_down_out