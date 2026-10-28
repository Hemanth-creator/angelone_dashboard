# Indian Intraday Scanner (Angel One SmartAPI)

A local Streamlit dashboard for day-trading NSE stocks: Top Gainers, Top
Losers, Most Active, High Volume, Volume Surge (RVOL), VWAP crossover, and a
Gap / Opening-Range Breakout scanner — all computed live from your Angel One
SmartAPI account.

**This runs on your own machine**, not in the cloud — your API key and login
PIN never leave your computer.

## 1. One-time Angel One / SmartAPI setup

1. Make sure you have an active Angel One trading account.
2. Go to **https://smartapi.angelone.in/**, log in, and create an app
   (choose "Market Data" or "Trading" app type — either works for this
   dashboard, which is read-only). This gives you an **API key**. SmartAPI
   access itself is free.
3. Go to **https://smartapi.angelbroking.com/enable-totp** and set up TOTP
   for API login. Scan the QR code into an authenticator app, **and also
   copy the plain-text secret key shown on that page** — you'll need it in
   step 5 below (the dashboard generates TOTP codes itself so it can log in
   unattended on each restart).
4. Note your **client code** (your Angel One login ID, like `A123456`) and
   your **4-digit PIN** (not your trading password).

## 2. Install

Requires Python 3.9+.

```bash
cd angelone_dashboard
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` and fill in `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_PIN`, and
`ANGEL_TOTP_SECRET` from step 1. **Never share this file or commit it** —
`.gitignore` already excludes it.

## 4. Run

```bash
streamlit run app.py
```

It opens at `http://localhost:8501`. First run takes ~1-2 minutes extra to
download the instrument master and build 20-day average-volume data for the
Nifty 200 (needed for the Volume Surge screener) — this is cached and only
redone once per calendar day after that.

## What each tab shows

| Tab | What it ranks by | Why it's useful |
|---|---|---|
| **Top Gainers / Losers** | % change from previous close | Momentum at a glance |
| **Most Active** | Turnover (price × volume) | Where real rupee money is flowing — the standard "most active" definition |
| **High Volume** | Raw share volume | Catches high-volume, low-price moves that turnover ranking can underweight |
| **Volume Surge** | RVOL (today's volume ÷ expected volume by this time of day, from the 20-day average) | The single best "something unusual is happening here" signal — news, breakout, institutional interest |
| **VWAP** | % deviation of price from today's VWAP, split into above/below | Tells you which side (bulls/bears) is in control intraday — a standard scalping/continuation reference |
| **Gap & Range Breakout** | Gap % vs yesterday's close, and price beyond the first 15-min high/low | Classic "gap and go" / opening-range-breakout setups |

## Important notes & limitations

- **Angel One has no native equity-cash "top gainers/losers" API** — that
  endpoint only covers F&O (derivatives) contracts. Everything here is
  computed by pulling live quotes for the Nifty 200 and ranking them
  ourselves, which is actually more flexible (you control the universe and
  thresholds).
- **Universe = Nifty 200** by default, re-derived from NSE's live index
  list when reachable, with a bundled fallback list if NSE blocks the
  request. NSE reshuffles index constituents twice a year (end of March /
  September) — if your results look stale, delete `.cache/nifty200.json`
  and restart, or run `python universe.py --refresh`.
- **Rate limits**: live quotes are fetched in batches of 50 symbols at
  ~1 request/second (Angel One's documented limit), so a full Nifty 200
  refresh cycle takes ~4-5 seconds. Historical data (used only once/day for
  average volume and the opening range) is paced at ~3 requests/second.
- **Auto-refresh** uses a simple `time.sleep()` + rerun loop (default every
  20s, adjustable in the sidebar). This briefly blocks the UI during the
  sleep — fine for personal single-user use. If you want a non-blocking
  refresh, swap in the `streamlit-autorefresh` package.
- **This is a research/screening tool, not a trading signal generator or
  advice.** It surfaces stocks worth a closer look; the entry/exit decision
  is yours.
- Angel One sessions are valid for part of a trading day; the app caches
  your login for the calendar day. If quotes stop updating, use the sidebar
  "Force full refresh" button to re-login.

## File overview

```
auth.py          Angel One login (API key + TOTP), session caching
universe.py       Builds the Nifty 200 symbol -> token universe
quotes.py         Batched live quote fetching (FULL mode)
historical.py     20-day avg volume + opening-range data (cached daily)
screeners.py      All screener ranking logic (pure pandas, no Streamlit)
app.py            Streamlit dashboard UI
```
