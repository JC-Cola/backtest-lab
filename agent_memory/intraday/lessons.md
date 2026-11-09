# What the intraday agent has learned so far

Updated 2026-11-09. 11019 trials in total: 9016 scored, 2003 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.000 (when found 0.000) | folds [1.394, 1.176, 1.838] | SPY at 11:00: long when gap > p85 and prev_day_ret < p99 and range_pos > p20; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.615, 1.209, 1.678] | SPY at 11:00: long when gap > p85 and prev_day_ret < p99 and range_pos > p25; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.611, 1.745, 1.107] | SPY at 11:00: long when gap > p85 and prev_day_ret < p99 and ret_30 > p20; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.819, 0.845, 1.58] | SPY at 11:00: long when gap > p85 and prev_day_ret < p89 and range_pos > p25; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.255, 1.806, 1.255] | SPY at 11:00: long when gap > p85 and prev_day_ret < p99 and ret_30 > p15; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.703, 0.951, 1.488] | SPY at 11:00: long when gap > p85 and prev_day_ret < p99 and range_pos > p30; hold 240 min
- reward now 0.000 (when found 0.000) | folds [1.22, 1.55, 1.216] | SPY at 11:00: long when gap > p85 and prev_day_ret < p89 and ret_30 > p15; hold 240 min
- reward now 0.000 (when found 0.001) | folds [1.21, 1.535, 2.822] | QQQ at 10:00: long when ret_30 > p90 and rv_5d < p95 and vol_ratio > p30; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.21, 1.535, 2.822] | QQQ at 10:00: long when ret_open > p90 and rv_5d < p95 and vol_ratio > p30; hold 120 min; stop 25 bp, target 1.5R
- reward now 0.000 (when found 0.000) | folds [1.571, 0.817, 1.582] | SPY at 11:00: long when gap > p85 and prev_day_ret < p89 and range_pos > p20; hold 240 min

## Features that tend to help (learned weights, 1 = average)

- noise_z: 1.00
- ret_30: 1.00
- vwap_dist: 1.00
- prev_day_ibs: 1.00
- rv_5d: 1.00
- gap: 1.00
- ret_prev_close: 1.00
- vol_ratio: 1.00

## Features that tend to hurt

- ret_prev_close: 1.00
- vol_ratio: 1.00
- ret_open: 1.00
- prev_day_ret: 1.00
- range_pos: 1.00

## Why ideas were rejected

- too few trades: 1987
- risk review: 12
- invalid spec: 4

## Held-out tests (one shot each; BH-corrected together with the 40 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 52s. Best deflated reward went from 0.000 to 0.000. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
