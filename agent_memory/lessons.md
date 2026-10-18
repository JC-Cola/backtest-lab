# What the agent has learned so far

Updated 2026-10-18. 3100 trials in total: 2223 scored, 877 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.360 (when found 0.374) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.337 (when found 0.444) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.337 (when found 0.380) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.324 (when found 0.363) | folds [0.403, 1.191, 0.829] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p70 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.324 (when found 0.327) | folds [0.403, 1.191, 0.829] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p70 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.323 (when found 0.324) | folds [0.238, 1.143, 1.045] | IWM: buy when ret_20 > p80 and rsi_2 < p55 and to_month_end > p1 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.292 (when found 0.382) | folds [0.191, 1.107, 1.062] | IWM: buy when ret_20 > p80 and rsi_2 < p55 and to_month_end > p10 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.292 (when found 0.374) | folds [0.191, 1.107, 1.062] | IWM: buy when ret_20 > p80 and rsi_2 < p55 and to_month_end > p10 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days
- reward now 0.292 (when found 0.348) | folds [0.191, 1.107, 1.062] | IWM: buy when ret_20 > p80 and rsi_2 < p55 and to_month_end > p10 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.281 (when found 0.389) | folds [0.238, 1.136, 0.868] | IWM: buy when ret_20 > p80 and rsi_2 < p55 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.26
- vix_vs_50: 1.12
- curve: 1.11
- rsi_2: 1.09
- dom_next: 1.09
- to_fomc: 1.08
- ret_20: 1.06
- ibs: 1.05

## Features that tend to hurt

- wr_2: 0.82
- dist_sma_200: 0.81
- ret_60: 0.80
- pre_holiday: 0.76
- credit_trend: 0.76

## Why ideas were rejected

- too few trades: 877

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

50 rounds x 10 ideas with the evolution proposer in 338s. Best deflated reward went from 0.462 to 0.360. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
