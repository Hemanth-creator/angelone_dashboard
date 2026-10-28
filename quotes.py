"""
Batched live-quote fetching via Angel One's /market/v1/quote endpoint (FULL mode).

The endpoint allows up to 50 instrument tokens per request and is rate-limited
to roughly 1 request/second. For a ~200-stock universe that's ~4 requests per
refresh cycle, i.e. about 4-5 seconds per full scan - comfortable for a
dashboard that refreshes every 15-30s.
"""

import time

import pandas as pd

BATCH_SIZE = 50
REQUEST_DELAY_SECONDS = 1.1  # stay just under the 1 req/sec limit


def fetch_quotes(smart_api, universe: pd.DataFrame) -> pd.DataFrame:
    """
    universe: DataFrame with columns [name, trading_symbol, token, exchange, lotsize]
    Returns a DataFrame with live FULL-mode quote fields merged in:
        ltp, open, high, low, close, volume, avgPrice (used as a VWAP proxy),
        netChange, percentChange, tradeVolume, exchFeedTime
    """
    tokens = universe["token"].astype(str).tolist()
    rows = []

    for i in range(0, len(tokens), BATCH_SIZE):
        batch = tokens[i : i + BATCH_SIZE]
        try:
            resp = smart_api.getMarketData("FULL", {"NSE": batch})
        except Exception as e:
            print(f"[quotes] batch {i // BATCH_SIZE} failed: {e}")
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        if not resp or not resp.get("status"):
            print(f"[quotes] batch {i // BATCH_SIZE} returned error: {resp}")
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        fetched = resp.get("data", {}).get("fetched", [])
        rows.extend(fetched)

        if i + BATCH_SIZE < len(tokens):
            time.sleep(REQUEST_DELAY_SECONDS)

    if not rows:
        return pd.DataFrame()

    quotes_df = pd.DataFrame(rows)
    quotes_df["token"] = quotes_df["symbolToken"].astype(str)

    merged = universe.copy()
    merged["token"] = merged["token"].astype(str)
    merged = merged.merge(quotes_df, on="token", how="inner", suffixes=("", "_q"))

    numeric_cols = ["ltp", "open", "high", "low", "close", "avgPrice",
                     "netChange", "percentChange", "tradeVolume", "opnInterest"]
    for col in numeric_cols:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")

    merged = merged.rename(columns={"tradeVolume": "volume"})
    merged["turnover"] = merged["ltp"] * merged["volume"]
    merged["fetch_time"] = pd.Timestamp.now()

    return merged
