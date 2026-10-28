"""
Historical data for context the live quote endpoint can't give you:

  1. 20-day average daily volume per stock -> used to compute Relative Volume
     (RVOL = today's volume so far / typical volume by this time of day),
     which is what the "Volume Surge" screener is actually built on.
  2. Today's opening-range high/low (first 15 minutes, 9:15-9:30 IST) and the
     previous close -> used for the gap scanner and opening-range breakout.

Angel One's historical candle API (getCandleData) is rate-limited (~3 req/sec)
and charged against a separate quota from live quotes, so this data is fetched
ONCE per day per stock and cached to disk - not on every dashboard refresh.

For ~200 stocks, expect this one-time daily fetch to take roughly 2-5 minutes
depending on API latency - each call is paced ~0.35s apart, but Angel One's
historical endpoint itself often takes 1-2s to respond, and occasional
timeouts (with automatic retry) add more. Progress is printed to the
terminal every 20 stocks, and partial results are checkpointed to disk every
25 stocks so a restart doesn't lose all progress.
"""

import json
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd

from ist_time import now_ist, today_ist_str, market_open_today

CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

REQUEST_DELAY_SECONDS = 0.35  # ~3 req/sec ceiling on historical API
CHECKPOINT_EVERY = 25
PRINT_EVERY = 10


class SessionExpired(RuntimeError):
    """Angel One rejected our token (AG8001) - need to log in again."""


class DataFetchError(RuntimeError):
    """Most candle requests failed, so the result is not worth caching."""


def _ok(resp) -> bool:
    return isinstance(resp, dict) and bool(resp.get("status") or resp.get("success"))


def _today_cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}_{today_ist_str()}.json"


def _get_candle_data_with_retry(smart_api, params, retries: int = 2, backoff: float = 1.5):
    """
    A single flaky connection (timeout, transient 5xx) shouldn't blank out a
    whole stock's data. Retries a couple of times with backoff before giving up.
    """
    last_err = None
    for attempt in range(retries + 1):
        try:
            return smart_api.getCandleData(params)
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise last_err


def _run_loop(smart_api, universe: pd.DataFrame, build_params, parse_candles,
              empty_row, cache_path: Path, label: str, progress_cb=None) -> pd.DataFrame:
    """
    Shared driver for both historical fetches below: loops the universe,
    calls getCandleData per stock, reports progress (console + callback),
    checkpoints partial results to disk, and caches the final result.
    """
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        if cached.get("done"):
            return pd.DataFrame(cached["rows"])
        results = cached["rows"]
        start_idx = len(results)
        print(f"[{label}] Resuming from checkpoint: {start_idx}/{len(universe)} already done.")
    else:
        results = []
        start_idx = 0

    total = len(universe)
    start_time = time.time()
    failures = 0

    for idx in range(start_idx, total):
        row = universe.iloc[idx]
        try:
            params = build_params(row)
            resp = _get_candle_data_with_retry(smart_api, params)
            if isinstance(resp, dict) and resp.get("errorCode") == "AG8001":
                raise SessionExpired("Angel One rejected the session token (AG8001 Invalid Token).")
            if not _ok(resp):
                failures += 1
                results.append(empty_row(row))
            else:
                candles = resp.get("data") or []
                results.append(parse_candles(row, candles))
        except SessionExpired:
            raise
        except Exception:
            failures += 1
            results.append(empty_row(row))

        done = idx + 1
        elapsed = time.time() - start_time
        rate = elapsed / (done - start_idx) if done > start_idx else 0
        remaining = (total - done) * rate
        eta_str = f"{int(remaining // 60)}m {int(remaining % 60)}s"

        if done % PRINT_EVERY == 0 or done == total:
            print(f"[{label}] {done}/{total} stocks ({done/total*100:.0f}%) "
                  f"- elapsed {int(elapsed)}s, ETA ~{eta_str}")

        if progress_cb:
            progress_cb(done, total, elapsed, remaining)

        if done % CHECKPOINT_EVERY == 0:
            cache_path.write_text(json.dumps({"done": False, "rows": results}))

        time.sleep(REQUEST_DELAY_SECONDS)

    if failures > 0.5 * max(total - start_idx, 1):
        # Don't save a mostly-empty result as "done" - that would poison the whole day.
        try:
            cache_path.unlink()
        except OSError:
            pass
        raise DataFetchError(f"{label}: {failures} of {total - start_idx} requests failed; "
                             f"nothing was cached, it will retry on the next reload.")
    cache_path.write_text(json.dumps({"done": True, "rows": results}))
    print(f"[{label}] Done: {total}/{total} in {int(time.time() - start_time)}s ({failures} failed).")
    return pd.DataFrame(results)


