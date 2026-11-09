# What the agent has learned so far

Updated 2026-11-09. 20617 trials in total: 14280 scored, 6337 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.299 (when found 0.304) | folds [0.52, 1.408, 0.989] | IWM: buy when dist_sma_200 < p40 and ret_20 > p80 and rsi_2 < p70 (only if range_rank_7 > p35); sell when z_20 > p70 or after 5 days
- reward now 0.243 (when found 0.244) | folds [0.52, 1.301, 0.996] | IWM: buy when dist_sma_200 < p40 and ret_20 > p80 and rsi_2 < p75 (only if range_rank_7 > p35); sell when z_20 > p70 or after 5 days
- reward now 0.228 (when found 0.231) | folds [0.834, 1.115, 0.927] | IWM: buy when dist_sma_200 < p40 and ret_20 > p80 and rsi_2 < p70 (only if range_rank_7 > p30); sell when z_20 > p70 or after 5 days
- reward now 0.228 (when found 0.230) | folds [0.834, 1.115, 0.927] | IWM: buy when dist_sma_200 < p40 and ret_20 > p80 and rsi_2 < p70 (only if range_rank_7 > p20); sell when z_20 > p70 or after 5 days
- reward now 0.228 (when found 0.279) | folds [0.378, 1.338, 1.01] | IWM: buy when dist_sma_200 < p40 and ret_20 > p80 and rsi_2 < p70 (only if range_rank_7 > p35); sell when ret_5 > p50 or after 5 days
- reward now 0.195 (when found 0.264) | folds [1.001, 1.113, 1.381] | GLD: buy when dom_next < p5 and range_rank_7 < p75 and vix_vs_50 < p70 (only if ret_1 < p85); sell when wr_2 < p50 or after 20 days
- reward now 0.195 (when found 0.263) | folds [1.001, 1.113, 1.381] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p70 (only if ret_1 < p85); sell when wr_2 < p50 or after 20 days
- reward now 0.184 (when found 0.184) | folds [0.755, 1.104, 0.846] | IWM: buy when dist_sma_200 < p35 and ret_20 > p80 and rsi_2 < p70 (only if range_rank_7 > p30); sell when z_20 > p70 or after 5 days
- reward now 0.181 (when found 0.246) | folds [0.272, 1.334, 0.887] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p70 (only if range_rank_7 > p40); sell when ret_5 > p50 or after 5 days
- reward now 0.179 (when found 0.252) | folds [1.393, 0.694, 1.43] | TLT: buy when credit_trend < p30 and ret_20 < p75 and ret_250 > p30 (only if rv_20_pct < p90); sell when to_month_end > p95 or after 10 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.10
- curve: 1.10
- rsi_2: 1.07
- ret_5: 1.06
- ret_20: 1.06
- gap: 1.05
- vix_vs_50: 1.04
- ibs: 1.04

## Features that tend to hurt

- rv_20_pct: 0.96
- dist_sma_50: 0.96
- dist_sma_200: 0.95
- down_streak: 0.94
- vix_ratio: 0.93

## Why ideas were rejected

- too few trades: 6329
- risk review: 8

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 111s. Best deflated reward went from 0.300 to 0.299. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
