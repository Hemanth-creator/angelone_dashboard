"""
Indian Intraday Scanner - a live Streamlit dashboard over Angel One SmartAPI.

Run with:
    streamlit run app.py

Single-page layout: a "Recommended" table on top (stocks appearing in 2+ of
Gainers / Losers / Most Active / Volume Surge), then every screener in a grid,
then a "Seen today" log of every stock that was ever recommended.
Only the data panel refreshes (st.fragment), so the page never dims or reloads.
"""

import time
import warnings

import pandas as pd
import streamlit as st

# Comparing NaN (stocks with no volume/range data yet) is expected and harmless.
warnings.filterwarnings("ignore", message="invalid value encountered", category=RuntimeWarning)

from auth import AuthError, get_session
from universe import build_universe
from quotes import fetch_quotes
from historical import fetch_avg_volume_20d, fetch_opening_range, SessionExpired, DataFetchError
from ist_time import now_ist, market_open_today, market_close_today, is_market_hours, is_trading_day
from recommend import recommended, update_seen, seen_table, reset_seen
from vwap_slope import record_vwap, vwap_slopes, reset_history
from movers import early_movers, update_em_seen, em_seen_table, reset_em_seen
from firstpush import first_push
import screeners as scr

st.set_page_config(page_title="Indian Intraday Scanner", layout="wide", page_icon="📈")

