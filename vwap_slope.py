"""
VWAP slope tracking.

Angel One's quote only gives the *current* VWAP (`avgPrice`), not its history, so
the dashboard records the VWAP of every stock on each refresh (while the market is
open) into .cache/vwap_hist_<date>.json and fits a straight line through the last
few minutes of it.

  slope  = least-squares slope of VWAP over the window, as % of VWAP per minute
  angle  = degrees, atan(slope / ANGLE_REF_PCT_PER_MIN)
           (so ANGLE_REF_PCT_PER_MIN %/min = 45 degrees; a chart-independent,
            standardised angle - a real chart's angle depends on its axis scale)
  trend  = Rising / Falling / Flat, using a +/- flat-zone angle threshold
"""

import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ist_time import today_ist_str

CACHE_DIR = Path(__file__).parent / ".cache"
KEEP_MINUTES = 60                 # history kept per stock
ANGLE_REF_PCT_PER_MIN = 0.02      # VWAP drift of 0.02 % per minute is drawn as 45 degrees
MIN_POINTS = 3
MIN_SPAN_FRACTION = 0.6           # need data covering >= 60 % of the window

SLOPE_COLS = ["name", "vwap_slope_pct_min", "vwap_angle", "vwap_trend"]


def _path() -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / f"vwap_hist_{today_ist_str()}.json"


def load_history() -> dict:
    try:
        return json.loads(_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def reset_history() -> None:
    try:
        _path().unlink()
    except OSError:
        pass


def record_vwap(live: pd.DataFrame, record: bool = True, now: float | None = None) -> dict:
    """Append the current VWAP of every stock to today's history and return it."""
    hist = load_history()
    if not record:
        return hist

    now = time.time() if now is None else now
    cutoff = now - KEEP_MINUTES * 60
    valid = live[live["avgPrice"] > 0][["name", "avgPrice"]]
    for name, vwap in zip(valid["name"], valid["avgPrice"]):
        pts = hist.get(name, [])
        pts.append([round(now, 1), float(vwap)])
        hist[name] = [p for p in pts if p[0] >= cutoff]

    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(hist))
    os.replace(tmp, path)
    return hist


def vwap_slopes(hist: dict, window_min: float = 10, flat_deg: float = 10) -> pd.DataFrame:
    """
    Fit a line through each stock's VWAP over the last `window_min` minutes of its
    recorded history. Stocks without enough history yet are left out.
    """
    rows = []
    for name, pts in hist.items():
        if len(pts) < MIN_POINTS:
            continue
        arr = np.asarray(pts, dtype=float)
        t_last = arr[-1, 0]
        w = arr[arr[:, 0] >= t_last - window_min * 60]
        if len(w) < MIN_POINTS or (w[-1, 0] - w[0, 0]) < window_min * 60 * MIN_SPAN_FRACTION:
            continue

        x = (w[:, 0] - w[0, 0]) / 60.0          # minutes
        slope = np.polyfit(x, w[:, 1], 1)[0]    # rupees per minute
        pct_per_min = slope / w[:, 1].mean() * 100
        angle = math.degrees(math.atan(pct_per_min / ANGLE_REF_PCT_PER_MIN))
        trend = "Rising" if angle >= flat_deg else ("Falling" if angle <= -flat_deg else "Flat")
        rows.append({"name": name, "vwap_slope_pct_min": pct_per_min,
                     "vwap_angle": angle, "vwap_trend": trend})

    return pd.DataFrame(rows, columns=SLOPE_COLS)