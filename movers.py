"""
Early Movers: stocks that are *setting up* to move, rather than ones that already
moved (which is what the Gainers / Losers style tables show).

A stock is only considered if it passes the trend filter:

  Long watch :  price ABOVE VWAP  and  VWAP RISING
  Short watch:  price BELOW VWAP  and  VWAP FALLING

plus "not already extended" gates:
  - |day change| <= max_move %          (the big move hasn't happened yet)
  - distance from VWAP <= max_ext %     (price isn't stretched away from VWAP)
  - turnover >= min_turnover_cr crore   (liquid enough to trade)

Each stock that passes then earns one point per confluence signal:

  Long                              Short
  - Volume building (RVOL >= min)   - Volume building (RVOL >= min)
  - At/near opening-range HIGH      - At/near opening-range LOW
  - Near the day's high (top 20%)   - Near the day's low (bottom 20%)
  - Price above today's open        - Price below today's open
  - Steep VWAP slope (>= 25 deg)    - Steep VWAP slope (<= -25 deg)

Only stocks with at least `min_score` points are shown, best first.
"""

import numpy as np
import pandas as pd

import screeners as scr

OUT_COLS = ["name", "score", "signals", "ltp", "percentChange", "vwap_dev_pct", "vwap_angle",
            "vwap_trend", "rvol", "day_range_pos", "or_dist_pct", "turnover_cr"]


def _num(df: pd.DataFrame, cols) -> None:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")


def _prepare(live, avg_volume, opening_range) -> pd.DataFrame:
    d = live.copy()
    d["token"] = d["token"].astype(str)
    _num(d, ["ltp", "open", "high", "low", "avgPrice", "percentChange", "volume", "turnover"])

    av = avg_volume[["token", "avg_volume_20d"]].copy()
    av["token"] = av["token"].astype(str)
    av["avg_volume_20d"] = pd.to_numeric(av["avg_volume_20d"], errors="coerce")
    d = d.merge(av, on="token", how="left")

    orng = opening_range[["token", "or_high", "or_low"]].copy()
    orng["token"] = orng["token"].astype(str)
    _num(orng, ["or_high", "or_low"])
    d = d.merge(orng, on="token", how="left")

    frac = scr._minutes_elapsed_today() / scr.TRADING_MINUTES
    expected = d["avg_volume_20d"] * frac
    d["rvol"] = (d["volume"] / expected.replace(0, np.nan)).round(2)

    valid = d["avgPrice"] > 0
    d["vwap_dev_pct"] = np.where(valid, (d["ltp"] - d["avgPrice"]) / d["avgPrice"].where(valid) * 100, np.nan)
    rng = d["high"] - d["low"]
    d["day_range_pos"] = np.where(rng > 0, (d["ltp"] - d["low"]) / rng.where(rng > 0) * 100, np.nan)
    d["turnover_cr"] = d["turnover"] / 1e7
    return d


def _finish(d, mask, signals: dict, min_score, n) -> pd.DataFrame:
    d = d[mask].copy()
    if d.empty:
        return pd.DataFrame(columns=OUT_COLS)
    names = list(signals)
    for k in names:
        d[k] = signals[k].reindex(d.index).fillna(False).astype(bool)
    d["score"] = d[names].sum(axis=1).astype(int)
    d["signals"] = d[names].apply(lambda r: ", ".join(k for k in names if r[k]), axis=1)
    d = d[d["score"] >= min_score]
    d["_a"] = d["vwap_angle"].abs()
    d = d.sort_values(["score", "_a"], ascending=[False, False])
    return d[OUT_COLS].head(n).reset_index(drop=True)


def early_movers(live: pd.DataFrame, avg_volume: pd.DataFrame, opening_range: pd.DataFrame,
                 max_move: float = 2.0, min_rvol: float = 1.5, min_turnover_cr: float = 5.0,
                 max_ext: float = 1.5, min_score: int = 2, strong_angle: float = 25.0,
                 near_or_pct: float = 0.3, n: int = 15):
    """Returns (long_df, short_df, info). info has counts for the caption."""
    info = {"slope_ready": 0, "long_pass": 0, "short_pass": 0}
    if "vwap_angle" not in live.columns or live["vwap_angle"].notna().sum() == 0:
        return (pd.DataFrame(columns=OUT_COLS), pd.DataFrame(columns=OUT_COLS), info)

    d = _prepare(live, avg_volume, opening_range)
    info["slope_ready"] = int(d["vwap_angle"].notna().sum())

    common = ((d["percentChange"].abs() <= max_move)
              & (d["vwap_dev_pct"].abs() <= max_ext)
              & (d["turnover_cr"] >= min_turnover_cr))
    long_gate = common & (d["vwap_trend"] == "Rising") & (d["ltp"] > d["avgPrice"])
    short_gate = common & (d["vwap_trend"] == "Falling") & (d["ltp"] < d["avgPrice"])
    info["long_pass"], info["short_pass"] = int(long_gate.sum()), int(short_gate.sum())

    vol = d["rvol"] >= min_rvol

    # distance beyond the opening-range edge (negative = still inside the range)
    or_up = (d["ltp"] - d["or_high"]) / d["or_high"] * 100
    or_dn = (d["or_low"] - d["ltp"]) / d["or_low"] * 100

    dl = d.assign(or_dist_pct=or_up)
    long_sig = {
        "Volume building": vol,
        "At OR high": (or_up >= -near_or_pct) & (or_up <= 1.0),
        "Near day high": d["day_range_pos"] >= 80,
        "Above open": d["ltp"] > d["open"],
        "Steep VWAP": d["vwap_angle"] >= strong_angle,
    }
    ds = d.assign(or_dist_pct=or_dn)
    short_sig = {
        "Volume building": vol,
        "At OR low": (or_dn >= -near_or_pct) & (or_dn <= 1.0),
        "Near day low": d["day_range_pos"] <= 20,
        "Below open": d["ltp"] < d["open"],
        "Steep VWAP": d["vwap_angle"] <= -strong_angle,
    }
    return (_finish(dl, long_gate, long_sig, min_score, n),
            _finish(ds, short_gate, short_sig, min_score, n), info)