# ---------------------------------------------------------------------------
# Styling - compact, and NO dimming while data refreshes
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 100%; }
    [data-testid="stMetricValue"] { font-size: 1.2rem; }
    h1 { font-size: 1.6rem !important; padding-bottom: 0 !important; }
    /* keep everything fully visible while the script/fragment is running */
    [data-stale="true"], .stale-element, div[data-stale="true"] {
        opacity: 1 !important; transition: none !important;
    }
    [data-testid="stStatusWidget"] { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


def color_pct(val):
    if pd.isna(val):
        return ""
    color = "#1fbf5c" if val > 0 else ("#e2493d" if val < 0 else "")
    return f"color: {color}; font-weight: 600;"


def color_label(val):
    if val in ("Bullish", "Gap Up", "Above", "On list", "Rising", "Aligned ↑", "Long"):
        return "color: #1fbf5c; font-weight: 700;"
    if val in ("Bearish", "Gap Down", "Below", "Falling", "Aligned ↓", "Short"):
        return "color: #e2493d; font-weight: 700;"
    return ""


def style_table(df: pd.DataFrame, pct_cols: list[str]):
    sty = df.style
    for col in pct_cols:
        if col in df.columns:
            sty = sty.map(color_pct, subset=[col])
    for col in ("bias", "direction", "vwap_pos", "status", "bias_when_seen",
                "slope_bias", "vwap_trend", "confluence", "side"):
        if col in df.columns:
            sty = sty.map(color_label, subset=[col])
    fmt = {}
    for col in df.columns:
        if col in ("ltp", "open", "high", "low", "close", "avgPrice", "or_high", "or_low",
                   "day_open", "prev_close", "first_price"):
            fmt[col] = "₹{:.2f}"
        elif col in ("percentChange", "vwap_dev_pct", "gap_pct", "pct_beyond_range",
                     "since_first_pct", "in_bias_pct", "in_side_pct", "push_pct", "flush_pct"):
            fmt[col] = "{:+.2f}%"
        elif col in ("volume", "avg_volume_20d", "turnover"):
            fmt[col] = "{:,.0f}"
        elif col == "turnover_cr":
            fmt[col] = "{:,.1f}"
        elif col == "rvol":
            fmt[col] = "{:.2f}x"
        elif col in ("vwap_angle", "first_angle"):
            fmt[col] = "{:+.1f}°"
        elif col == "day_range_pos":
            fmt[col] = "{:.0f}%"
        elif col == "or_dist_pct":
            fmt[col] = "{:+.2f}%"
    return sty.format(fmt, na_rep="-")


def show(title, df, pct_cols, cols=None, empty_msg="Nothing matching right now.", max_rows=None):
    """Render one titled, auto-height table (no inner scrolling unless max_rows is set)."""
    if title:
        st.markdown(f"**{title}**")
    if df is None or df.empty:
        st.caption(empty_msg)
        return
    df = df.copy()
    if "turnover" in df.columns:
        df["turnover_cr"] = df["turnover"] / 1e7  # rupees -> crore
    if cols:
        df = df[[c for c in cols if c in df.columns]]
    visible = len(df) if max_rows is None else min(len(df), max_rows)
    height = 35 * (visible + 1) + 3
    st.dataframe(style_table(df, pct_cols), width="stretch",
                 hide_index=True, height=height)


# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------
st.sidebar.title("⚙️ Scanner Controls")

st.sidebar.radio("Universe", ["Nifty 200 (recommended)"], index=0,
                 help="Stock universe scanned each cycle.")
refresh_secs = st.sidebar.slider("Auto-refresh interval (sec)", 10, 60, 20, step=5)
row_count = st.sidebar.slider("Rows per table", 5, 30, 10, step=5)
tables_per_row = st.sidebar.slider("Tables per row", 2, 4, 3)
st.sidebar.markdown("**Recommendation**")
min_lists = st.sidebar.slider("Min. tables a stock must appear in", 2, 4, 2)
st.sidebar.markdown("**Thresholds**")
rvol_threshold = st.sidebar.slider("Volume Surge: min RVOL", 1.5, 5.0, 2.0, step=0.5)
vwap_threshold = st.sidebar.slider("VWAP: min deviation %", 0.2, 3.0, 0.5, step=0.1)
gap_threshold = st.sidebar.slider("Gap scanner: min gap %", 0.5, 5.0, 2.0, step=0.5)
st.sidebar.markdown("**VWAP slope**")
slope_window = st.sidebar.slider("Slope window (minutes)", 5, 30, 10, step=5,
                                 help="How many recent minutes of VWAP the slope line is fitted to.")
flat_angle = st.sidebar.slider("Flat zone: ± angle (degrees)", 3, 30, 10,
                               help="VWAP angles between -X° and +X° count as Flat.")
st.sidebar.markdown("**Early Movers tab**")
em_max_move = st.sidebar.slider("Max day move so far (±%)", 0.5, 5.0, 2.0, step=0.5,
                                help="Only stocks that haven't made their big move yet.")
em_max_ext = st.sidebar.slider("Max distance from VWAP (%)", 0.5, 3.0, 1.5, step=0.5,
                               help="Skip stocks already stretched away from VWAP.")
em_min_rvol = st.sidebar.slider("Volume building: min RVOL", 1.0, 3.0, 1.5, step=0.5)
em_min_turnover = st.sidebar.slider("Min turnover so far (₹ crore)", 1, 50, 5)
em_min_score = st.sidebar.slider("Min confluence signals", 1, 5, 2,
                                 help="Out of 5: volume, opening-range edge, day high/low, vs open, steep VWAP.")
st.sidebar.markdown("**Opening Reversals tab**")
fp_window = st.sidebar.slider("VWAP slope window (min)", 3, 15, 5,
                              help="Short window so the slope is ready a few minutes after the open.")
fp_max_gap = st.sidebar.slider("Max opening gap (%)", 0.0, 3.0, 1.0, step=0.5,
                               help="Longs: gap must be at most this (no gap-ups). Shorts: at least minus this.")
fp_min_range = st.sidebar.slider("Min day range so far (% of prev close)", 0.5, 3.0, 1.0, step=0.5)
fp_min_push = st.sidebar.slider("Min push off the low/high (%)", 0.3, 3.0, 0.7, step=0.1)
fp_min_rvol = st.sidebar.slider("Volume surge: min RVOL", 2.0, 6.0, 3.0, step=0.5)
fp_min_turnover = st.sidebar.slider("Min turnover so far (₹ crore) ", 1, 30, 3)
fp_min_score = st.sidebar.slider("Min signals", 1, 5, 2, key="fp_min_score",
                                 help="Out of 5: volume, flush & reclaim, gap filled, near day high/low, steep VWAP.")

st.sidebar.markdown("---")
if st.sidebar.button("🗑 Reset 'Seen today' log"):
    reset_seen()
if st.sidebar.button("🗑 Reset VWAP slope history"):
    reset_history()
if st.sidebar.button("🗑 Reset 'Early Movers seen' log"):
    reset_em_seen()
if st.sidebar.button("🗑 Reset 'Opening Reversals seen' log"):
    reset_em_seen("firstpush_seen")
if st.sidebar.button("🔄 Force full refresh (re-login + reload universe)"):
    st.session_state.clear()
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()

st.sidebar.caption(
    "Data: Angel One SmartAPI (your account). Rankings are computed live from "
    "quotes across the universe. Screens are for your own analysis, not "
    "investment advice."
)

# ---------------------------------------------------------------------------
# Session / data loading
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner="Logging into Angel One...")
def _get_client():
    return get_session()


