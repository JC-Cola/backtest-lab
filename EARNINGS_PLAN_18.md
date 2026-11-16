# Study 18: post-earnings-announcement drift (PEAD) on the news-scan universe (pre-registered)

Written and committed **before** any Finnhub surprise history was downloaded for this study and before any result of this
study was computed. (Only endpoint availability and history depth were probed beforehand: see "Data".)

## Question

Josh asked for Finnhub as a data source. Does a top- or bottom-decile EPS surprise predict drift in the same direction after
the announcement reaction (classic PEAD, Ball and Brown 1968; Bernard and Thomas 1989), after costs, in current large caps?

**Null:** the 5/20/60-session open-to-open return after the reaction day, net of 0.05% per side, of top-decile-surprise longs
(and bottom-decile shorts), is no better than that of randomly chosen events from the same sample (coin flip).

## Data and its limits (this decides the design)

* Surprises: Finnhub `/stock/earnings` (free plan). Probed before this plan: **4 quarters per symbol** (NVDA: periods
  2025-12-31 .. 2026-09-30) and `/calendar/earnings` returned nothing before 2026-10-01. So there are **no tune and test
  years**; a 2016-2020 / 2021+ split is impossible and nothing can be "confirmed". **The study is descriptive only**; the
  most it can say is "suggestive" or "nothing visible", and no live rule may be built from it.
* Universe: `agent_memory/universe.csv` (S&P 500 + Nasdaq-100 that Alpaca lets us trade; today's membership, so survivorship
  and look-ahead in membership are accepted and stated). Prices: Alpaca daily bars (`news_scan.fetch_bars`, split adjusted).
* Event = one `/stock/earnings` row with `surprisePercent` defined and |estimate| >= 0.05 (tiny denominators dropped).
* Announcement date is not in the surprise row. **Reaction day R** = the first session in [period end + 10 days, period end +
  75 days] whose volume is >= 2.0 x the mean of the prior 20 sessions (known at R's close, no look-ahead). Events with no such
  session are dropped and counted. Check (descriptive): for events in the calendar window (2026-10-01 on), the share where R
  equals the calendar date (bmo) or the session after it (amc).

## Procedure (all fixed now, nothing tuned)

* Surprise = `surprisePercent`. Decile cut-offs are the 10th and 90th percentile of the pooled surprise of all kept events
  (depends on surprises only, never on returns). Long the top decile, short the bottom decile.
* Entry: OPEN of the session after R. Exit: open h sessions later, h in {5, 20, 60}. Costs 0.05% per side (0.10% per round
  trip). Events whose exit is not yet realised are excluded for that h and counted.
* Per (side, h): n, mean net return, mean excess over SPY for the same window, hit rate, t-stat of the mean excess, and the
  long-minus-short spread of the two means.
* **Coin-flip:** 10,000 random draws of the same number of events from all kept events (any decile), same side, same
  windows; p = (1 + #draws with mean excess >= observed)/(10,001) for longs, <= for shorts (short returns signed). Seed 0.
* **Multiple tests:** the family is the 6 tests (long and short x 3 holds); Benjamini-Hochberg q over those 6.
* **Consistency (not a test split):** events split by R date into earlier and later halves, same statistics; a drift that
  flips sign between halves is noted as fragile.
* **Lockbox:** events of the newest reported quarter (period 2026-09-30) are held out of the main table and reported
  separately, only for holds already realised. They are too few and too recent to confirm anything.
* **Not run (stated):** older years via Alpaca/Benzinga headline + price-gap proxy: no surprise value exists for them, so
  it would be a different study (gap momentum), not PEAD. Not done.

## Verdict rule

"Suggestive": for the same side at two or more holds, mean excess > 0 (short: signed) after costs, coin-flip p < 0.05,
q < 0.10, same sign in both halves and in the lockbox. Anything else: "no visible drift". Either way: descriptive, not
tradable evidence, and it does not change any live system.
