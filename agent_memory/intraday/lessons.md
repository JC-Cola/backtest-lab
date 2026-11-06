# What the intraday agent has learned so far

Updated 2026-11-06. 2276 trials in total: 1761 scored, 515 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.000 (when found 0.001) | folds [1.21, 1.535, 2.822] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vol_ratio > p30; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.21, 1.535, 2.822] | QQQ at 10:00: long when ret_open > p90 and rv_5d < p95 and vol_ratio > p30; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.001) | folds [1.21, 1.561, 2.631] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vol_ratio > p35; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.21, 1.561, 2.631] | QQQ at 10:00: long when ret_open > p90 and rv_5d < p95 and vol_ratio > p35; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [0.951, 1.677, 2.616] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vol_ratio > p30; hold 60 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [0.951, 1.677, 2.616] | QQQ at 10:00: long when ret_open > p90 and rv_5d < p95 and vol_ratio > p30; hold 60 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.245, 1.588, 2.474] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p89 and vol_ratio > p30; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.001) | folds [1.21, 1.596, 2.432] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vol_ratio > p40; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.146, 1.596, 2.461] | QQQ at 10:00: long when ret_open > p90 and rv_5d < p95 and vol_ratio > p45; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.315, 1.359, 2.357] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vol_ratio > p30; hold to the close; stop 25 bp, target 1.5R

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

- too few trades: 515

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 2s. Best deflated reward went from 0.000 to 0.000. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
