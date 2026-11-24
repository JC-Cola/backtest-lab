# What the intraday agent has learned so far

Updated 2026-11-24. 37253 trials in total: 34741 scored, 2512 rejected, 3 sent to the held-out test, 0 passed it.

## Most robust ideas on the search years (all 3 folds positive, median fold Sharpe, 2x costs; deflated reward is shown for information only)

- robustness 1.75 | folds [1.98, 1.938, 1.224] | 2x costs [1.814, 1.753, 1.063] | 157 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p80 and prev_day_ibs < p99 and ret_30 > p30; hold 240 min; stop 200 bp, target 2R
- robustness 1.75 | folds [1.98, 1.938, 1.162] | 2x costs [1.814, 1.753, 0.979] | 157 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p80 and prev_day_ibs < p99 and ret_30 > p30; hold 240 min; stop 200 bp
- robustness 1.75 | folds [1.98, 1.938, 1.172] | 2x costs [1.814, 1.753, 0.986] | 157 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p80 and prev_day_ibs < p99 and ret_30 > p30; hold 240 min; stop 200 bp, target 1.5R
- robustness 1.75 | folds [1.98, 1.938, 0.708] | 2x costs [1.814, 1.753, 0.554] | 157 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p80 and prev_day_ibs < p99 and ret_30 > p30; hold 240 min
- robustness 1.75 | folds [1.98, 1.938, 1.162] | 2x costs [1.814, 1.753, 0.979] | 157 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p80 and prev_day_ibs < p99 and ret_30 > p30; hold 240 min; stop 200 bp, target 3R
- robustness 1.75 | folds [1.98, 1.938, 1.162] | 2x costs [1.814, 1.753, 0.979] | 157 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p80 and prev_day_ibs < p99 and ret_30 > p30; hold 240 min; stop 200 bp, target 4R
- robustness 1.65 | folds [1.865, 0.925, 1.92] | 2x costs [1.647, 0.737, 1.678] | 242 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p80 and prev_day_ibs < p99 and rvol_30 < p99; hold 240 min; stop 200 bp, target 4R
- robustness 1.65 | folds [1.865, 0.925, 1.92] | 2x costs [1.647, 0.737, 1.678] | 242 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p80 and prev_day_ibs < p99 and rvol_30 < p99; hold 240 min; stop 200 bp
- robustness 1.65 | folds [1.865, 0.919, 1.933] | 2x costs [1.647, 0.729, 1.689] | 242 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p80 and prev_day_ibs < p99 and rvol_30 < p99; hold 240 min; stop 200 bp, target 1.5R
- robustness 1.65 | folds [1.865, 0.925, 1.922] | 2x costs [1.647, 0.737, 1.703] | 242 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p80 and prev_day_ibs < p99 and rvol_30 < p99; hold 240 min; stop 200 bp, target 2R

## Features that tend to help (learned weights, 1 = average)

- gap: 2.66
- ret_30: 1.67
- rvol_30: 1.56
- range_pos: 1.38
- prev_day_ibs: 1.35
- rv_5d: 1.32
- vol_ratio: 1.27
- prev_day_ret: 1.17

## Features that tend to hurt

- orb30: 0.80
- vwap_dist: 0.75
- range_5d: 0.75
- z_20d: 0.74
- consol_30: 0.74

## Why ideas were rejected

- too few trades: 2320
- risk review: 173
- invalid spec: 19

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)

- fail (q=0.94) SPY at 11:00: long when gap > p85 and rv_5d > p25 and vol_ratio < p95; hold 240 min
- fail (q=1.00) SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min
- fail (q=0.94) SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and vol_ratio < p95; hold 240 min

## Last session

1 rounds x 10 ideas with the evolution proposer in 3s. Best deflated reward went from 0.000 to 0.000. Held-out shots this session: 0 (3 of 3 this month). Strategies passing the held-out test (BH across all 3 shots ever): 0.

## Held-out budget

3 of 3 held-out shots spent in 2026-11; 3 spent ever. Each shot goes to the most robust untested, non-duplicate idea; a pass needs BH significance across all shots ever and beating the live noise band at 2x costs. The deflated Sharpe is informational (27k+ trials keep it near 0).