def _get_client_fresh():
    """Force a brand-new login (used for auto-recovery on an expired token)."""
    _get_client.clear()
    return get_session(force_relogin=True)


@st.cache_data(ttl=24 * 3600, show_spinner="Building Nifty 200 universe...")
def _get_universe():
    return build_universe()


def _progress_updater(bar, label):
    def _update(done, total, elapsed, remaining):
        mins, secs = divmod(int(remaining), 60)
        bar.progress(
            done / total,
            text=f"{label}: {done}/{total} stocks ({done/total*100:.0f}%) "
                 f"— elapsed {int(elapsed)}s, ETA ~{mins}m {secs}s",
        )
    return _update


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _get_avg_volume(_client, universe):
    smart_api, _, _ = _client
    progress = st.progress(0.0, text="Loading 20-day average volumes (one-time, usually 3-6 min)...")
    df = fetch_avg_volume_20d(smart_api, universe, progress_cb=_progress_updater(progress, "Average volume"))
    progress.empty()
    return df


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def _get_opening_range(_client, universe):
    smart_api, _, _ = _client
    progress = st.progress(0.0, text="Loading today's opening range (one-time)...")
    df = fetch_opening_range(smart_api, universe, minutes=15, progress_cb=_progress_updater(progress, "Opening range"))
    progress.empty()
    return df


EMPTY_OPENING_RANGE_COLS = ["name", "token", "or_high", "or_low", "day_open"]

# ---------------------------------------------------------------------------
# Market timing (IST)
# ---------------------------------------------------------------------------
OPENING_RANGE_MINUTES = 15
now_time_ist = now_ist()
opening_range_ready_at = market_open_today() + pd.Timedelta(minutes=OPENING_RANGE_MINUTES)
opening_range_has_formed = is_trading_day(now_time_ist) and now_time_ist >= opening_range_ready_at

# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
try:
    smart_api, feed_token, client_code = _get_client()
except AuthError as e:
    st.error(f"❌ Login failed: {e}")
    st.info("Check your `.env` file against `.env.example` and make sure your "
            "Angel One SmartAPI app, TOTP, and PIN are all set up correctly.")
    st.stop()

# Keep the live API handle in session_state so the refresh fragment can swap
# in a fresh login without a full page rerun.
st.session_state.setdefault("api", smart_api)

universe = _get_universe()
if universe.empty:
    with st.spinner("Universe came back empty, retrying..."):
        time.sleep(3)
        _get_universe.clear()
        universe = _get_universe()
    if universe.empty:
        st.error("Could not build a stock universe. Check your internet connection and reload.")
        st.stop()

EMPTY_AVG_VOLUME_COLS = ["name", "token", "avg_volume_20d", "prev_close"]


def _load_hist(loader, empty_cols, label):
    """Run a historical loader; on an expired token log in again once; never crash the page."""
    client = (smart_api, feed_token, client_code)
    for attempt in range(2):
        try:
            return loader(client, universe), client
        except SessionExpired:
            if attempt == 0:
                client = _get_client_fresh()
                continue
            st.warning(f"Could not load {label}: Angel One keeps rejecting the login token.")
        except DataFetchError as e:
            st.warning(f"Could not load {label}: {e}")
        break
    return pd.DataFrame(columns=empty_cols), client


