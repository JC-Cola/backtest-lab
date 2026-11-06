# What the intraday agent has learned so far

Updated 2026-11-06. 4978 trials in total: 3950 scored, 1028 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.000 (when found 0.000) | folds [1.253, 0.145, 1.983] | SPY at 11:00: long when ret_prev_close > p80; hold 120 min
- reward now 0.000 (when found 0.001) | folds [1.21, 1.535, 2.822] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vol_ratio > p30; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.21, 1.535, 2.822] | QQQ at 10:00: long when ret_open > p90 and rv_5d < p95 and vol_ratio > p30; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.253, 0.145, 2.175] | SPY at 11:00: long when ret_prev_close > p80; hold 120 min; stop 200 bp
- reward now 0.000 (when found 0.000) | folds [1.633, -0.356, 2.061] | SPY at 11:00: long when ret_prev_close > p85; hold 120 min
- reward now 0.000 (when found 0.001) | folds [1.21, 1.561, 2.631] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vol_ratio > p35; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.21, 1.561, 2.631] | QQQ at 10:00: long when ret_open > p90 and rv_5d < p95 and vol_ratio > p35; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.21, 1.535, 2.63] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p94 and vol_ratio > p30; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.21, 1.535, 2.63] | QQQ at 10:00: long when ret_open > p90 and rv_5d < p94 and vol_ratio > p30; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.663, 0.019, 2.266] | SPY at 11:00: long when prev_day_ret < p40 and ret_prev_close > p80; hold 120 min

## Features that tend to help (learned weights, 1 = average)

- vwap_dist: 1.00
- gap: 1.00
- noise_z: 1.00
- prev_day_ibs: 1.00
- ret_30: 1.00
- rv_5d: 1.00
- ret_prev_close: 1.00
- prev_day_ret: 1.00

## Features that tend to hurt

- ret_prev_close: 1.00
- prev_day_ret: 1.00
- ret_open: 1.00
- vol_ratio: 1.00
- range_pos: 1.00

## Why ideas were rejected

- too few trades: 1028

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 10s. Best deflated reward went from 0.000 to 0.000. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
