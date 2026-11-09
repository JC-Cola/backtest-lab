# Study 13: short at the open after bad news, cover within minutes (pre-registered)

Written and committed **before** any minute bar was downloaded and before any result of this study was computed.

## Question

Josh: "i would have shorted on open and then closed in a couple minutes" (after bad news, e.g. NVDA). Study 12 only
tested daily holds (next open to a later open). This study tests his intraday version on 1-minute bars.
**Null:** after bad news, shorting at the open and covering m minutes later earns no more (after costs) than the same
short on random days. The **mirror** (buy the open, sell m minutes later) is tested alongside, so we see which way the
first minutes actually go.

## Data

- **News:** the cached Alpaca/Benzinga articles of study 12 (`data/news/<SYM>.jsonl`, via `src/news_data.py`). An
  article's time is its `created_at`.
- **Minute bars:** Alpaca historical 1-minute bars, **SIP feed** (consolidated), `adjustment=SPLIT` (split-adjusted, not
  dividend-adjusted, so opens and the previous close are comparable raw prices). If SIP is refused for this key the
  download falls back to **IEX** and the result says so (IEX bars are thin: missing bars are then likely and counted).
  Only bars starting 09:30 to 10:14 New York time (the first 45 minutes of the session) are kept, for **every**
  session from 2016-01-04 to the last fully completed session before the download date, cached to `data/news13/`
  (gitignored). Download time is measured and reported. A bar's timestamp is its START; "the close of the bar that starts
  at 09:30+m-1" is the price at 09:30+m.
- **Daily bars (for the gap and volatility):** Alpaca daily bars, same feed and `adjustment=SPLIT`, from 2015-09-01 (for
  the 60-day warm-up). Close_{t-1} is the official consolidated close, so the gap Open_t / Close_{t-1} is made from the
  same source and adjustment. (E2 uses study 12's own event detection, i.e. the Yahoo daily series of `src/data.py`.)
- **Universe (tune + test):** NVDA AAPL MSFT AMZN GOOGL META TSLA AMD NFLX AVGO. **Lockbox, not downloaded and not
  looked at unless a candidate exists:** CRM ORCL ADBE INTC QCOM.

## Events (decided with information available at the stated moment, no look-ahead)

- **E1 "overnight bad news, gap down":** for stock S on trading day t:
  - `vol_t` = sample std (ddof 1) of the 60 daily close-to-close returns ending on day **t-1** (day t is not in it);
    fewer than 60 prior returns = not eligible.
  - `gap_t` = Open_t / Close_{t-1} - 1, where Open_t is the **open of the first 1-minute bar (09:30)**.
  - **Gap-down:** `gap_t <= -g x vol_t`, g in {1, 2}.
  - **News:** at least 1 article for S with 16:00 ET of the previous trading day (the previous row of the daily series,
    so a Monday includes the weekend) **< created_at < 09:30:00 ET of day t** (strictly before the open). ET handled with
    time zones, so daylight-saving changes are right.
  - Event = gap-down AND news. A trade is taken once per stock-day.
- **E2 "day after a bad-news shock":** study 12's negative news shock on day t (|ret_t| >= k x vol_t with the study 12
  vol, k in {2, 3}, ret_t < 0, at least 1 article in the study 12 window, scope "all"). The trade is on the **next
  trading day t+1**. No gap condition.
- **Control, descriptive:** gap-down days with **no** news (same g), traded by the same rules, and for E1 the placebo
  "random gap-down days" below.

## Trades

Direction **short** (the idea) or **long** (mirror). Entry variants:
- **09:30 print (main, an upper bound):** enter at the **open of the 09:30 bar**, the opening print. The gap that defines
  E1 uses this very print, so this variant is not fully executable (you cannot condition on the open and trade the
  same print); it is the ideal bound.
- **09:31 variant (realistic for a person or bot that sees the open and reacts):** enter at the **open of the 09:31
  bar**. The E1 condition still uses the 09:30 open (known by then). Reported for every rule.

Exit rules (hold m minutes after the entry price time T): m in {1, 2, 5, 15, 30} exits at the **close of the bar that
starts at T+m-1** (the price at T+m); plus **G**: exit at the close of the first bar (starting at or after the entry
bar) that is green for a short (close > open; red for the mirror long), or at T+15 minutes if none is by then,
whichever comes first. So 6 exit rules. If the entry bar or an exit bar is missing (no trade that minute), the event is
**skipped** and counted.

Net return per trade: long = Exit/Entry - 1 - 2c; short = -(Exit/Entry - 1) - 2c. **Borrow cost is ignored (minutes;
mega caps assumed shortable and easy to borrow).** c = **0.05% per side main**, **0.10%** and **0.20%** stress, i.e. a
round trip costs 10 / 20 / 40 bp. Why: the opening auction and the first minute spread on mega caps is several bp
and the quote is unsettled after news; a market order at 09:30 can slip more; the stress levels cover that.