avg_volume, (smart_api, feed_token, client_code) = _load_hist(
    _get_avg_volume, EMPTY_AVG_VOLUME_COLS, "20-day average volume (Volume Surge will be empty)")

if opening_range_has_formed:
    opening_range, (smart_api, feed_token, client_code) = _load_hist(
        _get_opening_range, EMPTY_OPENING_RANGE_COLS, "the opening range (Range Breakout will be empty)")
else:
    opening_range = pd.DataFrame(columns=EMPTY_OPENING_RANGE_COLS)

st.session_state["api"] = smart_api  # the live panel uses the latest working session

st.title("📈 Indian Intraday Scanner")


# ---------------------------------------------------------------------------
# Live panel: only this fragment re-runs every refresh_secs, so the rest of
# the page stays put and nothing dims.
# ---------------------------------------------------------------------------
@st.fragment(run_every=refresh_secs)
def live_panel():
    now = now_ist()
    market_open = is_market_hours(now)

    # Opening range becomes available mid-session: do one full rerun to load it.
    if (not opening_range_has_formed) and is_trading_day(now) and now >= opening_range_ready_at:
        st.rerun(scope="app")

    api = st.session_state["api"]
    live = fetch_quotes(api, universe)
    if live.empty:
        try:
            api, _, _ = _get_client_fresh()
            st.session_state["api"] = api
            live = fetch_quotes(api, universe)
        except AuthError as e:
            st.error(f"Re-login failed: {e}")
            return

    if live.empty:
        if market_open:
            st.error("No live quote data returned. Retrying on the next refresh.")
        else:
            st.info("No quote data available right now. Retrying on the next refresh.")
        return

    now = now_ist()
    # VWAP slope: record this refresh's VWAP (market hours only), then fit the recent trend.
    vwap_hist = record_vwap(live, record=market_open)
    slopes = vwap_slopes(vwap_hist, window_min=slope_window, flat_deg=flat_angle)
    live = live.merge(slopes[["name", "vwap_angle", "vwap_trend"]], on="name", how="left")

    rec = recommended(live, avg_volume, n=row_count, min_rvol=rvol_threshold, min_lists=min_lists,
                      min_vwap_dev=vwap_threshold)
    # Log picks only while the market is open, so after-hours snapshots don't pollute the day's log.
    seen = update_seen(rec, record=market_open)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Market", "🟢 OPEN" if market_open else "🔴 CLOSED")
    m2.metric("Last update (IST)", now.strftime("%H:%M:%S"))
    m3.metric("Universe", len(universe))
    m4.metric("Recommended", len(rec))
    if not market_open:
        st.caption("Market closed (NSE 9:15–15:30 IST, Mon–Fri) — showing the last available quotes.")

    tab_main, tab_early, tab_fp = st.tabs(["⭐ Recommended & Screeners", "🔎 Early Movers", "⚡ Opening Reversals"])

    with tab_main:
        # ---- Recommended ----
        st.markdown("### ⭐ Recommended Stocks")
        st.caption(f"Stocks appearing in {min_lists}+ of: Top Gainers, Top Losers, Most Active, Volume Surge, "
                   f"Above VWAP, Below VWAP "
                   f"(top {row_count} of each). **bias** = today's price change; **vwap_angle / slope_bias** = "
                   f"direction of the VWAP line over the last {slope_window} min (needs a few minutes of "
                   f"history after start-up); **confluence** = Aligned ↑/↓ when bias, price-vs-VWAP and "
                   f"VWAP slope all agree.")
        show("", rec, ["percentChange", "vwap_angle"],
             cols=["name", "bias", "vwap_pos", "vwap_angle", "slope_bias", "confluence", "hits",
                   "appears_in", "ltp", "avgPrice", "vwap_dev_pct", "percentChange",
                   "turnover_cr", "volume", "rvol"],
             empty_msg="No stock is in 2+ tables right now. Try raising 'Rows per table' or lowering the RVOL threshold.")

        # ---- Screener grid ----
        gainers = scr.top_gainers(live, n=row_count)
        losers = scr.top_losers(live, n=row_count)
        active = scr.most_active(live, n=row_count)
        high_vol = scr.high_volume(live, n=row_count)
        surge = scr.volume_surge(live, avg_volume, min_rvol=rvol_threshold, n=row_count)
        above, below = scr.vwap_crossover(live, min_deviation_pct=vwap_threshold, n=row_count)
        slope_cols = slopes[["name", "vwap_angle", "vwap_trend"]]
        above = above.merge(slope_cols, on="name", how="left")
        below = below.merge(slope_cols, on="name", how="left")
        gap_df, orb_up, orb_down = scr.gap_and_opening_range(
            live, opening_range, min_gap_pct=gap_threshold, n=row_count)

        orb_msg = (f"Opening range forms at {opening_range_ready_at.strftime('%H:%M')} IST."
                   if not opening_range_has_formed else "Nothing matching right now.")

        base = ["name", "ltp", "percentChange"]
        panels = [
            ("🟢 Top Gainers", gainers, ["percentChange"], base + ["turnover_cr"], None),
            ("🔴 Top Losers", losers, ["percentChange"], base + ["turnover_cr"], None),
            ("💰 Most Active (₹ Cr traded)", active, ["percentChange"], base + ["turnover_cr"], None),
            ("⚡ Volume Surge (RVOL)", surge, ["percentChange"], base + ["rvol"], None),
            ("📊 High Volume (shares)", high_vol, ["percentChange"], base + ["volume"], None),
            ("🌅 Gap Scanner", gap_df, ["gap_pct", "percentChange"],
             ["name", "direction", "gap_pct", "ltp", "percentChange"], None),
            ("📐 Above VWAP (bullish)", above, ["vwap_dev_pct", "vwap_angle"],
             ["name", "ltp", "vwap_dev_pct", "vwap_angle", "vwap_trend"], None),
            ("📐 Below VWAP (bearish)", below, ["vwap_dev_pct", "vwap_angle"],
             ["name", "ltp", "vwap_dev_pct", "vwap_angle", "vwap_trend"], None),
            ("🚀 Range Breakout ↑", orb_up, ["pct_beyond_range"],
             ["name", "ltp", "or_high", "pct_beyond_range"], orb_msg),
            ("🔻 Range Breakdown ↓", orb_down, ["pct_beyond_range"],
             ["name", "ltp", "or_low", "pct_beyond_range"], orb_msg),
        ]

        st.markdown("### 📋 Screeners")
        for i in range(0, len(panels), tables_per_row):
            cols = st.columns(tables_per_row)
            for col, (title, df, pcts, view, msg) in zip(cols, panels[i:i + tables_per_row]):
                with col:
                    kwargs = {"empty_msg": msg} if msg else {}
                    show(title, df, pcts, cols=view, **kwargs)

        # ---- Seen today (below everything) ----
        st.markdown("### 🕘 Seen Today")
        st.caption("Every stock that appeared in the Recommended list today, newest first. "
                   "**since_first_pct** = price change since it first appeared; **in_bias_pct** = same, "
                   "but + means it moved the way its bias predicted (up for Bullish, down for Bearish). "
                   "**refreshes** = number of refresh cycles it stayed on the list. "
                   "Logged only while the market is open.")
        show("", seen_table(seen, live, set(rec["name"])),
             ["since_first_pct", "in_bias_pct"],
             cols=["name", "status", "bias_when_seen", "first_seen", "last_seen", "first_price", "ltp",
                   "since_first_pct", "in_bias_pct", "max_hits", "refreshes"],
             empty_msg="Nothing logged yet. Picks are recorded here while the market is open (09:15–15:30 IST).",
             max_rows=15)

    with tab_early:
        long_df, short_df, em = early_movers(
            live, avg_volume, opening_range, max_move=em_max_move, min_rvol=em_min_rvol,
            min_turnover_cr=em_min_turnover, max_ext=em_max_ext, min_score=em_min_score, n=row_count * 2)
        st.markdown("### 🔎 Early Movers — setting up, not yet moved")
        st.caption(
            f"Long watch = price **above VWAP and VWAP rising**; short watch = **below VWAP and VWAP falling**. "
            f"Only stocks that have moved less than ±{em_max_move}% today, sit within {em_max_ext}% of VWAP "
            f"and have traded ≥ ₹{em_min_turnover} Cr. Each gets a point per confluence signal "
            f"(volume building, at the opening-range edge, near day high/low, vs today's open, steep VWAP) "
            f"and needs ≥ {em_min_score}.")
        if em["slope_ready"] == 0:
            st.info("VWAP slope isn't ready yet. It needs about "
                    f"{int(slope_window * 0.6) + 1} minutes of readings after the market opens "
                    "(or after you start the dashboard). Slope history is saved, so restarts mid-day are fine.")
        else:
            st.caption(f"{em['slope_ready']} stocks have a VWAP slope · {em['long_pass']} pass the long trend "
                       f"filter · {em['short_pass']} pass the short trend filter.")
        if not opening_range_has_formed:
            st.caption(f"Opening-range signal starts after {opening_range_ready_at.strftime('%H:%M')} IST.")
        view = ["name", "score", "signals", "ltp", "percentChange", "vwap_dev_pct", "vwap_angle",
                "vwap_trend", "rvol", "day_range_pos", "or_dist_pct", "turnover_cr"]
        show("🟢 Long watch (above VWAP, rising)", long_df, ["percentChange", "vwap_angle", "or_dist_pct"],
             cols=view, empty_msg="No stock meets the long conditions right now. Try lowering the minimum "
                                  "signals or raising the max day move in the sidebar.")
        show("🔴 Short watch (below VWAP, falling)", short_df, ["percentChange", "vwap_angle", "or_dist_pct"],
             cols=view, empty_msg="No stock meets the short conditions right now.")
        st.caption("**or_dist_pct**: distance beyond the opening-range high (long) or low (short); "
                   "negative = still inside the range. **day_range_pos**: where price sits in today's "
                   "low–high range (100% = at the high).")

        # ---- Sticky log: stays here even after a stock leaves the two tables above ----
        em_seen = update_em_seen(long_df, short_df, record=market_open)
        em_seen_df = em_seen_table(em_seen, live, long_df, short_df)
        st.markdown("### 🕘 Early Movers Seen Today")
        st.caption("Every stock that has appeared in Long or Short watch today, newest first. It stays here "
                   "after dropping out of the tables above (**status** = Dropped). **since_first_pct** = price "
                   "change since it first appeared; **in_side_pct** = same, but + means it moved the way the "
                   "watch predicted (up for Long, down for Short). **first_angle** = VWAP angle when it first "
                   "appeared; **vwap_angle / vwap_trend** = the VWAP angle and trend *now* (Rising / Falling / "
                   "Flat), so you can see if the trend behind a pick has faded. Logged only while the market is open.")

        em_signal_options = sorted({
            sig.strip() for sigs in em_seen_df["signals_when_first"].dropna()
            for sig in str(sigs).split(",") if sig.strip()
        })
        em_default_signals = [s for s in em_signal_options if s == "Volume building"] or em_signal_options
        em_selected_signals = st.multiselect(
            "Filter by signal (when first seen)", em_signal_options, default=em_default_signals,
            key="em_seen_signal_filter",
            help="Defaults to 'Volume building' only. Clear the filter or pick other signals to see the rest.",
        )
        if em_selected_signals:
            em_seen_df = em_seen_df[em_seen_df["signals_when_first"].apply(
                lambda sigs: any(sig in str(sigs) for sig in em_selected_signals)
            )]

        show("", em_seen_df,
             ["since_first_pct", "in_side_pct", "first_angle", "vwap_angle"],
             cols=["name", "signals_when_first", "side", "status", "first_seen", "last_seen", "first_price",
                   "ltp", "since_first_pct", "in_side_pct", "first_angle", "vwap_angle", "vwap_trend",
                   "max_score", "refreshes"],
             empty_msg="Nothing matches this filter. Try selecting more signals above, or nothing was "
                       "logged yet (recorded while the market is open, 09:15–15:30 IST).",
             max_rows=15)

    with tab_fp:
        fp_slopes = vwap_slopes(vwap_hist, window_min=fp_window, flat_deg=flat_angle)
        live_fp = live.drop(columns=["vwap_angle", "vwap_trend"], errors="ignore").merge(
            fp_slopes[["name", "vwap_angle", "vwap_trend"]], on="name", how="left")
        fp_long, fp_short, fp = first_push(
            live_fp, avg_volume, max_gap=fp_max_gap, min_range=fp_min_range, min_push=fp_min_push,
            min_rvol=fp_min_rvol, min_turnover_cr=fp_min_turnover, min_score=fp_min_score, n=row_count * 2)
        st.markdown("### ⚡ Opening Reversals — dip & reclaim, spike & fail")
        st.caption(
            f"For the fast directional moves that start in the first minutes: a stock that flushed (or opened soft), "
            f"reclaimed its open and is **pushing above a rising VWAP** (long), or the mirror image (short). "
            f"Not gap-ups: the opening gap must be ≤ {fp_max_gap}% (long) or ≥ −{fp_max_gap}% (short). Needs a day "
            f"range of ≥ {fp_min_range}%, a push of ≥ {fp_min_push}% off the low/high, ≥ ₹{fp_min_turnover} Cr traded, "
            f"and a {fp_window}-minute VWAP slope. Points for: volume surge, flush & reclaim, gap filled, "
            f"near the day's high/low, steep VWAP; needs ≥ {fp_min_score}.")
        if fp["slope_ready"] == 0:
            st.info(f"The {fp_window}-minute VWAP slope isn't ready yet. If the dashboard has been running since the "
                    f"open, it shows up about {max(int(fp_window * 0.6) + 1, 3) + 1} minutes after 09:15 IST "
                    "(about 09:19 to 09:21). Start the dashboard before 09:15 so it records from the first tick.")
        else:
            st.caption(f"{fp['slope_ready']} stocks have a slope · {fp['long_pass']} pass the long filter · "
                       f"{fp['short_pass']} pass the short filter.")
        fp_view = ["name", "score", "signals", "ltp", "gap_pct", "percentChange", "push_pct", "flush_pct",
                   "vwap_dev_pct", "vwap_angle", "vwap_trend", "rvol", "turnover_cr"]
        fp_pcts = ["gap_pct", "percentChange", "vwap_angle"]
        show("🟢 Dip & reclaim (long watch)", fp_long, fp_pcts, cols=fp_view,
             empty_msg="Nothing matches yet. Try lowering min signals, min RVOL or min push in the sidebar.")
        show("🔴 Spike & fail (short watch)", fp_short, fp_pcts, cols=fp_view,
             empty_msg="Nothing matches yet.")
        st.caption("**gap_pct**: today's open vs yesterday's close. **push_pct**: how far price has moved off the "
                   "day's low (long) or high (short). **flush_pct**: how far the open was from the extreme "
                   "(the dip for longs, the spike for shorts).")

        fp_seen = update_em_seen(fp_long, fp_short, record=market_open, prefix="firstpush_seen")
        st.markdown("### 🕘 Opening Reversals Seen Today")
        st.caption("Every stock that has appeared in the two tables above today, newest first, kept even after it "
                   "drops out (**status** = Dropped). **in_side_pct** + means it moved the way the watch predicted. "
                   "**first_angle** vs **vwap_angle / vwap_trend** show whether the VWAP trend behind it has faded. "
                   "Logged only while the market is open.")
        show("", em_seen_table(fp_seen, live_fp, fp_long, fp_short),
             ["since_first_pct", "in_side_pct", "first_angle", "vwap_angle"],
             cols=["name", "side", "status", "first_seen", "last_seen", "first_price", "ltp",
                   "since_first_pct", "in_side_pct", "first_angle", "vwap_angle", "vwap_trend",
                   "max_score", "refreshes", "signals_when_first"],
             empty_msg="Nothing logged yet. Stocks are recorded here while the market is open (09:15–15:30 IST).",
             max_rows=15)

    st.caption(f"Auto-refreshing every {refresh_secs}s · only this panel reloads. "
               f"Gap & range-breakout use the first {OPENING_RANGE_MINUTES}-min range.")


live_panel()