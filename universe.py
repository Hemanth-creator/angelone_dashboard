"""
Builds the tradable stock universe (symbol -> NSE token) the dashboard scans.

Two layers:
  1. Angel One's instrument master (OpenAPIScripMaster.json) - the authoritative
     symbol -> token mapping needed for every market-data call.
  2. The Nifty 200 constituent list - which of those symbols to actually scan.
     NSE reshuffles index constituents twice a year (end of March / September),
     so this tries to pull the live list from NSE first and falls back to a
     bundled static list (captured below) if NSE blocks the request (it's
     notoriously strict about headers/cookies from non-browser clients).

Everything is cached to disk for 24h so you're not re-downloading a multi-MB
JSON file on every Streamlit rerun.
"""

import json
import time
from pathlib import Path

import pandas as pd
import requests

CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
NSE_NIFTY200_CSV = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"

SCRIP_MASTER_CACHE = CACHE_DIR / "scrip_master.json"
UNIVERSE_CACHE = CACHE_DIR / "nifty200.json"
CACHE_TTL_SECONDS = 24 * 60 * 60

# Fallback Nifty 200 list (trading symbols as listed on NSE, Angel One suffixes
# these with "-EQ"). Captured as a static backstop in case the live NSE fetch
# is blocked. Re-run `python universe.py --refresh` periodically, or update
# this list by hand from https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-200
NIFTY_200_FALLBACK = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "ITC",
    "SBIN", "LT", "HINDUNILVR", "BAJFINANCE", "KOTAKBANK", "AXISBANK",
    "MARUTI", "SUNPHARMA", "HCLTECH", "ASIANPAINT", "TITAN", "ULTRACEMCO",
    "ADANIENT", "ADANIPORTS", "NTPC", "TATAMOTORS", "ONGC", "POWERGRID",
    "WIPRO", "M&M", "JSWSTEEL", "TATASTEEL", "BAJAJFINSV", "COALINDIA",
    "NESTLEIND", "HDFCLIFE", "SBILIFE", "GRASIM", "TECHM", "INDUSINDBK",
    "DRREDDY", "CIPLA", "EICHERMOT", "BRITANNIA", "APOLLOHOSP", "DIVISLAB",
    "BAJAJ-AUTO", "HEROMOTOCO", "BPCL", "TATACONSUM", "UPL", "HINDALCO",
    "SHRIRAMFIN", "LTIM", "DLF", "PIDILITIND", "VEDANTA", "GODREJCP",
    "SIEMENS", "AMBUJACEM", "HAVELLS", "BANKBARODA", "PNB", "CANBK",
    "IOC", "GAIL", "INDIGO", "TRENT", "ZOMATO", "JIOFIN", "IRFC",
    "TVSMOTOR", "BOSCHLTD", "CHOLAFIN", "ICICIPRULI", "ICICIGI", "MARICO",
    "DABUR", "COLPAL", "BERGEPAINT", "MUTHOOTFIN", "SRF", "PAGEIND",
    "TORNTPHARM", "LUPIN", "AUROPHARMA", "ALKEM", "MCDOWELL-N", "UBL",
    "NAUKRI", "PERSISTENT", "COFORGE", "MPHASIS", "OFSS", "LTTS",
    "POLYCAB", "ABB", "CUMMINSIND", "SIEMENS", "BEL", "HAL", "BHEL",
    "SAIL", "NMDC", "JINDALSTEL", "NATIONALUM", "HINDZINC", "RECLTD",
    "PFC", "BANKINDIA", "IDFCFIRSTB", "FEDERALBNK", "AUBANK", "BANDHANBNK",
    "RBLBANK", "YESBANK", "IDEA", "MOTHERSON", "ASHOKLEY", "BALKRISIND",
    "MRF", "APOLLOTYRE", "EXIDEIND", "BHARATFORG", "SONACOMS", "SUPREMEIND",
    "ASTRAL", "DIXON", "AMBER", "KPITTECH", "TATAELXSI", "CYIENT",
    "ZYDUSLIFE", "GLENMARK", "IPCALAB", "LAURUSLABS", "BIOCON", "ABBOTINDIA",
    "SANOFI", "GLAXO", "PGHH", "GILLETTE", "HONAUT", "WHIRLPOOL",
    "VOLTAS", "BLUESTARCO", "CROMPTON", "VGUARD", "FINEORG", "DEEPAKNTR",
    "ATUL", "AARTIIND", "NAVINFLUOR", "GNFC", "GUJGASLTD", "IGL",
    "MGL", "PETRONET", "OIL", "GSPL", "CONCOR", "GMRAIRPORT",
    "ADANIGREEN", "ADANIPOWER", "ADANIENSOL", "ADANITRANS", "TATAPOWER",
    "NHPC", "SJVN", "TORNTPOWER", "CESC", "JSWENERGY", "INDHOTEL",
    "IRCTC", "IEX", "CDSL", "BSE", "MCX", "ANGELONE", "CAMS",
    "NUVAMA", "MFSL", "SBICARD", "PFIZER", "FORTIS", "MAXHEALTH",
    "SYNGENE", "GRANULES", "AJANTPHARM", "NATCOPHARM", "JUBLFOOD",
    "VBL", "RADICO", "EMAMILTD", "GODREJPROP", "OBEROIRLTY", "PHOENIXLTD",
    "PRESTIGE", "LODHA", "IDFC", "PAYTM", "POLICYBZR", "NYKAA",
    "DELHIVERY", "KALYANKJIL", "METROPOLIS", "LALPATHLAB", "SUNTV",
    "ZEEL", "PVRINOX", "ESCORTS", "CGPOWER", "THERMAX", "KEI",
    "RVNL", "IRB", "GICRE", "NIACL", "STARHEALTH", "LICI",
]


