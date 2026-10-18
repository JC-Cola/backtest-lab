# What the agent has learned so far

Updated 2026-10-18. 5600 trials in total: 3962 scored, 1638 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.302 (when found 0.314) | folds [2.136, 0.562, 0.024] | SPY: buy when ibs < p10 and ret_20 < p40 and ret_250 < p90; sell when gap < p70 or after 5 days
- reward now 0.279 (when found 0.316) | folds [0.283, 2.075, 2.134] | BTC-USD: buy when curve < p70 and gap > p40 and rsi_14 > p75 (only if ret_250 > p16); sell when ret_60 > p95 or after 3 days
- reward now 0.261 (when found 0.374) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.261 (when found 0.328) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days
- reward now 0.261 (when found 0.303) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.258 (when found 0.262) | folds [0.654, 0.332, 0.893] | DIA: buy when ret_5 < p10 and rsi_2 < p10 (only if curve < p90); hold 5 days
- reward now 0.242 (when found 0.248) | folds [2.039, 0.414, 0.119] | SPY: buy when ibs < p10 and ret_20 < p30; sell when gap < p70 or after 5 days
- reward now 0.242 (when found 0.299) | folds [2.023, 0.483, 0.05] | SPY: buy when ibs < p10 and ret_20 < p30 and ret_250 < p90; sell when gap < p70 or after 5 days
- reward now 0.241 (when found 0.444) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.241 (when found 0.380) | folds [0.181, 1.204, 1.141] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p60 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.21
- vix_vs_50: 1.13
- curve: 1.10
- ibs: 1.08
- dom_next: 1.08
- rsi_2: 1.08
- gap: 1.06
- to_fomc: 1.06

## Features that tend to hurt

- rv_20_pct: 0.87
- wr_2: 0.86
- dist_sma_200: 0.85
- credit_trend: 0.81
- pre_holiday: 0.81

## Why ideas were rejected

- too few trades: 1638

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

50 rounds x 10 ideas with the evolution proposer in 688s. Best deflated reward went from 0.267 to 0.302. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
