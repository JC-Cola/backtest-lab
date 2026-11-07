# What the agent has learned so far

Updated 2026-11-07. 16534 trials in total: 11253 scored, 5281 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.271 (when found 0.279) | folds [0.378, 1.338, 1.01] | IWM: buy when dist_sma_200 < p40 and ret_20 > p80 and rsi_2 < p70 (only if range_rank_7 > p35); sell when ret_5 > p50 or after 5 days
- reward now 0.229 (when found 0.264) | folds [1.001, 1.113, 1.381] | GLD: buy when dom_next < p5 and range_rank_7 < p75 and vix_vs_50 < p70 (only if ret_1 < p85); sell when wr_2 < p50 or after 20 days
- reward now 0.229 (when found 0.263) | folds [1.001, 1.113, 1.381] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p70 (only if ret_1 < p85); sell when wr_2 < p50 or after 20 days
- reward now 0.219 (when found 0.246) | folds [0.272, 1.334, 0.887] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p70 (only if range_rank_7 > p40); sell when ret_5 > p50 or after 5 days
- reward now 0.215 (when found 0.261) | folds [1.284, 0.43, 1.448] | QQQ: buy when dom_next > p30 and rsi_14 < p5 and rsi_2 < p45 (only if curve > p20); sell when ret_20 < p5 or after 10 days
- reward now 0.207 (when found 0.225) | folds [0.378, 1.225, 1.01] | IWM: buy when dist_sma_200 < p40 and ret_20 > p80 and rsi_2 < p70 (only if range_rank_7 > p40); sell when ret_5 > p50 or after 5 days
- reward now 0.204 (when found 0.281) | folds [1.393, 0.599, 1.361] | TLT: buy when credit_trend < p30 and ret_20 < p75 and ret_250 > p25 (only if rv_20_pct < p90); sell when to_month_end > p95 or after 10 days
- reward now 0.197 (when found 0.204) | folds [0.171, 2.106, 2.141] | BTC-USD: buy when curve < p70 and gap > p40 and rsi_14 > p70 (only if ret_250 > p16); sell when ret_60 > p95 or after 3 days
- reward now 0.193 (when found 0.212) | folds [1.47, 1.324, 1.598] | DIA: buy when dow_next < p65 and ret_5 < p10 and wr_2 < p15 (only if curve < p60); sell when wr_10 < p10 or after 3 days
- reward now 0.193 (when found 0.209) | folds [1.47, 1.324, 1.598] | DIA: buy when dow_next < p60 and ret_5 < p10 and wr_2 < p15 (only if curve < p60); sell when wr_10 < p10 or after 3 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.12
- curve: 1.11
- ret_5: 1.08
- rsi_2: 1.07
- gap: 1.06
- vix_vs_50: 1.06
- ret_20: 1.05
- wr_2: 1.04

## Features that tend to hurt

- rv_20_pct: 0.95
- ret_60: 0.94
- down_streak: 0.93
- dist_sma_200: 0.93
- vix_ratio: 0.92

## Why ideas were rejected

- too few trades: 5281

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

1 rounds x 10 ideas with the evolution proposer in 69s. Best deflated reward went from 0.271 to 0.271. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
