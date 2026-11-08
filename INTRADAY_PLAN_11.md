# Intraday study 11: volatility-targeted sizing and a VIX regime filter for the live noise-band breakout (pre-registered)

Written and committed **before** any of this study's results were computed.

## Question

Can the odds of the live noise-band breakout be improved by two ideas from the original paper and from practitioners,
without re-tuning its signal?

- **A. Volatility-targeted sizing** (Zarattini, Aziz & Barbon 2024, *Beat the market*): scale each day's position by
  min(cap, target daily vol / realized daily vol).
- **B. Regime filter:** trade only on days when the previous day's VIX close is above (or below) its 20-day average.

**Null:** neither idea beats the plain live rule on risk-adjusted terms after costs.

## The plain rule (the benchmark) and the fixed signal

The live rule exactly as in `src/live_noise.py` and study 1: `noise_breakout` in `src/intraday.py`, **lookback 14, check
60** (checks 10:00 to 15:00 New York; long above max(upper band, VWAP), short below min(lower band, VWAP); flat at the
close; fills at the next minute's open), size 1.0 (i.e. the current 50% of equity per symbol), no filter. The signal is
not re-tuned and no signal code changes: every variant uses the **same trades** as the plain rule and only changes
which days are traded and how big each day's position is.

## Variants

- **Sizing (day d):** `size_d = min(cap, target / vol_d)`. `vol_d` = the sample standard deviation (ddof = 1) of the 14
  daily close-to-close returns of sessions d-14 ... d-1 (only days before d). Grid: target in {1%, 2%} daily, cap in
  {1.0, 2.0}. Cap 1.0 is the no-leverage version (size never above today's); cap 2.0 allows up to 2x today's size
  (Alpaca intraday buying power would permit it). The day's trades, and the costs on them, scale by `size_d`. Or
  "off" (size 1.0).
- **Regime filter (day d):** `v` = the VIX close of the last trading day before d (strictly earlier date), `m` = the mean
  of the 20 VIX closes ending that day. "high" trades only if v > m; "low" trades only if v <= m; "none" trades every
  day. A day with no VIX history is not traded under "high"/"low". VIX comes from the repo's cached `data/ctx_VIX.csv`
  (the same series the daily engine's `^VIX` context uses).
- **Grid (14):** sizing {off, (1%,1.0), (1%,2.0), (2%,1.0), (2%,2.0)} x filter {none, high, low}, minus the plain rule
  itself (off, none), which is the benchmark and not a trial. Marking: id `id_noise_volsize_vix`.

## Data, windows, costs, tests

SPY and QQQ minute bars (discovery); lockbox IWM and DIA. Tune on 2016-2020: the variant with the best Sharpe
(needing at least 50 trades), per market. Test once on 2021-today, no re-tuning. Costs 1 bp per side, re-run at 2x
(2 bp per side), both scaled by the day's size.

1. **Coin-flip test** (all 14 variants x 2 markets = 28 tests): 2,000 random re-timings with the same days, the same
   number of trades, the same holding times, the same long/short mix and the same day sizes (same exposure); only the
   entry minute moves. p = share at least as good (size-weighted sum of trade returns net of cost). The test holds the
   chosen days fixed, so it asks whether the intraday timing beats random on those days; whether the day filter or the
   sizing adds value is the job of the primary comparison below.
2. **Benjamini-Hochberg q-values across all 28 tests.**
3. **Deflated Sharpe** of the tuned variant's test-year returns against the 14 variants' tuning-year Sharpes as trials
   (reported for context, not a gate).
4. **Paired circular block bootstrap** (block length 10 days, 10,000 resamples, fixed seed) of the daily-return
   difference variant minus plain over the test years: one-sided p that the mean difference is not above zero (the
   differences are centred under the null). Reported at 1x and at 2x costs, for every variant; a Sharpe-difference
   bootstrap p is reported alongside, descriptively. Reported, not a gate, but it is used in the plain answer: a
   variant whose paired p at 2x costs is above 0.10 is "not distinguishable from the plain rule".

## Primary comparison and candidate rule

For the tuned variant on each market, in the test years at **2x costs**: its Sharpe must be **higher than the plain
live rule's** on the same market **and** its maximum drawdown (of the daily-return equity curve) **no deeper than**
the plain rule's.

**Candidate** (as in studies 1, 3-8): coin-flip p < 0.05, q < 0.10 (family of 28), test Sharpe > 0 at 1x and at 2x
costs, **and** the primary comparison above.

**Confirmed:** fresh tuning on IWM and DIA gives Fisher's combined coin-flip p < 0.05 and a median test Sharpe > 0
(the lockbox is run only if a candidate exists; the lockbox variants' comparison with the plain rule on those markets
is reported alongside).

## Readout (reported whatever the outcome)

For each of the 14 variants and the plain rule, on SPY and QQQ, test years, at 1x and 2x costs: Sharpe, total return,
maximum drawdown, worst day, trades, average size, share of days traded; buy-and-hold alongside; the paired bootstrap
p's. Plain answer: the live rule changes only if a candidate exists, is confirmed on the lockbox, and its paired p at
2x costs is below 0.10; anything less is "not shown", and the live rule stays. Josh decides before any live change.
