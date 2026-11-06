# What the agent has learned so far

Updated 2026-11-06. 14760 trials in total: 10086 scored, 4674 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.245 (when found 0.273) | folds [1.241, 0.452, 1.184] | TLT: buy when credit_trend < p30 and ret_20 < p75 and ret_250 > p10 (only if rv_20_pct < p95); sell when to_month_end > p95 or after 10 days
- reward now 0.236 (when found 0.246) | folds [0.272, 1.334, 0.887] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p70 (only if range_rank_7 > p40); sell when ret_5 > p50 or after 5 days
- reward now 0.227 (when found 0.264) | folds [1.001, 1.113, 1.381] | GLD: buy when dom_next < p5 and range_rank_7 < p75 and vix_vs_50 < p70 (only if ret_1 < p85); sell when wr_2 < p50 or after 20 days
- reward now 0.227 (when found 0.263) | folds [1.001, 1.113, 1.381] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p70 (only if ret_1 < p85); sell when wr_2 < p50 or after 20 days
- reward now 0.224 (when found 0.225) | folds [0.378, 1.225, 1.01] | IWM: buy when dist_sma_200 < p40 and ret_20 > p80 and rsi_2 < p70 (only if range_rank_7 > p40); sell when ret_5 > p50 or after 5 days
- reward now 0.216 (when found 0.252) | folds [1.241, 0.448, 1.101] | TLT: buy when credit_trend < p30 and ret_20 < p75 and ret_250 > p15 (only if rv_20_pct < p95); sell when to_month_end > p95 or after 10 days
- reward now 0.212 (when found 0.213) | folds [0.95, 0.601, 1.356] | TLT: buy when credit_trend < p30 and ret_20 < p70 and ret_250 > p25 (only if rv_20_pct < p99); sell when to_month_end > p95 or after 10 days
- reward now 0.208 (when found 0.374) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.208 (when found 0.328) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days
- reward now 0.208 (when found 0.303) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.13
- curve: 1.12
- ret_5: 1.09
- rsi_2: 1.07
- vix_vs_50: 1.06
- gap: 1.06
- wr_2: 1.05
- ret_20: 1.05

## Features that tend to hurt

- ret_60: 0.94
- down_streak: 0.93
- dist_sma_200: 0.92
- vix_ratio: 0.91
- rv_20_pct: 0.91

## Why ideas were rejected

- too few trades: 4674

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 126s. Best deflated reward went from 0.247 to 0.245. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
