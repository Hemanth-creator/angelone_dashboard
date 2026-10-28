"""
Opening Reversals ("first push"): stocks that flushed at the open (or opened soft) and are
now recovering and pushing hard above a steeply rising VWAP - the shape of the 28-Oct
Voltas / Exide / Apollo / Fortis / Max moves - or the mirror image for shorts.

Built only from data the dashboard already has (live quote fields + the VWAP history it
records), so it works a few minutes after the 09:15 open:

  Long  (dip & reclaim):  price above VWAP AND above today's open, VWAP Rising (short window),
                          gap is NOT a gap-up, day range has expanded, price has pushed up
                          off the day's low, enough turnover.
  Short (spike & fail):   mirror image (gap is NOT a gap-down, price below VWAP and the open,
                          VWAP Falling, pushed down off the day's high).

One point per confluence signal (Long / Short):
  - Volume surge           RVOL >= min          RVOL >= min
  - Flush & reclaim        opened -> dipped >= 0.5 % -> back above open     Spike & fail (mirror)
  - Gap filled             gap-down opening that has recovered to prev close  Gap-up that has faded
  - Near day high / low    top 30 % / bottom 30 % of the day's range
  - Steep VWAP             angle >= +25 deg     angle <= -25 deg
"""

import numpy as np
import pandas as pd

import screeners as scr
from movers import _num

FP_COLS = ["name", "score", "signals", "ltp", "gap_pct", "percentChange", "push_pct", "flush_pct",
           "vwap_dev_pct", "vwap_angle", "vwap_trend", "rvol", "turnover_cr"]


def _prepare(live: pd.DataFrame, avg_volume: pd.DataFrame) -> pd.DataFrame:
    d = live.copy()
    d["token"] = d["token"].astype(str)
    _num(d, ["ltp", "open", "high", "low", "close", "avgPrice", "percentChange", "volume", "turnover"])

    av = avg_volume[["token", "avg_volume_20d"]].copy()
    av["token"] = av["token"].astype(str)
    av["avg_volume_20d"] = pd.to_numeric(av["avg_volume_20d"], errors="coerce")
    d = d.merge(av, on="token", how="left")

    frac = scr._minutes_elapsed_today() / scr.TRADING_MINUTES
    d["rvol"] = (d["volume"] / (d["avg_volume_20d"] * frac).replace(0, np.nan)).round(2)

    ok = (d["close"] > 0) & (d["open"] > 0)
    d["gap_pct"] = np.where(ok, (d["open"] - d["close"]) / d["close"].where(ok) * 100, np.nan)
    d["range_pct"] = np.where(ok, (d["high"] - d["low"]) / d["close"].where(ok) * 100, np.nan)
    valid = d["avgPrice"] > 0
    d["vwap_dev_pct"] = np.where(valid, (d["ltp"] - d["avgPrice"]) / d["avgPrice"].where(valid) * 100, np.nan)
    rng = d["high"] - d["low"]
    d["day_range_pos"] = np.where(rng > 0, (d["ltp"] - d["low"]) / rng.where(rng > 0) * 100, np.nan)
    d["turnover_cr"] = d["turnover"] / 1e7
    return d


def _finish(d, gate, signals: dict, min_score, n) -> pd.DataFrame:
    d = d[gate].copy()
    if d.empty:
        return pd.DataFrame(columns=FP_COLS)
    names = list(signals)
    for k in names:
        d[k] = signals[k].reindex(d.index).fillna(False).astype(bool)
    d["score"] = d[names].sum(axis=1).astype(int)
    d["signals"] = d[names].apply(lambda r: ", ".join(k for k in names if r[k]), axis=1)
    d = d[d["score"] >= min_score]
    d["_p"] = d["push_pct"]
    d = d.sort_values(["score", "_p"], ascending=[False, False])
    return d[FP_COLS].head(n).reset_index(drop=True)


def first_push(live: pd.DataFrame, avg_volume: pd.DataFrame, max_gap: float = 1.0,
               min_range: float = 1.0, min_push: float = 0.7, min_rvol: float = 3.0,
               min_turnover_cr: float = 3.0, min_score: int = 2, strong_angle: float = 25.0,
               min_flush: float = 0.5, n: int = 15):
    """Returns (long_df, short_df, info). `live` must carry vwap_angle / vwap_trend columns."""
    info = {"slope_ready": 0, "long_pass": 0, "short_pass": 0}
    empty = pd.DataFrame(columns=FP_COLS)
    if "vwap_angle" not in live.columns or live["vwap_angle"].notna().sum() == 0:
        return empty, empty.copy(), info

    d = _prepare(live, avg_volume)
    info["slope_ready"] = int(d["vwap_angle"].notna().sum())

    base = (d["turnover_cr"] >= min_turnover_cr) & (d["range_pct"] >= min_range) & d["gap_pct"].notna()
    vol = d["rvol"] >= min_rvol

    # ---- Long: dip & reclaim ----
    dl = d.assign(push_pct=(d["ltp"] - d["low"]) / d["low"] * 100,
                  flush_pct=(d["open"] - d["low"]) / d["open"] * 100)
    long_gate = (base & (d["gap_pct"] <= max_gap) & (dl["push_pct"] >= min_push)
                 & (d["ltp"] > d["avgPrice"]) & (d["ltp"] > d["open"]) & (d["vwap_trend"] == "Rising"))
    long_sig = {
        "Volume surge": vol,
        "Flush & reclaim": dl["flush_pct"] >= min_flush,
        "Gap-down filled": (d["gap_pct"] < 0) & (d["ltp"] >= d["close"]),
        "Near day high": d["day_range_pos"] >= 70,
        "Steep VWAP": d["vwap_angle"] >= strong_angle,
    }

    # ---- Short: spike & fail (mirror) ----
    ds = d.assign(push_pct=(d["high"] - d["ltp"]) / d["high"] * 100,
                  flush_pct=(d["high"] - d["open"]) / d["open"] * 100)
    short_gate = (base & (d["gap_pct"] >= -max_gap) & (ds["push_pct"] >= min_push)
                  & (d["ltp"] < d["avgPrice"]) & (d["ltp"] < d["open"]) & (d["vwap_trend"] == "Falling"))
    short_sig = {
        "Volume surge": vol,
        "Spike & fail": ds["flush_pct"] >= min_flush,
        "Gap-up faded": (d["gap_pct"] > 0) & (d["ltp"] <= d["close"]),
        "Near day low": d["day_range_pos"] <= 30,
        "Steep VWAP": d["vwap_angle"] <= -strong_angle,
    }

    info["long_pass"], info["short_pass"] = int(long_gate.sum()), int(short_gate.sum())
    return (_finish(dl, long_gate, long_sig, min_score, n),
            _finish(ds, short_gate, short_sig, min_score, n), info)