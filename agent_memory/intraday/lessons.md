# What the intraday agent has learned so far

Updated 2026-11-23. 28098 trials in total: 25595 scored, 2503 rejected, 3 sent to the held-out test, 0 passed it.

## Most robust ideas on the search years (all 3 folds positive, median fold Sharpe, 2x costs; deflated reward is shown for information only)

- robustness 1.58 | folds [1.717, 1.176, 1.847] | 2x costs [1.576, 0.996, 1.695] | 150 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min
- robustness 1.58 | folds [1.717, 0.646, 2.542] | 2x costs [1.576, 0.485, 2.368] | 150 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min; stop 200 bp
- robustness 1.58 | folds [1.717, 0.92, 2.422] | 2x costs [1.576, 0.74, 2.241] | 150 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min; stop 100 bp
- robustness 1.58 | folds [1.717, 0.92, 2.329] | 2x costs [1.576, 0.74, 2.169] | 150 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min; stop 100 bp, target 4R
- robustness 1.58 | folds [1.717, 0.646, 2.542] | 2x costs [1.576, 0.485, 2.368] | 150 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min; stop 200 bp, target 4R
- robustness 1.58 | folds [1.717, 0.646, 2.542] | 2x costs [1.576, 0.485, 2.368] | 150 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min; stop 200 bp, target 3R
- robustness 1.58 | folds [1.717, 0.929, 2.907] | 2x costs [1.576, 0.751, 2.703] | 150 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min; stop 100 bp, target 1.5R
- robustness 1.58 | folds [1.717, 0.646, 2.568] | 2x costs [1.576, 0.485, 2.392] | 150 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min; stop 200 bp, target 1.5R
- robustness 1.58 | folds [1.717, 0.92, 2.829] | 2x costs [1.576, 0.74, 2.634] | 150 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min; stop 100 bp, target 2R
- robustness 1.58 | folds [1.717, 0.92, 2.449] | 2x costs [1.576, 0.74, 2.265] | 150 trades | deflated reward now 0.000 | SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min; stop 100 bp, target 3R

## Features that tend to help (learned weights, 1 = average)

- gap: 2.25
- orb30: 1.78
- gap_fill: 1.69
- ret_30: 1.65
- consol_30: 1.62
- rvol_30: 1.47
- rv_5d: 1.44
- range_pos: 1.40

## Features that tend to hurt

- prev_day_ibs: 0.89
- vwap_dist: 0.79
- run_10d: 0.63
- orb5: 0.56
- z_20d: 0.42

## Why ideas were rejected

- too few trades: 2309
- risk review: 172
- invalid spec: 19

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)

- fail (q=0.94) SPY at 11:00: long when gap > p85 and rv_5d > p25 and vol_ratio < p95; hold 240 min
- fail (q=1.00) SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and range_pos > p20; hold 240 min
- fail (q=0.94) SPY at 11:00: long when gap > p85 and prev_day_ibs < p99 and vol_ratio < p95; hold 240 min

## Last session

1 rounds x 10 ideas with the evolution proposer in 2s. Best deflated reward went from 0.000 to 0.000. Held-out shots this session: 0 (3 of 3 this month). Strategies passing the held-out test (BH across all 3 shots ever): 0.

## Held-out budget

3 of 3 held-out shots spent in 2026-11; 3 spent ever. Each shot goes to the most robust untested, non-duplicate idea; a pass needs BH significance across all shots ever and beating the live noise band at 2x costs. The deflated Sharpe is informational (27k+ trials keep it near 0).
