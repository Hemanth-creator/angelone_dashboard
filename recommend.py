"""
Recommendation engine: a stock is recommended when it shows up in two or more
of the core screener tables (Top Gainers, Top Losers, Most Active, Volume Surge,
Above VWAP, Below VWAP).

Also keeps a per-day "Seen today" log (saved to .cache/seen_<date>.json) of every
stock that ever appeared in the recommended list, with the price and time it
first showed up, so picks that dropped off the live list aren't lost.
"""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import screeners as scr
from ist_time import now_ist, today_ist_str

CACHE_DIR = Path(__file__).parent / ".cache"

REC_COLS = ["name", "bias", "vwap_pos", "vwap_angle", "vwap_trend", "slope_bias", "confluence",
            "hits", "appears_in", "ltp", "avgPrice", "vwap_dev_pct", "percentChange",
            "volume", "turnover", "rvol"]

SEEN_COLS = ["name", "status", "bias_when_seen", "first_seen", "last_seen", "first_price",
             "ltp", "since_first_pct", "in_bias_pct", "max_hits", "refreshes"]


def recommended(live: pd.DataFrame, avg_volume: pd.DataFrame, n: int = 10,
                min_rvol: float = 2.0, min_lists: int = 2,
                min_vwap_dev: float = 0.5) -> pd.DataFrame:
    above_vwap, below_vwap = scr.vwap_crossover(live, min_deviation_pct=min_vwap_dev, n=n)
    lists = {
        "Gainers": scr.top_gainers(live, n=n),
        "Losers": scr.top_losers(live, n=n),
        "Most Active": scr.most_active(live, n=n),
        "Vol Surge": scr.volume_surge(live, avg_volume, min_rvol=min_rvol, n=n),
        "Above VWAP": above_vwap,
        "Below VWAP": below_vwap,
    }

    membership: dict[str, list[str]] = {}
    for label, df in lists.items():
        for name in df["name"]:
            membership.setdefault(name, []).append(label)

    picks = {k: v for k, v in membership.items() if len(v) >= min_lists}
    if not picks:
        return pd.DataFrame(columns=REC_COLS)

    base_cols = ["name", "ltp", "avgPrice", "percentChange", "volume", "turnover"]
    base_cols += [c for c in ("vwap_angle", "vwap_trend") if c in live.columns]
    base = live[live["name"].isin(picks)][base_cols].drop_duplicates(subset="name").copy()
    if "vwap_angle" not in base.columns:
        base["vwap_angle"] = np.nan
    if "vwap_trend" not in base.columns:
        base["vwap_trend"] = "-"
    base["vwap_trend"] = base["vwap_trend"].fillna("-")

    # avgPrice in Angel One's FULL quote is the day's VWAP
    valid = base["avgPrice"] > 0
    base["vwap_dev_pct"] = np.where(
        valid, (base["ltp"] - base["avgPrice"]) / base["avgPrice"].where(valid) * 100, np.nan)
    base["vwap_pos"] = np.where(~valid, "-",
                                np.where(base["ltp"] > base["avgPrice"], "Above",
                                         np.where(base["ltp"] < base["avgPrice"], "Below", "At")))

    rvol = lists["Vol Surge"].set_index("name")["rvol"]
    base["rvol"] = base["name"].map(rvol)
    base["hits"] = base["name"].map(lambda k: len(picks[k]))
    base["appears_in"] = base["name"].map(lambda k: ", ".join(picks[k]))
    base["bias"] = np.where(base["percentChange"] > 0, "Bullish",
                            np.where(base["percentChange"] < 0, "Bearish", "Neutral"))
    # Bias from the VWAP slope: rising VWAP = Bullish, falling = Bearish, flat = Neutral
    base["slope_bias"] = base["vwap_trend"].map(
        {"Rising": "Bullish", "Falling": "Bearish", "Flat": "Neutral"}).fillna("-")
    # Aligned = day change, price-vs-VWAP and VWAP slope all point the same way
    long_ok = (base["bias"] == "Bullish") & (base["vwap_pos"] == "Above") & (base["vwap_trend"] == "Rising")
    short_ok = (base["bias"] == "Bearish") & (base["vwap_pos"] == "Below") & (base["vwap_trend"] == "Falling")
    base["confluence"] = np.select(
        [base["vwap_trend"] == "-", long_ok, short_ok],
        ["No slope yet", "Aligned ↑", "Aligned ↓"], default="Mixed")

    base["_abs"] = base["percentChange"].abs()
    base = base.sort_values(["hits", "_abs"], ascending=[False, False]).drop(columns="_abs")
    return base[REC_COLS].reset_index(drop=True)


# ---------------------------------------------------------------------------
# "Seen today" log
# ---------------------------------------------------------------------------
def _seen_path() -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / f"seen_{today_ist_str()}.json"


def load_seen() -> dict:
    try:
        return json.loads(_seen_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def reset_seen() -> None:
    try:
        _seen_path().unlink()
    except OSError:
        pass


def update_seen(rec: pd.DataFrame, record: bool = True) -> dict:
    """Add every stock currently in `rec` to today's log. Returns the full log."""
    seen = load_seen()
    if not record or rec.empty:
        return seen

    stamp = now_ist().strftime("%H:%M:%S")
    for r in rec.itertuples(index=False):
        name = r.name
        entry = seen.get(name)
        if entry is None:
            entry = {"first_seen": stamp, "first_price": float(r.ltp),
                     "first_bias": r.bias, "max_hits": int(r.hits), "refreshes": 0}
        entry["refreshes"] += 1
        entry["max_hits"] = max(entry["max_hits"], int(r.hits))
        entry["last_seen"] = stamp
        seen[name] = entry

    path = _seen_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen))
    os.replace(tmp, path)
    return seen


def seen_table(seen: dict, live: pd.DataFrame, on_list: set) -> pd.DataFrame:
    """
    One row per stock ever recommended today:
      since_first_pct = price change since it first appeared
      in_bias_pct     = same, signed so that + means it moved in the direction
                        of its bias (up for Bullish, down for Bearish)
    """
    if not seen:
        return pd.DataFrame(columns=SEEN_COLS)

    px = live.drop_duplicates("name").set_index("name")["ltp"]
    rows = []
    for name, s in seen.items():
        ltp = px.get(name, np.nan)
        first = s["first_price"]
        since = (ltp - first) / first * 100 if first and pd.notna(ltp) else np.nan
        sign = -1 if s["first_bias"] == "Bearish" else 1
        rows.append({
            "name": name,
            "status": "On list" if name in on_list else "Dropped",
            "bias_when_seen": s["first_bias"],
            "first_seen": s["first_seen"],
            "last_seen": s.get("last_seen", s["first_seen"]),
            "first_price": first,
            "ltp": ltp,
            "since_first_pct": since,
            "in_bias_pct": since * sign if pd.notna(since) else np.nan,
            "max_hits": s["max_hits"],
            "refreshes": s["refreshes"],
        })
    df = pd.DataFrame(rows, columns=SEEN_COLS)
    return df.sort_values("first_seen", ascending=False).reset_index(drop=True)