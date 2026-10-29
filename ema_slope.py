"""
20 EMA slope tracking.

Mirrors vwap_slope.py's approach, but for a period-length EMA of each stock's
live price (LTP) instead of VWAP. The dashboard has no intraday candle store,
so the EMA is computed directly over the recorded tick series captured on
every refresh (while the market is open) rather than over fixed-interval
candles - a reasonable live-quote-cadence approximation of "is price
trending up or down right now."

  ema         = standard exponential moving average, span = `period` (default
                20), computed over every tick recorded so far today
  slope/angle = same least-squares-over-a-recent-window method as
                vwap_slope.py, fitted through the EMA's own values (not the
                raw price) so one noisy tick doesn't swing the angle, and
                using the same ANGLE_REF_PCT_PER_MIN scale so EMA angle and
                VWAP angle are directly comparable
  trend       = Rising / Falling / Flat, same +/- flat-zone angle threshold
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
ANGLE_REF_PCT_PER_MIN = 0.02      # same reference as vwap_slope.py, for comparable angles
MIN_POINTS = 3
MIN_SPAN_FRACTION = 0.6           # need data covering >= 60 % of the window
DEFAULT_PERIOD = 20

SLOPE_COLS = ["name", "ema_value", "ema_slope_pct_min", "ema_angle", "ema_trend"]


def _path() -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / f"ltp_hist_{today_ist_str()}.json"


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


def record_ltp(live: pd.DataFrame, record: bool = True, now: float | None = None) -> dict:
    """Append the current LTP of every stock to today's history and return it."""
    hist = load_history()
    if not record:
        return hist

    now = time.time() if now is None else now
    cutoff = now - KEEP_MINUTES * 60
    valid = live[live["ltp"] > 0][["name", "ltp"]]
    for name, ltp in zip(valid["name"], valid["ltp"]):
        pts = hist.get(name, [])
        pts.append([round(now, 1), float(ltp)])
        hist[name] = [p for p in pts if p[0] >= cutoff]

    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(hist))
    os.replace(tmp, path)
    return hist


def _ema_series(prices: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    ema = np.empty_like(prices)
    ema[0] = prices[0]
    for i in range(1, len(prices)):
        ema[i] = alpha * prices[i] + (1 - alpha) * ema[i - 1]
    return ema


def ema_slopes(hist: dict, period: int = DEFAULT_PERIOD, window_min: float = 10,
               flat_deg: float = 10) -> pd.DataFrame:
    """
    For each stock: compute a `period`-length EMA over all ticks recorded
    today, then fit a line through the EMA's last `window_min` minutes to get
    its angle. Stocks without enough history (or not enough ticks to seed a
    stable EMA) are left out.
    """
    rows = []
    min_ticks = max(MIN_POINTS, period // 4)
    for name, pts in hist.items():
        if len(pts) < min_ticks:
            continue
        arr = np.asarray(pts, dtype=float)
        ema = _ema_series(arr[:, 1], period)

        t_last = arr[-1, 0]
        mask = arr[:, 0] >= t_last - window_min * 60
        w_t, w_ema = arr[mask, 0], ema[mask]
        if len(w_t) < MIN_POINTS or (w_t[-1] - w_t[0]) < window_min * 60 * MIN_SPAN_FRACTION:
            continue

        x = (w_t - w_t[0]) / 60.0               # minutes
        slope = np.polyfit(x, w_ema, 1)[0]       # rupees per minute
        pct_per_min = slope / w_ema.mean() * 100
        angle = math.degrees(math.atan(pct_per_min / ANGLE_REF_PCT_PER_MIN))
        trend = "Rising" if angle >= flat_deg else ("Falling" if angle <= -flat_deg else "Flat")
        rows.append({"name": name, "ema_value": float(ema[-1]), "ema_slope_pct_min": pct_per_min,
                     "ema_angle": angle, "ema_trend": trend})

    return pd.DataFrame(rows, columns=SLOPE_COLS)