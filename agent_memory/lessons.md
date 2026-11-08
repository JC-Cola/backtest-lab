# What the agent has learned so far

Updated 2026-11-08. 19728 trials in total: 13415 scored, 6313 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.239 (when found 0.279) | folds [0.378, 1.338, 1.01] | IWM: buy when dist_sma_200 < p40 and ret_20 > p80 and rsi_2 < p70 (only if range_rank_7 > p35); sell when ret_5 > p50 or after 5 days
- reward now 0.205 (when found 0.264) | folds [1.001, 1.113, 1.381] | GLD: buy when dom_next < p5 and range_rank_7 < p75 and vix_vs_50 < p70 (only if ret_1 < p85); sell when wr_2 < p50 or after 20 days
- reward now 0.205 (when found 0.263) | folds [1.001, 1.113, 1.381] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p70 (only if ret_1 < p85); sell when wr_2 < p50 or after 20 days
- reward now 0.191 (when found 0.246) | folds [0.272, 1.334, 0.887] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p70 (only if range_rank_7 > p40); sell when ret_5 > p50 or after 5 days
- reward now 0.188 (when found 0.252) | folds [1.393, 0.694, 1.43] | TLT: buy when credit_trend < p30 and ret_20 < p75 and ret_250 > p30 (only if rv_20_pct < p90); sell when to_month_end > p95 or after 10 days
- reward now 0.185 (when found 0.323) | folds [2.086, 1.26, 0.308] | SPY: buy when ibs < p10 and ret_20 < p40 and to_month_end < p90 (only if curve < p90); sell when gap < p30 or after 5 days
- reward now 0.183 (when found 0.198) | folds [0.656, 0.926, 1.745] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p70 (only if ret_1 < p85); sell when ibs < p70 or after 10 days
- reward now 0.183 (when found 0.193) | folds [0.656, 0.926, 1.745] | GLD: buy when dom_next < p5 and range_rank_7 < p75 and vix_vs_50 < p70 (only if ret_1 < p85); sell when ibs < p70 or after 10 days
- reward now 0.182 (when found 0.198) | folds [0.282, 1.289, 0.977] | IWM: buy when dist_sma_200 < p40 and ret_20 > p80 and rsi_2 < p70 (only if range_rank_7 > p40); sell when rsi_2 > p50 or after 5 days
- reward now 0.182 (when found 0.198) | folds [1.49, 1.322, 1.661] | DIA: buy when dow_next < p70 and ret_5 < p10 and wr_2 < p15 (only if curve < p60); sell when gap > p90 or after 3 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.11
- curve: 1.10
- rsi_2: 1.07
- ret_5: 1.07
- ret_20: 1.06
- gap: 1.05
- vix_vs_50: 1.05
- ibs: 1.04

## Features that tend to hurt

- rv_20_pct: 0.96
- dist_sma_50: 0.95
- dist_sma_200: 0.94
- down_streak: 0.94
- vix_ratio: 0.93

## Why ideas were rejected

- too few trades: 6311
- risk review: 2

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 174s. Best deflated reward went from 0.239 to 0.239. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
