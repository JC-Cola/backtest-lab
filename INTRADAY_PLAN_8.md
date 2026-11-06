# Intraday study 8: how often to check the noise-band breakout (pre-registered)

Written and committed **before** any of this study's results were computed.

## Question

Josh asked whether checking the live noise-band breakout more often than hourly (it now checks at 10:00, 11:00 ... 15:00
New York and is flat at the close) would mean less loss and more profit.

**Hypothesis (null):** checking every 5, 15 or 30 minutes does not beat hourly checks after costs.

## Rule

The noise-band breakout exactly as in study 1 (`noise_breakout` in `src/intraday.py`, unchanged; no new strategy code):
bands = max(open, previous close) x (1 + sigma) and min(open, previous close) x (1 - sigma), sigma(m) = the average
absolute move from the open at minute m over the last `lookback` sessions. At each check, long above max(upper band,
VWAP), short below min(lower band, VWAP), otherwise flat; the position is held until the next check; flat at the
close. Fills at the next minute's open. The first check is 10:00 and the rest follow every `check` minutes, so
check = 60 is the live schedule (10:00 to 15:00, then held to the close). A shorter interval also adds later checks
(30 min: last at 15:30; 15 min: 15:45; 5 min: 15:55). That is part of "checking more often" and is not adjusted.

The code already accepts any check interval that divides the schedule, so it is unchanged; a test pins that
check = 60 and check = 30 output is identical to the study-1 values.

## Grid (8) and tests

`check` in {5, 15, 30, 60} minutes x `lookback` in {10, 14} sessions. Markets SPY and QQQ (the discovery markets).
Data, windows, costs and tests are those of studies 1, 3-7: minute bars; tune on 2016-2020 (best Sharpe, at least 50
trades), test once on 2021-today; 1 bp per side, re-run at 2x; coin-flip test with 2,000 random re-timings (same days,
trade count, holds and long/short mix); Benjamini-Hochberg across this study's 2 tests (strategy `id_noise_freq` x
SPY, QQQ). The deflated Sharpe of the tuned setting's test-year returns, against the 8 grid settings' tuning-year
Sharpes as the trials, is reported for context and is not a gate (the existing machinery does not gate on it).

- **Candidate:** p < 0.05, q < 0.10, test Sharpe > 0 at 1x and 2x costs, **and** the tuned setting checks more often
  than hourly (check < 60) with a 2x test Sharpe higher than the hourly setting at the same lookback on the same market.
  If tuning picks check = 60, there is no faster variant to promote and the null stands.
- **Confirmed:** fresh tuning on IWM and DIA (study 1's lockbox) gives Fisher's combined p < 0.05 and a median test
  Sharpe > 0. The lockbox is run only if a candidate exists.

## Secondary readout (fixed now, reported whatever the candidate outcome)

For each check in {5, 15, 30, 60} at lookback 14 (the live setting), on SPY and QQQ, in the test years (2021-today),
at 1x and at 2x costs: Sharpe, total return, trades, win rate, average hold (minutes), average trade (bp), worst day,
maximum drawdown (of the daily-return equity curve), with buy-and-hold alongside. Also the mean and t-statistic of
the daily-return difference (variant minus hourly), descriptive only, no test or threshold.

How the question is answered: "less loss" = a smaller worst day and a smaller maximum drawdown than hourly;
"more profit" = a higher total return and Sharpe than hourly, both at 1x and at 2x costs, on both markets. A variant
that does only part of this is reported as such. With no candidate, the answer to Josh is "not shown".