## Periods

- **Tune:** events with entry day 2016-01-01 to 2021-12-31. **Test (once, no re-tuning):** entry day 2022-01-01 to the
  end of the data.

## Grid and tests

Strategy = (event, direction) = E1-short, E1-long, E2-short, E2-long (4). Parameters = (g or k) x exit rule = 2 x 6 = 12
sets per strategy at the 09:30 entry; the 09:31 entry variant of each is also tested. **4 x 12 x 2 = 96 tests**, all
coin-flipped; Benjamini-Hochberg q over all 96. Tuning picks per strategy the parameter set with the best tuning-year
Sharpe at 0.05% (at least 30 tuning trades, else best overall), 09:30 entry; the rest are reported.

## Statistics

For every test in the test years: events, mean net return per trade in bp at 0.05%, 0.10%, 0.20%, median, hit rate, the
Sharpe of the daily series (equal sleeves, each stock 1/10 of capital, a day's return = the average of that day's trade
returns of the stocks weighted 1/10, cost included, 0 on days without a trade; Sharpe = mean/std ddof 1 x sqrt(252)) at 0.05%
and 0.10% per side.
- **Coin-flip test (2,000 sims):** per stock the **same number of trades** at **random days** drawn without replacement
  from that stock's test-window sessions that have bars, same direction, same entry variant, same exit rule; value of a
  draw = sum of net returns pooled over stocks. p = (1 + #sims >= strategy) / (1 + 2000). Primary p of the BH family.
- **News vs gaps (E1, descriptive, not in the BH family):** the same, but the random days are drawn from that stock's
  **gap-down days of the same g with or without news** (no-news gap days alone are also reported). If the news events do
  not beat random gap-down days, the gap, not the news, is the signal.
- **Cluster check:** events on the same day across stocks are correlated, so the placebo p can be optimistic. Also
  reported (and gating for candidates): a one-sided t-test, mean > 0, on **day-level** mean net returns (events on the
  same date averaged first).
- **Deflated Sharpe** of the tuned set's test daily returns against the 12 tuning-year Sharpes (context, not a gate).
- **Candidate:** the tuned set (09:30 entry) with coin-flip p < 0.05, q < 0.10, day-level t-test p < 0.05, test Sharpe > 0
  at 0.05% **and** at 0.10% per side, and positive mean net return at 0.05%. **Actionable:** a candidate whose 09:31
  entry variant also has a positive mean net return at 0.05% and coin-flip p < 0.05.
- **Lockbox (downloaded only if a candidate exists):** frozen (event, direction, g or k, exit) run unchanged on CRM
  ORCL ADBE INTC QCOM, all years from 2016. Per-stock coin-flip p (no trades = p 1), Fisher's method. **Confirmed** if
  Fisher p < 0.05, pooled Sharpe > 0 and pooled mean net return > 0 (at 0.05%).

## Descriptive readouts (no gates)

- Events per year (E1 by g with news, no-news gap-down days; E2 by k), per stock, and the share of gap-down days with news.
- Test-year table: event x direction x entry variant x exit rule: trades, mean bp, hit rate, p, q at 0.05% and mean at 0.10% / 0.20%.
- Average cumulative path (in bp, long direction, from the 09:30 open) at minutes 1 to 30 for E1 test-year events, for
  random days and for no-news gap-down days: which way do the first minutes go?
- **NVDA alone:** each rule's NVDA events, tune and test, mean net bp and hit rate; the list of NVDA E1/E2 events in
  the test years with gap and the first-30-minute path; and last week's ~-6% day if it qualifies (checked, reported
  either way).

## Plain answer rule (stated now)

Josh's idea "works" only if a candidate exists, its **09:31 entry variant keeps a positive mean at 0.05% (actionable)**,
it is **confirmed** on the lockbox, and the mean stays positive at 0.10% per side. A pass in the tuning years that dies
in the test years, or a short that only works at the unattainable 09:30 print, is "not shown". If the mirror long is what
shows an edge, that is reported as such. Every result is reported whatever the outcome. Josh decides before any
trading change; **no orders are placed by this study.**

## Deliverables

`src/news13.py` (download + cache + study), `results/news13.json` (same style as `results/news12.json`), tests
`tests/test_news13.py` for: news windows in ET incl. daylight-saving dates, gap/vol using only data up to the stated
moment, entry at the first bar's open (and 09:31 variant), exit timing, short P&L. Lab template, `build_lab`, `lab_text`
are integrated separately.
