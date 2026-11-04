# What the intraday agent has learned so far

Updated 2026-11-04. 358 trials in total: 286 scored, 72 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.013 (when found 0.013) | folds [0.003, 1.251, 1.19] | QQQ at 14:30: long when gap > p90 and vwap_dist < p80; hold 30 min
- reward now 0.003 (when found 0.003) | folds [0.246, 0.524, 1.19] | SPY at 15:30: short when prev_day_ibs > p60 and ret_30 < p20 and vwap_dist > p10; hold 15 min; stop 100 bp
- reward now 0.002 (when found 0.018) | folds [0.629, 0.379, 0.893] | QQQ at 14:30: long when rv_5d > p90; hold 30 min
- reward now 0.002 (when found 0.003) | folds [-0.299, 1.571, 0.563] | QQQ at 10:00: long when noise_z > p75 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp
- reward now 0.002 (when found 0.004) | folds [-0.419, 1.404, 0.732] | QQQ at 14:30: long when gap > p90; hold 30 min
- reward now 0.001 (when found 0.003) | folds [-0.577, 1.571, 0.563] | QQQ at 10:00: long when noise_z > p70 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp
- reward now 0.001 (when found 0.002) | folds [-0.577, 1.571, 0.563] | QQQ at 10:00: long when noise_z > p60 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp
- reward now 0.001 (when found 0.002) | folds [-0.577, 1.571, 0.563] | QQQ at 10:00: long when noise_z > p65 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp
- reward now 0.001 (when found 0.001) | folds [0.097, 0.924, 0.69] | SPY at 15:30: short when prev_day_ibs > p60 and ret_30 < p15 and vwap_dist > p10; hold 15 min; stop 100 bp
- reward now 0.001 (when found 0.002) | folds [0.842, 0.857, 0.327] | QQQ at 11:00: long when rv_5d > p90; hold 30 min

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

- too few trades: 72

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)


## Last session

1 rounds x 8 ideas with the claude proposer in 5s. Best deflated reward went from 0.013 to 0.013. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0. Claude CLI cost this session: $0.00.
