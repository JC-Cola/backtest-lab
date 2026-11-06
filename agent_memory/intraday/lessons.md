# What the intraday agent has learned so far

Updated 2026-11-06. 5450 trials in total: 4324 scored, 1126 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.000 (when found 0.000) | folds [1.394, 1.176, 1.878] | SPY at 11:00: long when gap > p85 and range_pos > p20; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.255, 1.806, 1.358] | SPY at 11:00: long when gap > p85 and ret_30 > p15; hold 240 min
- reward now 0.000 (when found 0.000) | folds [0.728, 1.412, 1.751] | SPY at 11:00: long when gap > p85; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.498, 0.645, 1.723] | SPY at 11:00: long when gap > p85 and ret_prev_close > p80; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.633, 0.493, 1.681] | SPY at 11:00: long when gap > p85 and ret_prev_close > p85; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.029, 0.251, 2.421] | SPY at 11:00: long when gap > p85 and ret_prev_close > p80; hold 120 min
- reward now 0.000 (when found 0.000) | folds [1.255, 1.508, 1.21] | SPY at 11:00: long when gap > p85 and ret_30 > p10; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.029, 0.251, 2.324] | SPY at 11:00: long when gap > p85 and range_pos > p5 and ret_prev_close > p80; hold 120 min
- reward now 0.000 (when found 0.000) | folds [1.936, 0.334, 1.199] | SPY at 11:00: long when gap > p85 and ret_prev_close > p90; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.595, -0.098, 2.43] | SPY at 11:00: long when gap > p85 and ret_prev_close > p85; hold 120 min

## Features that tend to help (learned weights, 1 = average)

- vwap_dist: 1.00
- noise_z: 1.00
- ret_30: 1.00
- gap: 1.00
- prev_day_ibs: 1.00
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

- too few trades: 1126

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 12s. Best deflated reward went from 0.000 to 0.000. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
