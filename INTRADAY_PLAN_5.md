# Intraday study 5: the Darvas box on minute bars (pre-registered)

Written and committed **before** any of this study's results were computed. The daily Darvas box was tested in the
first hunt (no market beat random timing); this tests the same rule inside the trading day, which has not been done.

## Rule (Nicolas Darvas, *How I Made $2,000,000 in the Stock Market*, 1960)

Within each session, on N-minute bars (the day's minute bars grouped from 09:30):
- a **box top** is a high not exceeded for the next `hold` bars; the **box bottom** is the lowest low after the top
  that is not broken for the next `hold` bars; the box is confirmed at that bar (the code is `find_boxes` from
  `src/strategies_trend.py`, the daily version's, run on each day's bars separately; boxes never span days);
- **enter long** when a bar closes above the most recent box top confirmed before that bar;
- **exit** when a bar closes below the most recent confirmed box bottom (it trails up as new boxes confirm);
- re-entry allowed; long-only, as Darvas traded; flat at the close.

Signals on a bar's close fill at the next minute's open, as in every intraday study.

**Grid (4):** N = 5 or 15 minutes; hold = 3 or 5 bars.

## Data, windows, costs, tests

The same as studies 1, 3 and 4: SPY and QQQ minute bars, tune on 2016–2020, test once on 2021–today, 1 bp per side
(re-run at 2×), coin-flip test with 2,000 random re-timings, Benjamini–Hochberg across this study's 2 tests.

- **Candidate:** p < 0.05, q < 0.10, Sharpe > 0 at 1× and 2× costs.
- **Confirmed:** fresh tuning on XLK, SMH, XLF and XLE (never used: reserved by study 3, never downloaded, since study 3
  had no candidate) gives Fisher's combined p < 0.05 and a median test Sharpe > 0.

Every result is reported and added to the Strategy Lab page if it made money in its test years.
