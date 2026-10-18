# What the agent has learned so far

Updated 2026-10-18. 6100 trials in total: 4327 scored, 1773 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.484 (when found 0.675) | folds [1.281, 1.473, 0.829] | DIA: buy when ret_5 < p10 and wr_2 < p10 (only if curve < p60); sell when pre_holiday > p70 or after 3 days
- reward now 0.484 (when found 0.486) | folds [1.281, 1.473, 0.829] | DIA: buy when ret_1 < p30 and ret_5 < p10 and wr_2 < p10 (only if curve < p60); sell when pre_holiday > p70 or after 3 days
- reward now 0.418 (when found 0.429) | folds [1.256, 1.473, 0.729] | DIA: buy when ret_5 < p10 and wr_2 < p10 (only if curve < p60); hold 3 days
- reward now 0.405 (when found 0.648) | folds [1.282, 1.459, 0.683] | DIA: buy when dist_sma_200 > p5 and ret_5 < p10 and wr_2 < p10 (only if curve < p60); hold 5 days
- reward now 0.389 (when found 0.491) | folds [2.103, 1.002, 0.111] | SPY: buy when ibs < p10 and ret_20 < p40 and to_month_end < p90; sell when gap < p70 or after 5 days
- reward now 0.374 (when found 0.799) | folds [1.221, 1.459, 0.683] | DIA: buy when ret_5 < p10 and wr_2 < p10 (only if curve < p60); hold 5 days
- reward now 0.329 (when found 0.497) | folds [1.246, 1.459, 0.542] | DIA: buy when ret_5 < p10 and wr_2 < p10 (only if curve < p55); sell when vix_vs_50 < p10 or after 5 days
- reward now 0.329 (when found 0.481) | folds [1.246, 1.459, 0.542] | DIA: buy when ret_5 < p10 and to_fomc < p95 and wr_2 < p10 (only if curve < p55); sell when vix_vs_50 < p10 or after 5 days
- reward now 0.329 (when found 0.463) | folds [1.246, 1.459, 0.542] | DIA: buy when gap < p95 and ret_5 < p10 and wr_2 < p10 (only if curve < p55); sell when vix_vs_50 < p10 or after 5 days
- reward now 0.329 (when found 0.436) | folds [1.246, 1.459, 0.542] | DIA: buy when ret_5 < p10 and wr_2 < p10 and z_20 < p95 (only if curve < p55); sell when vix_vs_50 < p10 or after 5 days

## Features that tend to help (learned weights, 1 = average)

- curve: 1.19
- rsi_14: 1.19
- ret_5: 1.16
- vix_vs_50: 1.11
- wr_2: 1.09
- ibs: 1.07
- gap: 1.07
- dom_next: 1.07

## Features that tend to hurt

- down_streak: 0.90
- vix_ratio: 0.87
- dist_sma_200: 0.87
- rv_20_pct: 0.86
- credit_trend: 0.80

## Why ideas were rejected

- too few trades: 1773

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

50 rounds x 10 ideas with the evolution proposer in 813s. Best deflated reward went from 0.302 to 0.484. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
