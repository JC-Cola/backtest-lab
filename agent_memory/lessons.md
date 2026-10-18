# What the agent has learned so far

Updated 2026-10-18. 2100 trials in total: 1535 scored, 565 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.415 (when found 0.444) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.385 (when found 0.472) | folds [0.699, 0.502, 1.067] | QQQ: buy when rsi_14 < p5; sell when vix_vs_50 > p50 or after 5 days
- reward now 0.385 (when found 0.417) | folds [0.699, 0.502, 1.067] | QQQ: buy when rsi_14 < p5 (only if rsi_14 < p80); sell when vix_vs_50 > p50 or after 5 days
- reward now 0.379 (when found 0.379) | folds [0.691, 0.502, 1.067] | QQQ: buy when ret_20 < p45 and rsi_14 < p5; sell when vix_vs_50 > p50 or after 5 days
- reward now 0.363 (when found 0.382) | folds [0.191, 1.107, 1.062] | IWM: buy when ret_20 > p80 and rsi_2 < p55 and to_month_end > p10 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.363 (when found 0.374) | folds [0.191, 1.107, 1.062] | IWM: buy when ret_20 > p80 and rsi_2 < p55 and to_month_end > p10 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days
- reward now 0.353 (when found 0.374) | folds [0.652, 0.502, 1.067] | QQQ: buy when ret_20 < p30 and rsi_14 < p5; sell when vix_vs_50 > p50 or after 5 days
- reward now 0.349 (when found 0.389) | folds [0.238, 1.136, 0.868] | IWM: buy when ret_20 > p80 and rsi_2 < p55 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.349 (when found 0.386) | folds [0.238, 1.136, 0.868] | IWM: buy when ret_20 > p80 and rsi_2 < p55 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.349 (when found 0.372) | folds [0.238, 1.136, 0.868] | IWM: buy when ret_20 > p80 and rsi_2 < p55 and rsi_2 > p5 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.22
- curve: 1.14
- to_fomc: 1.11
- ibs: 1.09
- ret_20: 1.08
- vix_vs_50: 1.08
- rsi_2: 1.07
- range_rank_7: 1.05

## Features that tend to hurt

- dist_sma_200: 0.83
- ret_60: 0.80
- wr_2: 0.79
- credit_trend: 0.71
- pre_holiday: 0.69

## Why ideas were rejected

- too few trades: 565

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

50 rounds x 10 ideas with the evolution proposer in 268s. Best deflated reward went from 0.381 to 0.415. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
