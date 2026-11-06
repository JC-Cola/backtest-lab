# What the agent has learned so far

Updated 2026-11-06. 13940 trials in total: 9542 scored, 4398 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.244 (when found 0.246) | folds [0.272, 1.334, 0.887] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p70 (only if range_rank_7 > p40); sell when ret_5 > p50 or after 5 days
- reward now 0.228 (when found 0.264) | folds [1.001, 1.113, 1.381] | GLD: buy when dom_next < p5 and range_rank_7 < p75 and vix_vs_50 < p70 (only if ret_1 < p85); sell when wr_2 < p50 or after 20 days
- reward now 0.228 (when found 0.263) | folds [1.001, 1.113, 1.381] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p70 (only if ret_1 < p85); sell when wr_2 < p50 or after 20 days
- reward now 0.217 (when found 0.374) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.217 (when found 0.328) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days
- reward now 0.217 (when found 0.303) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.204 (when found 0.323) | folds [2.086, 1.26, 0.308] | SPY: buy when ibs < p10 and ret_20 < p40 and to_month_end < p90 (only if curve < p90); sell when gap < p30 or after 5 days
- reward now 0.203 (when found 0.204) | folds [0.037, 1.245, 1.222] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p40); sell when ret_5 > p50 or after 5 days
- reward now 0.200 (when found 0.200) | folds [2.058, 1.164, 0.453] | SPY: buy when ibs < p10 and ret_20 < p55 and to_month_end < p90 (only if curve < p90); sell when gap < p30 or after 5 days
- reward now 0.199 (when found 0.444) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.13
- curve: 1.13
- ret_5: 1.10
- rsi_2: 1.07
- vix_vs_50: 1.06
- gap: 1.06
- wr_2: 1.06
- ret_20: 1.05

## Features that tend to hurt

- ret_60: 0.94
- down_streak: 0.93
- dist_sma_200: 0.92
- vix_ratio: 0.91
- rv_20_pct: 0.89

## Why ideas were rejected

- too few trades: 4398

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 172s. Best deflated reward went from 0.245 to 0.244. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
