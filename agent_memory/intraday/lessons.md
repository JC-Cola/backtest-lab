# What the intraday agent has learned so far

Updated 2026-11-06. 6146 trials in total: 4891 scored, 1255 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.000 (when found 0.000) | folds [1.394, 1.176, 1.838] | SPY at 11:00: long when gap > p85 and prev_day_ret < p99 and range_pos > p20; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.615, 1.209, 1.678] | SPY at 11:00: long when gap > p85 and prev_day_ret < p99 and range_pos > p25; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.611, 1.745, 1.107] | SPY at 11:00: long when gap > p85 and prev_day_ret < p99 and ret_30 > p20; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.444, 1.339, 1.836] | SPY at 11:00: long when gap > p85 and range_pos > p20 and vol_ratio < p95; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.849, 0.569, 1.513] | SPY at 11:00: long when gap > p85 and prev_day_ret < p84 and range_pos > p25; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.444, 1.176, 1.901] | SPY at 11:00: long when gap > p85 and range_pos > p20 and vol_ratio < p99; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.394, 1.176, 1.878] | SPY at 11:00: long when gap > p85 and range_pos > p20; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.394, 1.176, 1.878] | SPY at 11:00: long when gap > p85 and range_pos > p20 and ret_prev_close > p1; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.304, 1.296, 1.812] | SPY at 11:00: long when gap > p85 and range_pos > p20 and vol_ratio < p90; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.615, 1.209, 1.734] | SPY at 11:00: long when gap > p85 and range_pos > p25; hold 240 min

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

- too few trades: 1255

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 12s. Best deflated reward went from 0.000 to 0.000. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
