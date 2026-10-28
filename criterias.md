# How a Stock Ends Up in Each Table

This explains exactly what each table on the dashboard does, based on the code in `screeners.py`, `recommend.py` and `app.py`.

**Common to all tables**

- **Universe:** about 200 stocks (the Nifty 200 list). Nothing outside it is ever shown, however big its move.
- **Refresh:** every table is rebuilt from scratch on each refresh (default 20 s) from one fresh quote snapshot. There is no memory between refreshes, so stocks move in and out.
- **N (Rows per table, default 10):** each table shows at most N rows. The filter is applied first, then the top N are kept, so a table can have fewer than N rows.
- **Source fields (Angel One FULL quote):**
  - `ltp`: last traded price
  - `percentChange`: change versus the previous day's close
  - `volume`: shares traded today
  - `avgPrice`: today's VWAP (volume-weighted average price)
  - `open`, `close`: today's open and the previous session's close
- **Turnover:** `ltp × volume`, the rupee value traded today (shown in ₹ crore in the tables).

---

## 1. Top Gainers

| | |
|---|---|
| **Rule** | `percentChange > 0` |
| **Sorted by** | `percentChange`, highest first |
| **Shown** | Top N |

A stock is in the table if it is up on the day and is one of the N biggest risers in the universe.

## 2. Top Losers

| | |
|---|---|
| **Rule** | `percentChange < 0` |
| **Sorted by** | `percentChange`, lowest (most negative) first |
| **Shown** | Top N |

The mirror image of Top Gainers. A stock can never be in both.

## 3. Most Active

| | |
|---|---|
| **Rule** | none (every stock is eligible) |
| **Sorted by** | Turnover (`ltp × volume`), highest first |
| **Shown** | Top N |

This ranks by rupees traded, not share count, so expensive and heavily traded stocks rank high. The direction of the move doesn't matter, so a stock can be here and in Gainers, or here and in Losers.

## 4. High Volume

| | |
|---|---|
| **Rule** | none |
| **Sorted by** | `volume` (raw shares), highest first |
| **Shown** | Top N |

This favors low-priced stocks, because the same rupees buy more shares. It is not used for recommendations.

## 5. Volume Surge (RVOL)

| | |
|---|---|
| **Rule** | `RVOL >= min RVOL` (sidebar, default 2.0) |
| **Sorted by** | RVOL, highest first |
| **Shown** | Top N |

**RVOL (relative volume)** compares today's volume with what is normal by this time of day:

```
expected_volume_now = avg_volume_20d × (minutes since 09:15 IST ÷ 375)
RVOL                = today's volume ÷ expected_volume_now
```

