# What the agent has learned so far

Updated 2026-10-18. 1600 trials in total: 1161 scored, 439 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.381 (when found 0.445) | folds [0.603, 0.904, 1.637] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p70; sell when ibs < p70 or after 5 days
- reward now 0.378 (when found 0.852) | folds [0.723, 0.926, 1.486] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p65 (only if ret_1 < p90); sell when ibs < p70 or after 5 days
- reward now 0.377 (when found 0.392) | folds [0.279, 1.053, 0.851] | IWM: buy when ret_20 > p80 and rsi_2 < p50 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.377 (when found 0.383) | folds [0.279, 1.053, 0.851] | IWM: buy when ret_20 > p80 and rsi_2 < p50 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.372 (when found 0.377) | folds [0.689, 0.956, 1.486] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p65 (only if wr_10 < p95); sell when ibs < p70 or after 5 days
- reward now 0.345 (when found 0.573) | folds [0.383, 1.041, 0.668] | IWM: buy when ret_20 > p80 and rsi_2 < p50 and vix > p25 (only if range_rank_7 > p40); hold 5 days
- reward now 0.344 (when found 0.361) | folds [0.603, 0.883, 1.578] | GLD: buy when range_rank_7 < p75 and vix_vs_50 < p75 (only if dom_next < p5); sell when ibs < p70 or after 5 days
- reward now 0.338 (when found 0.352) | folds [0.003, 1.177, 0.891] | IWM: buy when ret_20 > p85 and rsi_2 < p55 and vix > p30 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.337 (when found 0.482) | folds [0.335, 1.041, 0.701] | IWM: buy when ret_20 > p80 and rsi_2 < p50 and vix > p30 (only if range_rank_7 > p40); hold 5 days
- reward now 0.337 (when found 0.382) | folds [0.335, 1.041, 0.701] | IWM: buy when ret_20 > p80 and rsi_2 < p50 and vix > p30 (only if range_rank_7 > p30); hold 5 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.24
- curve: 1.17
- ibs: 1.12
- to_fomc: 1.12
- ret_20: 1.08
- rsi_2: 1.08
- vix_vs_50: 1.07
- range_rank_7: 1.05

## Features that tend to hurt

- wr_2: 0.79
- dist_sma_200: 0.78
- ret_60: 0.76
- pre_holiday: 0.66
- credit_trend: 0.65

## Why ideas were rejected

- too few trades: 439

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

50 rounds x 10 ideas with the evolution proposer in 232s. Best deflated reward went from 0.746 to 0.381. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