# ---------------------------------------------------------------------------
# "Early Movers seen today" log: once a stock appears in Long or Short watch it
# stays in this log (with where it first showed up and how it has done since),
# even after it drops out of the live tables. Saved per day, survives restarts.
# ---------------------------------------------------------------------------
import json
import os
from pathlib import Path

from ist_time import now_ist, today_ist_str

_CACHE_DIR = Path(__file__).parent / ".cache"

EM_SEEN_COLS = ["name", "side", "ema_angle", "vwap_angle", "status", "first_seen", "last_seen",
                "first_price", "ltp", "since_first_pct", "in_side_pct", "first_angle", "vwap_trend",
                "max_score", "refreshes", "signals_when_first"]


def _em_path(prefix: str = "early_seen") -> Path:
    _CACHE_DIR.mkdir(exist_ok=True)
    return _CACHE_DIR / f"{prefix}_{today_ist_str()}.json"


def load_em_seen(prefix: str = "early_seen") -> dict:
    try:
        return json.loads(_em_path(prefix).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def reset_em_seen(prefix: str = "early_seen") -> None:
    try:
        _em_path(prefix).unlink()
    except OSError:
        pass


def update_em_seen(long_df: pd.DataFrame, short_df: pd.DataFrame, record: bool = True,
                   prefix: str = "early_seen") -> dict:
    """Add every stock currently in the Long/Short tables to today's log."""
    seen = load_em_seen(prefix)
    if not record:
        return seen

    stamp = now_ist().strftime("%H:%M:%S")
    changed = False
    for side, df in (("Long", long_df), ("Short", short_df)):
        for r in df.itertuples(index=False):
            key = f"{r.name}|{side}"
            e = seen.get(key)
            if e is None:
                e = {"name": r.name, "side": side, "first_seen": stamp, "first_price": float(r.ltp),
                     "max_score": int(r.score), "refreshes": 0, "signals_when_first": r.signals,
                     "first_angle": None if pd.isna(r.vwap_angle) else float(r.vwap_angle)}
            e["refreshes"] += 1
            e["max_score"] = max(e["max_score"], int(r.score))
            e["last_seen"] = stamp
            seen[key] = e
            changed = True

    if changed:
        path = _em_path(prefix)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(seen))
        os.replace(tmp, path)
    return seen


def em_seen_table(seen: dict, live: pd.DataFrame, long_df: pd.DataFrame, short_df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per stock+side ever shown. in_side_pct is the move since first appearing,
    signed so that + means it moved the way the watch predicted (up for Long, down for Short).
    """
    if not seen:
        return pd.DataFrame(columns=EM_SEEN_COLS)

    lv = live.drop_duplicates("name").set_index("name")
    px = lv["ltp"]
    ang = lv["vwap_angle"] if "vwap_angle" in lv.columns else pd.Series(dtype=float)
    trd = lv["vwap_trend"] if "vwap_trend" in lv.columns else pd.Series(dtype=object)
    ema_ang = lv["ema_angle"] if "ema_angle" in lv.columns else pd.Series(dtype=float)
    on = {("Long", n) for n in long_df["name"]} | {("Short", n) for n in short_df["name"]}
    rows = []
    for e in seen.values():
        ltp = px.get(e["name"], np.nan)
        first = e["first_price"]
        since = (ltp - first) / first * 100 if first and pd.notna(ltp) else np.nan
        sign = -1 if e["side"] == "Short" else 1
        rows.append({
            "name": e["name"], "side": e["side"],
            "ema_angle": ema_ang.get(e["name"], np.nan),
            "vwap_angle": ang.get(e["name"], np.nan),
            "status": "On list" if (e["side"], e["name"]) in on else "Dropped",
            "first_seen": e["first_seen"], "last_seen": e.get("last_seen", e["first_seen"]),
            "first_price": first, "ltp": ltp, "since_first_pct": since,
            "in_side_pct": since * sign if pd.notna(since) else np.nan,
            "first_angle": e.get("first_angle"),
            "vwap_trend": trd.get(e["name"], "-") if pd.notna(trd.get(e["name"], np.nan)) else "-",
            "max_score": e["max_score"], "refreshes": e["refreshes"],
            "signals_when_first": e["signals_when_first"],
        })
    df = pd.DataFrame(rows, columns=EM_SEEN_COLS)
    df["first_angle"] = pd.to_numeric(df["first_angle"], errors="coerce")
    return df.sort_values("first_seen", ascending=False).reset_index(drop=True)