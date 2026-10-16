# What the agent has learned so far

Updated 2026-10-16. 700 trials in total: 509 scored, 191 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.636 (when found 0.636) | folds [0.333, 0.747, 1.129] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p65; hold 2 days
- reward now 0.539 (when found 0.654) | folds [0.35, 1.041, 0.469] | IWM: buy when ret_20 > p80 and rsi_2 < p50 (only if range_rank_7 > p40); hold 5 days
- reward now 0.504 (when found 0.545) | folds [0.316, 1.041, 0.376] | IWM: buy when ret_20 > p80 and rsi_2 < p50 and vix > p20 (only if range_rank_7 > p40); hold 5 days
- reward now 0.501 (when found 0.663) | folds [0.35, 0.992, 0.462] | IWM: buy when gap < p85 and ret_20 > p80 and rsi_2 < p50 (only if range_rank_7 > p40); hold 5 days
- reward now 0.378 (when found 0.610) | folds [0.516, 0.276, 0.828] | GLD: buy when dom_next < p5 and vix_vs_50 < p70; sell when vix_vs_50 > p80 or after 3 days
- reward now 0.364 (when found 0.368) | folds [0.299, 0.802, 0.465] | IWM: buy when ret_20 > p80 and rsi_2 < p45 and vix > p20 (only if range_rank_7 > p40); hold 5 days
- reward now 0.362 (when found 0.530) | folds [0.059, 0.538, 1.0] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p65; sell when vix_vs_50 > p80 or after 3 days
- reward now 0.358 (when found 0.396) | folds [0.352, 0.717, 0.575] | IWM: buy when gap < p85 and ret_20 > p80 and rsi_2 < p40 (only if range_rank_7 > p40); hold 5 days
- reward now 0.349 (when found 0.374) | folds [0.073, 0.562, 1.237] | QQQ: buy when ret_20 < p20 and vix_vs_50 > p80 and z_20 < p5 (only if ret_5 < p15); sell when z_20 > p95 or after 20 days
- reward now 0.346 (when found 0.364) | folds [0.073, 0.562, 1.247] | QQQ: buy when ret_20 < p20 and vix_vs_50 > p80 and z_20 < p5 (only if ret_5 < p10); sell when z_20 > p95 or after 20 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.33
- curve: 1.16
- to_fomc: 1.14
- rsi_2: 1.11
- ret_20: 1.07
- range_rank_7: 1.06
- gap: 1.04
- vix_vs_50: 1.04

## Features that tend to hurt

- wr_10: 0.65
- ret_1: 0.62
- pre_holiday: 0.59
- ret_60: 0.58
- credit_trend: 0.56

## Why ideas were rejected

- too few trades: 191

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

20 rounds x 10 ideas with the evolution proposer in 97s. Best deflated reward went from 0.636 to 0.636. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
