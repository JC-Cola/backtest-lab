# What the agent has learned so far

Updated 2026-10-18. 1100 trials in total: 807 scored, 293 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.746 (when found 0.852) | folds [0.723, 0.926, 1.486] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p65 (only if ret_1 < p90); sell when ibs < p70 or after 5 days
- reward now 0.704 (when found 0.701) | folds [0.654, 0.904, 1.486] | GLD: buy when dom_next < p5 and range_rank_7 < p80 and vix_vs_50 < p65; sell when ibs < p70 or after 5 days
- reward now 0.422 (when found 0.573) | folds [0.383, 1.041, 0.668] | IWM: buy when ret_20 > p80 and rsi_2 < p50 and vix > p25 (only if range_rank_7 > p40); hold 5 days
- reward now 0.414 (when found 0.470) | folds [0.152, 1.053, 0.846] | IWM: buy when ret_20 > p80 and rsi_2 < p50 and vix > p30 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.413 (when found 0.482) | folds [0.335, 1.041, 0.701] | IWM: buy when ret_20 > p80 and rsi_2 < p50 and vix > p30 (only if range_rank_7 > p40); hold 5 days
- reward now 0.399 (when found 0.482) | folds [0.496, 0.739, 1.098] | GLD: buy when dom_next < p5 and vix_vs_50 < p65 and z_20 < p80 (only if ret_1 < p90); sell when ibs < p70 or after 5 days
- reward now 0.378 (when found 0.422) | folds [0.298, 0.855, 1.175] | GLD: buy when dom_next < p5 and vix_vs_50 < p65 (only if ret_1 < p90); sell when ibs < p70 or after 5 days
- reward now 0.368 (when found 0.502) | folds [0.396, 1.041, 0.469] | IWM: buy when ret_20 > p80 and rsi_2 < p50 and vix > p10 (only if range_rank_7 > p40); hold 5 days
- reward now 0.359 (when found 0.416) | folds [0.496, 0.655, 1.098] | GLD: buy when dom_next < p5 and vix_vs_50 < p65 and z_20 < p80; sell when ibs < p70 or after 5 days
- reward now 0.352 (when found 0.654) | folds [0.35, 1.041, 0.469] | IWM: buy when ret_20 > p80 and rsi_2 < p50 (only if range_rank_7 > p40); hold 5 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.27
- curve: 1.16
- to_fomc: 1.10
- dom_next: 1.10
- vix_vs_50: 1.09
- ibs: 1.09
- rsi_2: 1.06
- ret_1: 1.05

## Features that tend to hurt

- wr_10: 0.74
- dist_sma_200: 0.73
- ret_60: 0.64
- pre_holiday: 0.62
- credit_trend: 0.60

## Why ideas were rejected

- too few trades: 293

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

40 rounds x 10 ideas with the evolution proposer in 161s. Best deflated reward went from 0.636 to 0.746. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
