# Study 14: short at the open after bad news, hold to the CLOSE of the same day (pre-registered)

Written and committed **before** any full-day minute bar was downloaded and before any result of this study was computed.

## Question

Josh: shorting NVDA at the open on 2026-11-05 (open 196.07) and holding to the close (181.60) made about 7.4%. Study 12 held
daily from the NEXT open; study 13 exited 1 to 30 minutes after the open. Neither tested "short at the open, hold to the
close of the same day". **Null:** after overnight bad news (or after a bad-news shock day), shorting the open and holding to
the close (or to 15:55) earns no more, after costs, than the same short on random days. The **mirror long** (buy the open,
sell at the close) is tested alongside.

## Data

- Study 13's cache (`data/news13/`): the first 45 minutes of 1-minute bars (SIP, split-adjusted) and Alpaca daily bars;
  news in `data/news/`. The gap, volatility, prev close and news windows are exactly study 13's (`src/news13.py`).
- **New:** full regular-session 1-minute bars (bar starts 09:30 to 15:59 NY), SIP, `adjustment=SPLIT`, 2016-01-04 to the
  last completed session, for the 10 discovery stocks (NVDA AAPL MSFT AMZN GOOGL META TSLA AMD NFLX AVGO), cached as
  open/high/low/close arrays in `data/news14/` (gitignored). Needed for the 15:55 exit and the intraday stop. Lockbox
  (CRM ORCL ADBE INTC QCOM) is **downloaded only if a candidate exists**, and not looked at before.
- The official close of day t is the Alpaca daily bar close of day t (consolidated). Bar j starts at 09:30+j minutes; "15:55
  bar" = the bar starting 15:55 (j = 385).

## Events (same as study 13, no look-ahead)

- **E1 "overnight bad news + gap down", g in {0.5, 1, 2}:** `gap_t = Open_t/Close_{t-1} - 1` (Open_t = open of the 09:30 bar)
  `<= -g x vol_t` (vol = std ddof 1 of the 60 daily returns ending t-1, at least 60 needed) AND at least 1 article with
  16:00 ET of the previous trading day < created_at < 09:30:00 ET of day t.
- **E1b "any overnight news with a negative gap":** gap_t < 0 (strict) AND the same news condition, same 60-day eligibility
  (no size condition).
- **E2 "day after a bad-news shock", k in {2, 3}:** study 12's negative shock on day t-1 (scope "all"), trade on day t. No gap
  condition.
- Controls (descriptive): gap-down days with NO news (same g), traded by the same rules; random gap-down days (with or
  without news) as a placebo pool.

## Trades

Direction short (the idea) or long (mirror). Entry variants: **09:30 print** (open of the 09:30 bar; the gap uses this
print so it is an upper bound) and **09:31** (open of the 09:31 bar; the realistic one). Exit rules (4):

- **X55**: close of the bar starting 15:55 (a market order before the close; the tradable main exit).
- **XC**: the official daily close (upper bound).
- **S1, S2**: stop at s = 1%, 2% adverse to the entry price (short: price rises to entry x (1+s); long: falls to entry x
  (1-s)), else exit at X55. A stop is checked on every bar from the entry bar through the 15:55 bar using the bar high
  (short) or low (long), so a bar that touches the level stops it (conservative: the adverse extreme is assumed to come
  first). Fill = the stop level, or the bar's open if the bar opens beyond it (gap through). If no stop triggers and the
  15:55 bar is missing the event is skipped.

A missing entry bar, or a missing exit price when needed, skips the event (counted). Net return: long = Exit/Entry - 1 - 2c,
short = -(Exit/Entry - 1) - 2c; **c = 0.05% per side main**, 0.10% and 0.20% stress. Borrow cost is negligible for one day
on mega caps and is ignored.

## Periods, grid, tests

- Tune 2016-01-01 to 2021-12-31; test (once, no re-tuning) 2022-01-01 to the end of the data.
- Strategies = (E1, E1b, E2) x (short, long) = 6. Parameter sets: E1 3 g x 4 exits = 12, E1b 4, E2 2 k x 4 exits = 8, each at
  both entry variants, each direction: (12 + 4 + 8) x 2 x 2 = **96 tests**, all coin-flipped; Benjamini-Hochberg q over all 96.
- Tuning: per strategy the (parameter, exit) with the best tuning-year Sharpe at 0.05% (at least 30 tuning trades, else best
  overall), 09:30 entry.
- Statistics exactly as study 13: trades, mean/median bp at 0.05/0.10/0.20%, hit rate, daily-series Sharpe (equal 1/10
  sleeves) at 0.05% and 0.10%, **coin-flip with 2,000 random days** (per stock the same number of trades on random test
  sessions with bars, same direction, entry and exit) = primary p of the BH family; day-level one-sided t-test; deflated
  Sharpe of the tuned set against the tuning Sharpes of its strategy's sets (context).
- **News vs gaps (E1/E1b, descriptive, not in the BH family):** the same placebo but random days from (a) gap-down days with
  no news of the same g, (b) all gap-down days of the same g; plus the no-news gap days' own mean.
- **Stops effect (descriptive):** per event rule the paired mean difference S1 - X55 and S2 - X55 (same events) and the share
  of trades stopped; mean max adverse excursion.
- **Candidate:** the tuned set (09:30 entry) with coin-flip p < 0.05, q < 0.10, day-level t-test p < 0.05, test Sharpe > 0 at
  0.05% and at 0.10%, and positive mean net return at 0.05%. **Actionable:** a candidate whose exit is not XC (so a
  market-order exit before the close) and whose 09:31 entry variant has a positive mean at 0.05% with coin-flip p < 0.05.
- **Lockbox (only if a candidate exists):** frozen rule run unchanged on CRM ORCL ADBE INTC QCOM from 2016; per-stock coin-flip
  p, Fisher; **confirmed** if Fisher p < 0.05, pooled Sharpe > 0 and pooled mean > 0 at 0.05%.

## Descriptive readouts (no gates)

Events per year (E1 by g, E1b, no-news gap-down days, E2 by k); test-year table per rule (trades, mean bp, hit, p, q at 0.05%,
mean at 0.10/0.20%); average open-to-time path (bp, long direction) at 30m, 1h, 2h, 3h, 4h, 15:55 and the close for E1 events vs
no-news gap days vs all days; NVDA alone: each rule tune/test, its test-year events, and 2026-11-05 (checked and reported either way).

## Plain answer rule (stated now)

"Short the open and hold to the close after bad news" works only if a candidate exists, is actionable (09:31 entry, not XC),
is confirmed on the lockbox, and stays positive at 0.10% per side. A tuning-years-only pass, a result that needs the 09:30
print or the official close, or one that does not beat the gap-down no-news days, is "not shown"; if the mirror long is what
shows an edge that is reported as such. Everything is reported whatever the outcome. **No orders are placed by this study.**

## Deliverables

`src/news14.py` (+ `src/news14_data.py` for the full-day download), `tests/test_news14.py` (timing, stops, no look-ahead,
short P&L), `results/news14.json` in the style of `results/news13.json`.
