# What the intraday agent has learned so far

Updated 2026-11-28. 123997 trials in total: 121323 scored, 2674 rejected, 3 sent to the held-out test, 0 passed it.

## Most robust ideas on the search years (all 3 folds positive, median fold Sharpe, 2x costs; deflated reward is shown for information only)

- robustness 2.05 | folds [2.307, 0.316, 2.252] | 2x costs [2.19, 0.121, 2.053] | 162 trades | deflated reward now 0.000 | SPY at 11:00: long when consol_30 > p50 and gap > p80 and rvol_30 < p99; hold 120 min; stop 200 bp, target 1.5R
- robustness 2.05 | folds [2.307, 0.316, 2.265] | 2x costs [2.19, 0.121, 2.047] | 162 trades | deflated reward now 0.000 | SPY at 11:00: long when consol_30 > p50 and gap > p80 and rvol_30 < p99; hold 120 min; stop 200 bp
- robustness 2.05 | folds [2.307, 0.316, 2.265] | 2x costs [2.19, 0.121, 2.047] | 162 trades | deflated reward now 0.000 | SPY at 11:00: long when consol_30 > p50 and gap > p80 and rvol_30 < p99; hold 120 min; stop 200 bp, target 3R
- robustness 2.05 | folds [2.307, 0.316, 2.265] | 2x costs [2.19, 0.121, 2.047] | 162 trades | deflated reward now 0.000 | SPY at 11:00: long when consol_30 > p50 and gap > p80 and rvol_30 < p99; hold 120 min; stop 200 bp, target 4R
- robustness 2.01 | folds [2.307, 0.316, 2.195] | 2x costs [2.19, 0.121, 2.01] | 162 trades | deflated reward now 0.000 | SPY at 11:00: long when consol_30 > p50 and gap > p80 and rvol_30 < p99; hold 120 min; stop 200 bp, target 2R
- robustness 1.86 | folds [2.307, 0.316, 2.072] | 2x costs [2.19, 0.121, 1.862] | 162 trades | deflated reward now 0.000 | SPY at 11:00: long when consol_30 > p50 and gap > p80 and rvol_30 < p99; hold 120 min
- robustness 1.85 | folds [2.175, 0.476, 2.078] | 2x costs [2.047, 0.285, 1.853] | 154 trades | deflated reward now 0.000 | SPY at 11:00: long when consol_30 > p50 and gap > p80 and rvol_30 < p94; hold 120 min; stop 200 bp, target 4R
- robustness 1.82 | folds [2.054, 0.528, 1.995] | 2x costs [1.964, 0.385, 1.823] | 162 trades | deflated reward now 0.000 | SPY at 11:00: long when consol_30 > p50 and gap > p80 and rvol_30 < p99; hold 240 min; stop 200 bp, target 1.5R
- robustness 1.82 | folds [2.054, 0.539, 1.974] | 2x costs [1.964, 0.396, 1.82] | 162 trades | deflated reward now 0.000 | SPY at 11:00: long when consol_30 > p50 and gap > p80 and rvol_30 < p99; hold 240 min; stop 200 bp, target 2R
- robustness 1.81 | folds [2.054, 0.539, 1.98] | 2x costs [1.964, 0.396, 1.81] | 162 trades | deflated reward now 0.000 | SPY at 11:00: long when consol_30 > p50 and gap > p80 and rvol_30 < p99; hold 240 min; stop 200 bp

## Features that tend to help (learned weights, 1 = average)

- gap: 2.13
- ret_30: 1.49
- rvol_30: 1.39
- prev_day_ibs: 1.38
- consol_30: 1.33
- range_pos: 1.31
- vol_ratio: 1.12
- rv_5d: 1.11

## Features that tend to hurt

- rsi_14d: 0.82
- range_5d: 0.81
- z_20d: 0.80
- run_10d: 0.79
- hi20_dist: 0.73

## Why ideas were rejected

- too few trades: 2426
- risk review: 229
- invalid spec: 19

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)

- fail (q=0.94) SPY at 11:00: long when gap > p85 and rv_5d > p25 and vol_ratio < p95; hold 240 min
- fail (q=1.00) SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min
- fail (q=0.94) SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and vol_ratio < p95; hold 240 min

## Last session

1 rounds x 10 ideas with the evolution proposer in 8s. Best deflated reward went from 0.000 to 0.000. Held-out shots this session: 0 (3 of 3 this month). Strategies passing the held-out test (BH across all 3 shots ever): 0.

## Held-out budget

3 of 3 held-out shots spent in 2026-11; 3 spent ever. Each shot goes to the most robust untested, non-duplicate idea; a pass needs BH significance across all shots ever and beating the live noise band at 2x costs. The deflated Sharpe is informational (27k+ trials keep it near 0).
