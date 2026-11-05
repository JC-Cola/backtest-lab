# What the agent has learned so far

Updated 2026-11-05. 10900 trials in total: 7563 scored, 3337 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.233 (when found 0.302) | folds [0.656, 0.926, 1.704] | GLD: buy when dom_next < p5 and range_rank_7 < p75 and vix_vs_50 < p70 (only if ret_1 < p85); sell when ibs < p70 or after 5 days
- reward now 0.233 (when found 0.269) | folds [0.656, 0.926, 1.704] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p70 (only if ret_1 < p85); sell when ibs < p70 or after 5 days
- reward now 0.232 (when found 0.374) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.232 (when found 0.328) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days
- reward now 0.232 (when found 0.303) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.214 (when found 0.444) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.214 (when found 0.380) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.214 (when found 0.324) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days
- reward now 0.208 (when found 0.324) | folds [0.238, 1.143, 1.045] | IWM: buy when ret_20 > p80 and rsi_2 < p55 and to_month_end > p1 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.208 (when found 0.319) | folds [0.238, 1.143, 1.045] | IWM: buy when ret_20 > p80 and rsi_2 < p55 and to_month_end > p1 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.15
- curve: 1.14
- ret_5: 1.11
- rsi_2: 1.07
- vix_vs_50: 1.07
- wr_2: 1.07
- gap: 1.06
- ret_20: 1.05

## Features that tend to hurt

- ret_60: 0.93
- down_streak: 0.92
- dist_sma_200: 0.91
- vix_ratio: 0.91
- rv_20_pct: 0.88

## Why ideas were rejected

- too few trades: 3337

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 21s. Best deflated reward went from 0.233 to 0.233. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