def _cache_fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL_SECONDS


def _download_scrip_master() -> list[dict]:
    if _cache_fresh(SCRIP_MASTER_CACHE):
        return json.loads(SCRIP_MASTER_CACHE.read_text())

    resp = requests.get(SCRIP_MASTER_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    SCRIP_MASTER_CACHE.write_text(json.dumps(data))
    return data


def _fetch_live_nifty200_symbols() -> list[str] | None:
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "text/csv,*/*",
        }
        # NSE requires a warm-up hit to the main site to set cookies first
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        resp = session.get(NSE_NIFTY200_CSV, headers=headers, timeout=15)
        resp.raise_for_status()
        from io import StringIO

        df = pd.read_csv(StringIO(resp.text))
        col = "Symbol" if "Symbol" in df.columns else df.columns[2]
        return df[col].dropna().str.strip().tolist()
    except Exception:
        return None


def build_universe(refresh: bool = False) -> pd.DataFrame:
    """
    Returns a DataFrame: symbol, token, exchange, lotsize
    for every Nifty 200 stock Angel One has a cash-equity (EQ) listing for.
    """
    if not refresh and _cache_fresh(UNIVERSE_CACHE):
        return pd.DataFrame(json.loads(UNIVERSE_CACHE.read_text()))

    symbols = None if refresh else None
    live = _fetch_live_nifty200_symbols()
    symbols = live if live else NIFTY_200_FALLBACK
    source = "NSE live list" if live else "bundled fallback list"

    scrip_master = _download_scrip_master()
    sm_df = pd.DataFrame(scrip_master)
    # Angel One tags cash-equity NSE listings with exch_seg == "NSE" and
    # symbol like "RELIANCE-EQ"
    eq = sm_df[(sm_df["exch_seg"] == "NSE") & (sm_df["symbol"].str.endswith("-EQ"))].copy()
    eq["base_symbol"] = eq["symbol"].str.replace("-EQ", "", regex=False)

    matched = eq[eq["base_symbol"].isin(symbols)][
        ["base_symbol", "symbol", "token", "exch_seg", "lotsize"]
    ].drop_duplicates(subset="base_symbol")
    matched = matched.rename(columns={"base_symbol": "name", "symbol": "trading_symbol", "exch_seg": "exchange"})

    missing = sorted(set(symbols) - set(matched["name"]))
    if missing:
        print(f"[universe] Note: {len(missing)} symbols not matched in scrip master "
              f"(delisted/renamed?): {missing[:10]}{'...' if len(missing) > 10 else ''}")

    UNIVERSE_CACHE.write_text(matched.to_json(orient="records"))
    print(f"[universe] Built universe of {len(matched)} stocks from {source}.")
    return matched.reset_index(drop=True)


if __name__ == "__main__":
    import sys

    force = "--refresh" in sys.argv
    df = build_universe(refresh=force)
    print(df.head(20))
    print(f"\nTotal: {len(df)} stocks")
