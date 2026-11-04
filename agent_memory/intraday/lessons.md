# What the intraday agent has learned so far

Updated 2026-11-04. 300 trials in total: 244 scored, 56 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.004 (when found 0.003) | folds [0.246, 0.524, 1.19] | SPY at 15:30: short when prev_day_ibs > p60 and ret_30 < p20 and vwap_dist > p10; hold 15 min; stop 100 bp
- reward now 0.003 (when found 0.018) | folds [0.629, 0.379, 0.893] | QQQ at 14:30: long when rv_5d > p90; hold 30 min
- reward now 0.003 (when found 0.003) | folds [-0.299, 1.571, 0.563] | QQQ at 10:00: long when noise_z > p75 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp
- reward now 0.002 (when found 0.004) | folds [-0.419, 1.404, 0.732] | QQQ at 14:30: long when gap > p90; hold 30 min
- reward now 0.002 (when found 0.003) | folds [-0.577, 1.571, 0.563] | QQQ at 10:00: long when noise_z > p70 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp
- reward now 0.002 (when found 0.002) | folds [-0.577, 1.571, 0.563] | QQQ at 10:00: long when noise_z > p60 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp
- reward now 0.002 (when found 0.002) | folds [0.842, 0.857, 0.327] | QQQ at 11:00: long when rv_5d > p90; hold 30 min
- reward now 0.001 (when found 0.002) | folds [-0.299, 1.319, 0.296] | QQQ at 10:00: long when ret_30 > p90 and vol_ratio < p80; hold 60 min; stop 100 bp
- reward now 0.001 (when found 0.008) | folds [0.213, 0.424, 0.63] | SPY at 15:30: short when prev_day_ibs > p50 and ret_30 < p20 and vwap_dist > p10; hold 15 min; stop 100 bp
- reward now 0.001 (when found 0.001) | folds [0.213, 0.424, 0.63] | SPY at 15:30: short when prev_day_ibs > p50 and ret_30 < p20 and vwap_dist > p10; hold 15 min

## Features that tend to help (learned weights, 1 = average)

- gap: 1.02
- prev_day_ibs: 1.01
- vwap_dist: 1.01
- ret_30: 1.00
- rv_5d: 1.00
- vol_ratio: 0.99
- ret_prev_close: 0.99
- noise_z: 0.99

## Features that tend to hurt

- ret_prev_close: 0.99
- noise_z: 0.99
- prev_day_ret: 0.99
- range_pos: 0.99
- ret_open: 0.99

## Why ideas were rejected

- too few trades: 56

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)


## Last session

30 rounds x 10 ideas with the evolution proposer in 2s. Best deflated reward went from 0.000 to 0.004. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
