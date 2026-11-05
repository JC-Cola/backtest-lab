# What the intraday agent has learned so far

Updated 2026-11-05. 1108 trials in total: 845 scored, 263 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.001 (when found 0.003) | folds [0.634, 1.23, 2.401] | QQQ at 10:00: long when noise_z > p85 and ret_30 > p90 and rv_5d < p95; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.002) | folds [0.844, 2.709, 1.22] | QQQ at 10:00: long when noise_z > p85 and ret_30 > p90 and rv_5d < p75; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.074) | folds [0.844, 2.194, 1.366] | QQQ at 10:00: long when noise_z > p70 and ret_30 > p90 and rv_5d < p75; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.003) | folds [0.844, 2.194, 1.366] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p75; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.001) | folds [0.844, 2.194, 1.366] | QQQ at 10:00: long when noise_z > p60 and ret_30 > p90 and rv_5d < p75; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [0.844, 2.194, 1.366] | QQQ at 10:00: long when noise_z > p70 and ret_open > p90 and rv_5d < p75; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [0.844, 2.194, 1.366] | QQQ at 10:00: long when ret_open > p90 and rv_5d < p75; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [0.844, 2.194, 1.366] | QQQ at 10:00: long when noise_z > p10 and ret_open > p90 and rv_5d < p75; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [0.714, 0.813, 2.248] | QQQ at 10:00: long when gap < p80 and ret_30 > p90; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.002) | folds [0.845, 0.467, 1.027] | SPY at 12:30: short when noise_z < p30 and ret_open < p10; hold 120 min

## Features that tend to help (learned weights, 1 = average)

- gap: 1.01
- prev_day_ibs: 1.01
- vwap_dist: 1.00
- ret_30: 1.00
- noise_z: 1.00
- rv_5d: 1.00
- ret_prev_close: 1.00
- vol_ratio: 1.00

## Features that tend to hurt

- ret_prev_close: 1.00
- vol_ratio: 1.00
- prev_day_ret: 1.00
- ret_open: 0.99
- range_pos: 0.99

## Why ideas were rejected

- too few trades: 263

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 1s. Best deflated reward went from 0.001 to 0.001. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
