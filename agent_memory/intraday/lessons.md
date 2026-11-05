# What the intraday agent has learned so far

Updated 2026-11-05. 1984 trials in total: 1511 scored, 473 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.000 (when found 0.001) | folds [1.21, 1.596, 2.432] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vol_ratio > p40; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.001) | folds [1.011, 1.081, 2.696] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vol_ratio > p25; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.011, 1.081, 2.62] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vol_ratio > p20; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.001) | folds [1.059, 0.824, 2.595] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.059, 0.824, 2.595] | QQQ at 10:00: long when ret_30 > p90 and ret_open > p10 and rv_5d < p95; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.059, 0.824, 2.595] | QQQ at 10:00: long when ret_open > p90 and rv_5d < p95; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.059, 0.824, 2.595] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vwap_dist > p10; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.059, 0.824, 2.595] | QQQ at 10:00: long when ret_30 > p90 and ret_open > p1 and rv_5d < p95; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.09, 2.355, 1.628] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p75 and vol_ratio > p25; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.251, 0.677, 2.58] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and rv_5d > p25; hold 120 min; stop 25 bp, target 1.5R

## Features that tend to help (learned weights, 1 = average)

- gap: 1.00
- prev_day_ibs: 1.00
- vwap_dist: 1.00
- ret_30: 1.00
- noise_z: 1.00
- rv_5d: 1.00
- ret_prev_close: 1.00
- prev_day_ret: 1.00

## Features that tend to hurt

- ret_prev_close: 1.00
- prev_day_ret: 1.00
- vol_ratio: 1.00
- ret_open: 1.00
- range_pos: 1.00

## Why ideas were rejected

- too few trades: 473

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 2s. Best deflated reward went from 0.000 to 0.000. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
