# What the agent has learned so far

Updated 2026-10-18. 2600 trials in total: 1881 scored, 719 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.462 (when found 0.539) | folds [1.302, 0.337, 1.05] | QQQ: buy when dom_next > p30 and rsi_14 < p5 and rsi_2 < p50; sell when vix_vs_50 > p50 or after 5 days
- reward now 0.442 (when found 0.719) | folds [1.28, 0.337, 1.05] | QQQ: buy when dom_next > p30 and rsi_14 < p5 and rsi_2 < p45; sell when vix_vs_50 > p50 or after 5 days
- reward now 0.442 (when found 0.506) | folds [1.28, 0.337, 1.05] | QQQ: buy when dom_next > p30 and rsi_14 < p5 and rsi_2 < p45 (only if rsi_2 < p70); sell when vix_vs_50 > p50 or after 5 days
- reward now 0.391 (when found 0.399) | folds [1.266, 0.206, 1.05] | QQQ: buy when dom_next > p30 and rsi_14 < p5 and rsi_2 < p45; hold 1 days
- reward now 0.379 (when found 0.399) | folds [1.187, 0.407, 1.05] | QQQ: buy when dom_next > p30 and rsi_14 < p5 and rsi_2 < p40; sell when rsi_2 > p30 or after 5 days
- reward now 0.378 (when found 0.532) | folds [1.202, 0.337, 1.05] | QQQ: buy when dom_next > p30 and rsi_14 < p5 and rsi_2 < p40; sell when vix_vs_50 > p50 or after 5 days
- reward now 0.375 (when found 0.580) | folds [1.199, 0.337, 1.05] | QQQ: buy when dom_next > p30 and rsi_14 < p5; sell when vix_vs_50 > p50 or after 5 days
- reward now 0.375 (when found 0.419) | folds [1.199, 0.337, 1.05] | QQQ: buy when dom_next > p30 and rsi_14 < p5 and rsi_2 < p60; sell when vix_vs_50 > p50 or after 5 days
- reward now 0.375 (when found 0.375) | folds [1.199, 0.337, 1.05] | QQQ: buy when dom_next > p30 and rsi_14 < p5 (only if rsi_2 < p70); sell when vix_vs_50 > p50 or after 5 days
- reward now 0.357 (when found 0.444) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.28
- vix_vs_50: 1.12
- curve: 1.11
- to_fomc: 1.10
- rsi_2: 1.09
- dom_next: 1.09
- ibs: 1.07
- ret_20: 1.06

## Features that tend to hurt

- dist_sma_200: 0.81
- wr_2: 0.79
- ret_60: 0.79
- pre_holiday: 0.74
- credit_trend: 0.72

## Why ideas were rejected

- too few trades: 719

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

50 rounds x 10 ideas with the evolution proposer in 291s. Best deflated reward went from 0.415 to 0.462. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
