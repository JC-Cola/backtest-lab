# What the agent has learned so far

Updated 2026-10-18. 6600 trials in total: 4687 scored, 1913 rejected, 0 sent to the held-out test, 0 passed it.

## Best ideas on the search years (deflated by every trial run)

- reward now 0.379 (when found 0.438) | folds [1.275, 1.711, 1.123] | DIA: buy when dow_next < p85 and ret_5 < p10 and wr_2 < p20 (only if curve < p60); sell when pre_holiday > p70 or after 3 days
- reward now 0.378 (when found 0.425) | folds [1.379, 1.703, 0.985] | DIA: buy when dow_next < p95 and ret_5 < p10 and wr_2 < p25 (only if curve < p55); sell when pre_holiday > p70 or after 3 days
- reward now 0.370 (when found 0.529) | folds [1.288, 1.614, 1.188] | DIA: buy when dow_next < p95 and ret_5 < p10 and wr_2 < p25 (only if curve < p60); sell when pre_holiday > p70 or after 3 days
- reward now 0.370 (when found 0.473) | folds [1.288, 1.614, 1.188] | DIA: buy when dow_next < p85 and ret_5 < p10 and wr_2 < p25 (only if curve < p60); sell when pre_holiday > p70 or after 3 days
- reward now 0.370 (when found 0.457) | folds [1.288, 1.614, 1.188] | DIA: buy when dow_next < p90 and ret_5 < p10 and wr_2 < p25 (only if curve < p60); sell when pre_holiday > p70 or after 3 days
- reward now 0.276 (when found 0.411) | folds [1.292, 1.521, 1.06] | DIA: buy when dow_next < p95 and ret_5 < p10 and wr_2 < p25 (only if curve < p60); sell when vix < p50 or after 3 days
- reward now 0.250 (when found 0.374) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p40); sell when wr_10 > p70 or after 5 days
- reward now 0.250 (when found 0.328) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p30); sell when wr_10 > p70 or after 5 days
- reward now 0.250 (when found 0.303) | folds [0.305, 1.234, 0.983] | IWM: buy when ret_20 > p80 and ret_250 < p40 and rsi_2 < p65 (only if range_rank_7 > p35); sell when wr_10 > p70 or after 5 days
- reward now 0.247 (when found 0.316) | folds [0.283, 2.075, 2.134] | BTC-USD: buy when curve < p70 and gap > p40 and rsi_14 > p75 (only if ret_250 > p16); sell when ret_60 > p95 or after 3 days

## Features that tend to help (learned weights, 1 = average)

- rsi_14: 1.18
- curve: 1.17
- ret_5: 1.15
- vix_vs_50: 1.11
- wr_2: 1.09
- gap: 1.07
- ibs: 1.07
- dom_next: 1.06

## Features that tend to hurt

- ret_60: 0.90
- dist_sma_200: 0.88
- vix_ratio: 0.87
- rv_20_pct: 0.86
- credit_trend: 0.81

## Why ideas were rejected

- too few trades: 1913

## Held-out tests (one shot each; BH-corrected together with the 486 earlier hunt tests on the same years)


## Last session

50 rounds x 10 ideas with the evolution proposer in 871s. Best deflated reward went from 0.484 to 0.379. Held-out shots this session: 0. Strategies passing the held-out test (BH across all 0 shots ever): 0.
