# What the intraday agent has learned so far

Updated 2026-11-04. 434 trials in total: 349 scored, 85 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.044 (when found 0.044) | folds [0.504, 2.081, 1.094] | QQQ at 10:00: long when noise_z > p70 and ret_30 > p90 and rv_5d < p75; hold 60 min; stop 25 bp, target 1.5R
- reward now 0.017 (when found 0.022) | folds [0.644, 1.587, 0.936] | QQQ at 10:00: long when noise_z > p70 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp, target 1.5R
- reward now 0.015 (when found 0.019) | folds [0.517, 1.587, 0.936] | QQQ at 10:00: long when noise_z > p75 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp, target 1.5R
- reward now 0.005 (when found 0.015) | folds [0.415, 1.58, 0.658] | QQQ at 10:00: long when noise_z > p65 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp, target 2R
- reward now 0.005 (when found 0.008) | folds [0.202, 0.522, 1.522] | QQQ at 15:00: short when prev_day_ret > p80; hold 60 min; stop 50 bp, target 3R
- reward now 0.005 (when found 0.013) | folds [0.643, 1.598, 0.536] | QQQ at 10:00: long when noise_z > p65 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp, target 3R
- reward now 0.005 (when found 0.010) | folds [0.643, 1.598, 0.536] | QQQ at 10:00: long when noise_z > p50 and ret_30 > p90 and rv_5d < p80; hold 60 min; stop 25 bp, target 3R
- reward now 0.005 (when found 0.007) | folds [1.076, 1.359, 0.461] | QQQ at 10:00: long when noise_z > p50 and ret_30 > p90 and rv_5d < p80; hold to the close; stop 25 bp, target 3R
- reward now 0.004 (when found 0.013) | folds [0.003, 1.251, 1.19] | QQQ at 14:30: long when gap > p90 and vwap_dist < p80; hold 30 min
- reward now 0.001 (when found 0.002) | folds [0.845, 0.467, 1.027] | SPY at 12:30: short when noise_z < p30 and ret_open < p10; hold 120 min

## Features that tend to help (learned weights, 1 = average)

- gap: 1.01
- prev_day_ibs: 1.01
- ret_30: 1.00
- vwap_dist: 1.00
- rv_5d: 1.00
- noise_z: 1.00
- vol_ratio: 0.99
- ret_prev_close: 0.99

## Features that tend to hurt

- vol_ratio: 0.99
- ret_prev_close: 0.99
- prev_day_ret: 0.99
- ret_open: 0.99
- range_pos: 0.99

## Why ideas were rejected

- too few trades: 85

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 0s. Best deflated reward went from 0.021 to 0.044. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