def fetch_avg_volume_20d(smart_api, universe: pd.DataFrame, progress_cb=None) -> pd.DataFrame:
    """
    Returns DataFrame [name, token, avg_volume_20d, prev_close].
    Cached once per calendar day (with resumable checkpoints).
    """
    to_date = now_ist()
    from_date = to_date - timedelta(days=35)  # buffer for weekends/holidays -> ~20 trading days

    def build_params(row):
        return {
            "exchange": row["exchange"],
            "symboltoken": str(row["token"]),
            "interval": "ONE_DAY",
            "fromdate": from_date.strftime("%Y-%m-%d 09:15"),
            "todate": to_date.strftime("%Y-%m-%d 15:30"),
        }

    def parse_candles(row, candles):
        if len(candles) >= 2:
            # candle: [timestamp, open, high, low, close, volume]
            recent = candles[-21:-1] if len(candles) > 20 else candles[:-1]
            vols = [c[5] for c in recent if c[5]]
            avg_vol = sum(vols) / len(vols) if vols else None
            prev_close = candles[-2][4]
        else:
            avg_vol, prev_close = None, None
        return {"name": row["name"], "token": row["token"],
                "avg_volume_20d": avg_vol, "prev_close": prev_close}

    def empty_row(row):
        return {"name": row["name"], "token": row["token"],
                "avg_volume_20d": None, "prev_close": None}

    cache_path = _today_cache_path("avg_volume")
    return _run_loop(smart_api, universe, build_params, parse_candles, empty_row,
                      cache_path, "avg-volume", progress_cb)


def fetch_opening_range(smart_api, universe: pd.DataFrame, minutes: int = 15, progress_cb=None) -> pd.DataFrame:
    """
    Returns DataFrame [name, token, or_high, or_low, day_open] using the first
    `minutes` of today's 5-minute candles. Cached once per day - call this any
    time after 9:15 + minutes IST; once market open, the range doesn't change.
    """
    market_open = market_open_today()
    range_end = market_open + timedelta(minutes=minutes)
    from_dt = market_open.strftime("%Y-%m-%d %H:%M")
    to_dt = range_end.strftime("%Y-%m-%d %H:%M")

    def build_params(row):
        return {
            "exchange": row["exchange"],
            "symboltoken": str(row["token"]),
            "interval": "FIVE_MINUTE",
            "fromdate": from_dt,
            "todate": to_dt,
        }

    def parse_candles(row, candles):
        if candles:
            highs = [c[2] for c in candles]
            lows = [c[3] for c in candles]
            return {"name": row["name"], "token": row["token"],
                    "or_high": max(highs), "or_low": min(lows), "day_open": candles[0][1]}
        return {"name": row["name"], "token": row["token"],
                "or_high": None, "or_low": None, "day_open": None}

    def empty_row(row):
        return {"name": row["name"], "token": row["token"],
                "or_high": None, "or_low": None, "day_open": None}

    cache_path = _today_cache_path(f"opening_range_{minutes}m")
    return _run_loop(smart_api, universe, build_params, parse_candles, empty_row,
                      cache_path, "opening-range", progress_cb)