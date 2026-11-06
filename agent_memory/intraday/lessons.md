# What the intraday agent has learned so far

Updated 2026-11-06. 5684 trials in total: 4512 scored, 1172 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.000 (when found 0.000) | folds [1.444, 1.339, 1.836] | SPY at 11:00: long when gap > p85 and range_pos > p20 and vol_ratio < p95; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.394, 1.176, 1.878] | SPY at 11:00: long when gap > p85 and range_pos > p20; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.615, 1.209, 1.734] | SPY at 11:00: long when gap > p85 and range_pos > p25; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.476, 1.332, 1.643] | SPY at 11:00: long when gap > p85 and range_pos > p25 and vol_ratio < p90; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.255, 1.806, 1.358] | SPY at 11:00: long when gap > p85 and ret_30 > p15; hold 240 min
- reward now 0.000 (when found 0.000) | folds [0.845, 1.623, 1.772] | SPY at 11:00: long when gap > p85 and rv_5d > p25; hold 240 min
- reward now 0.000 (when found 0.000) | folds [0.867, 1.455, 1.782] | SPY at 11:00: long when gap > p85 and rv_5d > p15; hold 240 min
- reward now 0.000 (when found 0.000) | folds [0.982, 1.412, 1.751] | SPY at 11:00: long when gap > p85 and rv_5d > p1; hold 240 min
- reward now 0.000 (when found 0.000) | folds [0.867, 1.455, 1.751] | SPY at 11:00: long when gap > p85 and rv_5d > p10; hold 240 min
- reward now 0.000 (when found 0.000) | folds [0.728, 1.412, 1.751] | SPY at 11:00: long when gap > p85; hold 240 min

## Features that tend to help (learned weights, 1 = average)

- vwap_dist: 1.00
- noise_z: 1.00
- ret_30: 1.00
- prev_day_ibs: 1.00
- gap: 1.00
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

- too few trades: 1172

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 7s. Best deflated reward went from 0.000 to 0.000. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
