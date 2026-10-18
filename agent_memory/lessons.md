# What the agent has learned so far

Updated 2026-10-18. 7100 trials in total: 5049 scored, 2051 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.251 (when found 0.302) | folds [0.656, 0.926, 1.704] | GLD: buy when dom_next < p5 and range_rank_7 < p75 and vix_vs_50 < p70 (only if ret_1 < p85); sell when ibs < p70 or after 5 days
- reward now 0.251 (when found 0.269) | folds [0.656, 0.926, 1.704] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p70 (only if ret_1 < p85); sell when ibs < p70 or after 5 days
- reward now 0.245 (when found 0.275) | folds [2.061, 1.167, 0.111] | SPY: buy when ibs < p10 and ret_20 < p40 and to_month_end < p90 (only if curve < p90); sell when gap < p70 or after 5 days
- reward now 0.240 (when found 0.374) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.240 (when found 0.328) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days
- reward now 0.240 (when found 0.303) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.234 (when found 0.316) | folds [0.283, 2.075, 2.134] | BTC-USD: buy when curve < p70 and gap > p40 and rsi_14 > p75 (only if ret_250 > p16); sell when ret_60 > p95 or after 3 days
- reward now 0.222 (when found 0.444) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.222 (when found 0.380) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.222 (when found 0.324) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.18
- curve: 1.15
- ret_5: 1.12
- vix_vs_50: 1.11
- wr_2: 1.07
- rsi_2: 1.07
- gap: 1.07
- dom_next: 1.06

## Features that tend to hurt

- down_streak: 0.90
- vix_ratio: 0.88
- dist_sma_200: 0.88
- rv_20_pct: 0.86
- credit_trend: 0.85

## Why ideas were rejected

- too few trades: 2051

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

50 rounds x 10 ideas with the evolution proposer in 997s. Best deflated reward went from 0.379 to 0.251. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
