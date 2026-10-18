# What the agent has learned so far

Updated 2026-10-18. 7850 trials in total: 5540 scored, 2310 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.314 (when found 0.324) | folds [1.476, 0.337, 1.05] | QQQ: buy when dom_next > p30 and rsi_14 < p5 and rsi_2 < p50 (only if curve > p20); sell when vix_vs_50 > p50 or after 5 days
- reward now 0.246 (when found 0.302) | folds [0.656, 0.926, 1.704] | GLD: buy when dom_next < p5 and range_rank_7 < p75 and vix_vs_50 < p70 (only if ret_1 < p85); sell when ibs < p70 or after 5 days
- reward now 0.246 (when found 0.269) | folds [0.656, 0.926, 1.704] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p70 (only if ret_1 < p85); sell when ibs < p70 or after 5 days
- reward now 0.235 (when found 0.236) | folds [1.368, 0.337, 1.05] | QQQ: buy when dom_next > p30 and rsi_14 < p5 and rsi_2 < p55 (only if curve > p20); sell when vix_vs_50 > p50 or after 5 days
- reward now 0.226 (when found 0.374) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.226 (when found 0.328) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days
- reward now 0.226 (when found 0.303) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.210 (when found 0.215) | folds [0.656, 0.903, 1.644] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p75 (only if ret_1 < p85); sell when ibs < p70 or after 5 days
- reward now 0.209 (when found 0.267) | folds [0.927, 0.364, 0.682] | TLT: buy when credit_trend < p30; sell when ret_1 > p80 or after 20 days
- reward now 0.208 (when found 0.386) | folds [0.67, 0.926, 1.637] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p70 (only if ret_1 < p90); sell when ibs < p70 or after 5 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.16
- curve: 1.14
- ret_5: 1.12
- vix_vs_50: 1.10
- rsi_2: 1.07
- wr_2: 1.07
- gap: 1.06
- dom_next: 1.05

## Features that tend to hurt

- down_streak: 0.90
- dist_sma_200: 0.89
- vix_ratio: 0.88
- rv_20_pct: 0.87
- credit_trend: 0.86

## Why ideas were rejected

- too few trades: 2310

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

50 rounds x 10 ideas with the evolution proposer in 1011s. Best deflated reward went from 0.248 to 0.314. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