- `avg_volume_20d` is the average daily volume over the previous ~20 trading days (today's candle excluded). It is loaded once per day.
- RVOL of 2.0 means the stock is trading at twice its normal pace.
- Stocks with no historical volume data are skipped.

**Caveat:** the formula assumes volume is spread evenly through the day, but volume is heaviest at the open. So RVOL looks inflated in the first hour and is most reliable from mid-morning. After 15:30 IST the full 375 minutes is used.

## 6. VWAP Tables (Above / Below)

First: `deviation % = (ltp − avgPrice) ÷ avgPrice × 100`. Stocks with no VWAP yet (`avgPrice <= 0`) are skipped.

| Table | Rule | Sorted by |
|---|---|---|
| **Above VWAP (bullish)** | deviation ≥ +threshold (sidebar, default 0.5%) | Largest positive deviation first |
| **Below VWAP (bearish)** | deviation ≤ −threshold | Most negative deviation first |

A stock well above VWAP means the day's volume-weighted buyers are in profit, which traders read as buyers being in control. Below VWAP is the opposite.

## 7. Gap Scanner

```
gap % = (today's open − previous close) ÷ previous close × 100
```

| | |
|---|---|
| **Rule** | `abs(gap %) >= min gap` (sidebar, default 2.0%) |
| **Direction** | "Gap Up" if the gap is positive, "Gap Down" if negative |
| **Sorted by** | Size of the gap, largest first (either direction) |

The table shows where the stock opened, not where it is now. A stock can gap up 3% and then fall back, so also check `ltp` and `percentChange`.

## 8. Range Breakout ↑ and Breakdown ↓

These use the **opening range**: the high and low of the first 15 minutes (09:15 to 09:30 IST, three 5-minute candles). It is loaded once, after 09:30 IST on trading days. Before then the tables show "Opening range forms at 09:30 IST."

| Table | Rule | Sorted by |
|---|---|---|
| **Range Breakout ↑** | `ltp > opening-range high` | `(ltp − or_high) ÷ or_high × 100`, largest first |
| **Range Breakdown ↓** | `ltp < opening-range low` | `(or_low − ltp) ÷ or_low × 100`, largest first |

Both hold the stocks currently trading beyond their first-15-minute range. A stock that broke out earlier and then fell back inside the range is no longer shown.

---

## 9. ⭐ Recommended Stocks

A stock is recommended when it appears in **at least `min tables` (sidebar, default 2)** of these six tables, each taken at its top N:

1. Top Gainers
2. Top Losers
3. Most Active
4. Volume Surge
5. Above VWAP (uses the sidebar's VWAP deviation threshold)
6. Below VWAP

The other tables (High Volume, Gap, Range Breakout) are **not** counted.

| Column | Meaning |
|---|---|
| `hits` | How many of the four tables the stock is in |
| `appears_in` | Which ones (for example "Gainers, Most Active, Vol Surge") |
| `bias` | **Bullish** if `percentChange > 0`, **Bearish** if `< 0`, otherwise Neutral |
| `vwap_pos` | **Above** or **Below** VWAP (or At / `-` if no VWAP yet) |
| `vwap_dev_pct` | How far price is from VWAP, in % |

Rows are sorted by `hits` (most first), then by the size of the day's move.

**The practical maximum for `hits` is 4.** Gainers and Losers are mutually exclusive, and so are Above VWAP and Below VWAP, so a stock can be in at most one of each pair plus Most Active and Volume Surge. Setting "Min. tables" to 4 shows only stocks in all four.

**Note on VWAP tables:** a stock up on the day is often above VWAP, so Gainers + Above VWAP (or Losers + Below VWAP) is a very common pairing and gives 2 hits easily. If the list is too long, raise "Min. tables" to 3.

**Example.** With N = 10 and RVOL threshold 2.0, suppose stock ABC is up 4.1% and is the 6th biggest gainer, the 3rd most active by turnover, and trading at 2.8× its normal volume pace (4th highest RVOL). It appears in Gainers, Most Active and Vol Surge, so `hits = 3`, `bias = Bullish`. If its price is above VWAP, `vwap_pos = Above`.

**Why a stock may be missing from the list:**
- It is in only one of the four tables.
- It is just outside the top N of one of them (try raising Rows per table).
- Its RVOL is under the threshold, or it has no volume history.
- It is not in the Nifty 200 list.

---

## 10. 🕘 Seen Today

A log of every stock that has ever been in the Recommended list today. It is updated on every refresh, **only while the market is open** (09:15 to 15:30 IST), and saved to `.cache/seen_<date>.json`.

| Column | Meaning |
|---|---|
| `status` | **On list** if in the Recommended table now, **Dropped** if not |
| `bias_when_seen`, `first_seen`, `first_price` | Bias, time (IST) and price when it first appeared. These never change after that. |
| `last_seen` | The last time it was on the list |
| `since_first_pct` | `(ltp − first_price) ÷ first_price × 100` |
| `in_bias_pct` | Same value, but flipped for Bearish stocks, so **+ means it moved the way its bias predicted** |
| `max_hits` | The highest `hits` it reached |
| `refreshes` | How many refresh cycles it was on the list |

---

## VWAP slope, angle and slope bias

Angel One only provides the *current* VWAP, not its history. So the dashboard records every stock's VWAP on each refresh (market hours only, saved to `.cache/vwap_hist_<date>.json`, last 60 minutes kept) and fits a straight line through the last **Slope window** minutes (sidebar, default 10).

```
slope % per minute = least-squares slope of VWAP ÷ average VWAP × 100
angle (degrees)    = atan( slope % per minute ÷ 0.02 )
```

- **The angle is a standardised scale, not a chart angle.** A real chart's angle depends on its zoom. Here a VWAP drift of 0.02 % per minute is defined as 45°. Flatter lines give smaller angles, steeper lines larger ones, and the angle never exceeds ±90°.
- **Trend:** angle ≥ +X° is **Rising**, ≤ −X° is **Falling**, otherwise **Flat** (X is the "Flat zone" slider, default 10°).
- **`slope_bias`:** Rising → **Bullish**, Falling → **Bearish**, Flat → **Neutral**. It shows "-" until enough history exists.
- **`confluence`** (Recommended table only):
  - **Aligned ↑**: `bias` is Bullish, `vwap_pos` is Above, and VWAP is Rising.
  - **Aligned ↓**: `bias` is Bearish, `vwap_pos` is Below, and VWAP is Falling.
  - **Mixed**: anything else. **No slope yet**: history not ready.
- **Warm-up:** each stock needs at least 3 readings covering about 60 % of the window. If you start the dashboard at 09:15, slopes appear from about 09:21 with a 10-minute window. The history file survives restarts within the same day.
- The VWAP angle also appears in the Above/Below VWAP tables.

---

## 🔎 Early Movers tab (the second tab)

The first tab mostly shows stocks *after* they moved. This tab looks for stocks that are **setting up** and haven't made the big move yet. It scans the whole universe, not just the top N of other tables.

**Step 1: the trend filter (must pass)**

| Side | Condition |
|---|---|
| **Long watch** | price above VWAP **and** VWAP slope is Rising |
| **Short watch** | price below VWAP **and** VWAP slope is Falling |

"Rising / Falling" uses the same VWAP slope and "Flat zone" setting as the rest of the dashboard, so this tab needs the slope warm-up (about 6 minutes after the market opens or after you start the dashboard).

**Step 2: "not already moved" gates (sidebar, all must pass)**

- Day move so far within **±2.0 %** (default), because the point is to catch it before the big move.
- Distance from VWAP within **1.5 %** (default), so price isn't already stretched away from VWAP.
- Turnover so far at least **₹5 crore** (default), so it's liquid enough to trade.

**Step 3: confluence signals (one point each)**

| Signal | Long | Short |
|---|---|---|
| Volume building | RVOL ≥ 1.5 | RVOL ≥ 1.5 |
| Opening-range edge | price within 0.3 % below to 1 % above the opening-range **high** | same at the opening-range **low** |
| Day high / low | in the top 20 % of today's range | in the bottom 20 % |
| Versus open | price above today's open | price below today's open |
| Steep VWAP | VWAP angle ≥ +25° | VWAP angle ≤ −25° |

Only stocks with at least **2 signals** (adjustable, 1 to 5) are shown. Ranking: most signals first, then the steeper VWAP angle.

**Columns**

- `score` and `signals`: how many and which confluences it has.
- `vwap_dev_pct`, `vwap_angle`, `vwap_trend`: distance from VWAP, slope of VWAP in degrees, and the trend label (Rising / Falling / Flat) from that angle.
- `day_range_pos`: where price sits between today's low (0 %) and high (100 %).
- `or_dist_pct`: distance beyond the opening-range high (long) or low (short). Negative means still inside the range, which is the "about to break out" zone.

**🕘 Early Movers Seen Today (below the two tables).** Once a stock appears in Long or Short watch it is added to this log and **stays there for the rest of the day**, even after it leaves the live tables.

| Column | Meaning |
|---|---|
| `side` | **Long** or **Short** watch. A stock that appears on both sides gets one row per side. |
| `status` | **On list** if it is in the live table now, **Dropped** if not |
| `first_seen`, `first_price`, `signals_when_first` | When, at what price and with which signals it first appeared (never changes) |
| `since_first_pct` | Price change since it first appeared |
| `in_side_pct` | Same value, but **+ means it moved the way the watch predicted** (up for Long, down for Short) |
| `first_angle` | VWAP angle (degrees) when it first appeared |
| `vwap_angle`, `vwap_trend` | VWAP angle and trend **now** (Rising / Falling / Flat, same Flat-zone setting). Shows whether the trend behind the pick is still intact, even after it has dropped off the list. |
| `max_score`, `refreshes` | Best confluence score it reached, and how many refresh cycles it stayed on the live list |

A stock that shows "Dropped" with a large positive `in_side_pct` is one the tab caught early and that then made its move (it left the live list because it had moved too much). Logged only while the market is open, saved to `.cache/early_seen_<date>.json`, and cleared with the sidebar button "Reset 'Early Movers seen' log". A new log starts each day.

**How to read it:** a stock with 4 or 5 signals and a small `percentChange` is the classic "coiled" setup: above a rising VWAP, volume picking up, pressing against the opening-range high. It's a watchlist, not a trigger. Check the chart for a clean entry and place a stop-loss before trading. Because it looks at early, small moves, it also produces more false starts than the first tab.

---

## ⚡ Opening Reversals tab (the third tab)

Built for the fast directional moves that start within minutes of the open: a stock **flushes** at the open (or opens soft), **reclaims its open**, and then pushes hard above a **rising VWAP**. The mirror image (spike and fail) is the short side. It only uses data the dashboard already has, so it works a few minutes after 09:15 IST.

**The slope is ready sooner here.** This tab uses its own short VWAP slope window (default **5 minutes**, sidebar), so it can appear about 09:19 to 09:21 IST if the dashboard has been running since the open. Start the dashboard before 09:15, because VWAP history is only recorded while the market is open.

**Must pass (long watch / short watch)**

| Condition | Long: dip & reclaim | Short: spike & fail |
|---|---|---|
| Opening gap | at most **+1 %** (no gap-ups) | at least **−1 %** (no gap-downs) |
| Day range so far | ≥ 1 % of previous close | same |
| Push off the extreme | ≥ 0.7 % up off the day's **low** | ≥ 0.7 % down off the day's **high** |
| Versus VWAP and open | price above VWAP **and** above today's open | below both |
| VWAP trend (short window) | Rising | Falling |
| Turnover so far | ≥ ₹3 crore | same |

A gap-down that recovers is allowed on the long side, and a gap-up that fades on the short side. Those are exactly the cases of a flush followed by a V-shaped recovery.

**One point per signal (default: need 2 of 5)**

| Signal | Long | Short |
|---|---|---|
| Volume surge | RVOL ≥ 3 | RVOL ≥ 3 |
| Flush / spike | dipped ≥ 0.5 % below the open, now back above it ("Flush & reclaim") | spiked ≥ 0.5 % above the open, now below it ("Spike & fail") |
| Gap filled / faded | gapped down and is now back at or above previous close | gapped up and is now back at or below previous close |
| Near the extreme | top 30 % of today's range | bottom 30 % |
| Steep VWAP | angle ≥ +25° | angle ≤ −25° |

**Columns:** `gap_pct` (open vs previous close), `push_pct` (distance moved off the low for longs, off the high for shorts), `flush_pct` (the dip for longs, the spike for shorts), then the usual `vwap_dev_pct`, `vwap_angle`, `vwap_trend`, `rvol`, `turnover_cr`. Ranking: score first, then `push_pct`.

**🕘 Opening Reversals Seen Today** (below the tables) works the same as the Early Movers log: every stock that appeared stays, with status On list / Dropped, `since_first_pct`, `in_side_pct`, and the VWAP angle and trend at first sight and now. It has its own file and its own reset button.

**Caveats**

- **RVOL is inflated in the first minutes.** At 09:21 the "expected volume so far" is tiny, so many stocks pass RVOL ≥ 3. In the first 30 minutes treat the volume signal as weak and lean on the other four.
- **A 5-minute slope is noisy.** It reacts fast, so it will sometimes flag moves that fade. Use the Seen log to see how often.
- **It can't see the move before the open.** It reacts to the dip and reclaim, so you are catching the first push, not predicting it.
- It is a watchlist: confirm on the chart and set a stop before entering.

---

## Things to keep in mind

- **Data is slightly delayed.** Each refresh takes about 5 seconds to fetch, so prices can be 5 to 25 seconds old.
- **Tables are rankings, not signals.** A stock being in a table means it is unusual right now. It does not mean it will keep moving.
- **Thresholds are adjustable** in the sidebar: min RVOL, VWAP deviation %, gap %, rows per table, and min tables for a recommendation.
- This dashboard is for your own analysis, not investment advice.