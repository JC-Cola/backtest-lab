# Pre-registered confirmation test

Written and pushed **before** any confirmation data was downloaded or looked at.

## Why

After 6 hunt batches, 486 strategy × market tests sit in one Benjamini-Hochberg pool. With 2,000
coin-flip simulations, the smallest possible p-value is 1/2001 ≈ 0.0005, so a single test's best
possible q is 0.0005 × 486 ≈ 0.24. That is above the 0.10 PASS cut, so no single strategy can pass
any more, and every additional strategy makes it harder. Searching harder cannot produce confidence.

The standard remedy is to separate discovery from confirmation. Pick a few candidates using
discovery results only, fix every choice in writing, then test them once on data the search never
touched. Because the confirmation data played no part in choosing, the size of the search does
not dilute it. Only the number of candidates confirmed here does (Bonferroni).

## Candidates (selection rule fixed before choosing)

Rule: strategy families with coin-flip p < 0.05 on at least 3 of the 7 discovery markets **and** a
confirmed lockbox. Combos chosen after seeing results (batch 5) are excluded as derivative.

| Candidate | Discovery markets with p < 0.05 | Fixed parameters (most common in-sample choice on SPY/QQQ/IWM/DIA) |
|---|---|---|
| `williams_r`: Williams %R dip buy | SPY, QQQ, IWM, DIA | n = 2, no SMA200 filter: buy %R(2) < -90, sell %R(2) > -20 |
| `ibs`: Internal Bar Strength dip buy | SPY, QQQ, DIA | a = 0.2, b = 0.7: buy IBS < 0.2, sell IBS > 0.7 |
| `qp_january_barometer` | SPY, QQQ, BTC-USD | rule = first5: hold January; rest of year only if the first 5 sessions of January were up |

Parameters are fixed. There is no re-tuning on confirmation data.

## Confirmation data (never used by this project before)

1. **US, before the discovery window:** SPY 1993-02 to 2004-12, DIA 1998-01 to 2004-12, QQQ 1999-03 to 2004-12.
2. **Ten international stock indexes, full available history to today:** ^GDAXI (Germany), ^FTSE (UK),
   ^N225 (Japan), ^HSI (Hong Kong), ^AXJO (Australia), ^GSPTSE (Canada), ^FCHI (France),
   ^SSMI (Switzerland), ^AEX (Netherlands), ^KS11 (Korea).

Data-quality rule (mechanical, applied identically to every market): a bar is valid if
High ≥ max(Open, Close), Low ≤ min(Open, Close) and High > Low. A market's test starts on 1 January
of the first year from which every later full calendar year has ≥ 98% valid bars. Remaining invalid
bars are kept, since dropping them would change the calendar.

Same engine as discovery: decide at the close, trade at the next open, 3 bps per side, and a
2,000-sim coin-flip with its seed derived from the strategy and market name. An always-invested result
or one with no trades counts as p = 1.

## Decision rule for each candidate (13 markets)

A candidate is **confirmed** only if all three hold:

1. Fisher's combined p across the 13 markets < 0.05 / 3 = 0.0167 (Bonferroni for 3 candidates);
2. median net Sharpe ratio across the 13 markets > 0;
3. at least 7 of the 13 markets individually have coin-flip p < 0.10. This guards against Fisher's
   independence assumption, since stock markets are correlated.

Anything else is reported as **not confirmed**. Results are reported for all three candidates
whatever the outcome. If none is confirmed, that is the answer.
