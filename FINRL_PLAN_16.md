# Study 16: FinRL deep-reinforcement-learning stock trading on the Dow 30 (pre-registered)

Written and committed **before** any model was trained and before any result of this study was computed.

## Question

Josh: "try finrl for our backtest lab". **Null:** a FinRL agent (PPO / A2C / SAC trained with the standard FinRL stock-trading
setup) does not beat plain buy-and-hold of SPY after costs on years it never saw, once the seed-to-seed spread of
reinforcement learning is taken into account.

## Install (facts, not results)

`finrl` 0.3.7 from PyPI, in its own environment `.venv-finrl` (Python 3.12.13; pandas 2.3.3, numpy 2.2.6, gymnasium 1.4.0,
stable-baselines3 2.9.2, torch 2.15.0 CPU, stockstats 0.6.13). The wheel declares no dependencies and `finrl/__init__.py`
imports the Alpaca paper-trading module (needs the unmaintained `alpaca_trade_api`). So `finrl` is imported **without its
`__init__`**: a stub package object points at the installed directory and the **original, unmodified** FinRL files
`env_stock_trading/env_stocktrading.py` (`StockTradingEnv`), `preprocessor/preprocessors.py` (`FeatureEngineer`: technical
indicators + turbulence) and `config.py` (`INDICATORS`) are used as shipped. Nothing is re-implemented.

## Data

Daily OHLCV from yfinance (split- and dividend-adjusted, `auto_adjust=True`; the repo's daily loader's source; Alpaca's free
history starts in 2016, too short), 2009-01-01 to the last completed session, cached in `data/finrl16/` (gitignored).
Universe = FinRL's `DOW_30_TICKER` **minus WBA** (delisted 2025, no data) **and DOW** (no history before 2019-03) = **28 stocks**:
AXP AMGN AAPL BA CAT CSCO CVX GS HD HON IBM INTC JNJ KO JPM MCD MMM MRK MSFT NKE PG TRV UNH CRM VZ V WMT DIS. The list is a
2021-era Dow, so it carries **survivorship / selection bias that flatters every row** (including the equal-weight benchmark);
the fair yardstick for the agent's stock selection is therefore the equal-weight buy-and-hold of the same 28, and SPY / DIA are
the "what could Josh simply have held" yardsticks. Dates where any stock lacks a bar are dropped (FinRL `clean_data`).

Features: FinRL defaults `INDICATORS` = macd, boll_ub, boll_lb, rsi_30, cci_30, dx_30, close_30_sma, close_60_sma (stockstats)
plus FinRL's turbulence index (`calculate_turbulence`, 252-day look-back, Mahalanobis distance of the day's returns). 2009 is
warm-up only.

## Environment and agents

`StockTradingEnv` exactly as in FinRL's stock-trading tutorial: `hmax = 100`, initial cash 1,000,000, start with no shares,
`reward_scaling = 1e-4`, state = cash + 28 closes + 28 holdings + 8 indicators x 28, actions in [-1, 1] per stock, **long-only
by construction** (no shorting, no leverage), buy/sell cost **0.1% per side** (FinRL default). Turbulence switch: training env
without it; validation/test env with `turbulence_threshold` = the **90th percentile of the turbulence index over the training
years** (FinRL ensemble convention; above it the env sells everything).
Stable-baselines3, FinRL's own default hyper-parameters (`finrl.config`): **A2C** (n_steps 5, ent_coef 0.01, lr 7e-4) 50,000
steps; **PPO** (n_steps 2048, ent_coef 0.01, lr 2.5e-4, batch 64) 200,000 steps; **SAC** (batch 64, buffer 100,000, lr 1e-4,
learning_starts 100, ent_coef auto_0.1) 70,000 steps (the tutorial's step counts). No hyper-parameter search: this is the
"standard setup", so the only choices made on data are the algorithm (below). **5 seeds** per algorithm: 0, 1, 2, 3, 4 (seed
sets numpy/torch/SB3). The policy acts deterministically at evaluation. As in FinRL, a decision at the close of day t is filled
at the close of day t (state shows that close); this is optimistic by up to a day and is stress-tested below.

## Periods (no overlap, no re-tuning)

- Train: 2010-01-01 to 2019-12-31.
- Validate: 2020-01-01 to 2021-12-31 — used **only** to pick the algorithm: the algorithm with the highest **median-over-seeds**
  validation Sharpe (1x cost) is "selected"; one algorithm, all 5 seeds, frozen before the test is read. Models are not
  retrained on the validation years.
- Test: 2022-01-01 to the last completed session, run once. Fresh 1,000,000 cash, empty book (the ramp-up cost is part of
  the result; the benchmarks are fully invested from day one with no cost, which favours them).
All three algorithms' test results are also reported (descriptive, labelled not-selected).

## Costs

Each trained model is evaluated on the test period at **1x = 0.1% per side** and **2x = 0.2% per side** (same policy, env cost
raised; the path can differ). Daily returns are the env's account-value changes (costs inside).

## Benchmarks (test years, same dates)

SPY buy-and-hold, DIA (DJIA ETF) buy-and-hold, equal-weight buy-and-hold of the same 28 stocks (initial equal weights, no
rebalancing, no costs). Sharpe = mean/std of daily returns x sqrt(252), no risk-free deduction (repo convention); also total
return, max drawdown.

## Tests and gates

- **Coin-flip (same exposure, same turnover):** for each model run, the day-by-day end-of-day weight vectors (stocks + cash) are
  taken from the run; 2,000 random relabellings of **which stock gets which weight** (one fixed random permutation of the 28
  names per simulation, applied to the whole path) keep the exposure path, the weight concentration and the traded volume
  (hence cost) identical, and ask whether the agent's choice of names beat random names. p = (1 + #sims with Sharpe >=
  the agent's) / 2,001, at 2x costs (cost series of the run is re-used).
- Benjamini-Hochberg q across the selected algorithm's 5 seeds. Deflated Sharpe of the median seed's test returns (2x), trials =
  the 15 model runs' validation daily Sharpes (context, not a gate).
- **Candidate (Josh's rule):** the selected algorithm's **median-seed** test Sharpe at **2x costs beats SPY buy-and-hold's test
  Sharpe AND all 5 seeds have positive test Sharpe at 2x costs.**
- **Supported (stricter, reported either way):** candidate AND median-seed coin-flip p < 0.05 AND the seed-level BH q < 0.10 for
  the median seed AND the median seed also beats the equal-weight benchmark's Sharpe.
- No lockbox: this is a universe study with no free parameters tuned on test data; if a candidate exists the next step would be
  a forward paper-test, not a lockbox.

## Descriptive readouts (no gates)

Per algorithm x seed: validation Sharpe, test Sharpe / total return / max DD / trades / final turnover at 1x and 2x; the spread
(min, median, max, std) across seeds; **lag-1 stress** on the selected algorithm: the recorded end-of-day weights are executed one
day late (hold the previous day's weights, costs on the weight change at the cost rate) — "what if the signal is not
fillable at the same close it was computed on"; yearly test Sharpe of the median seed; correlation of the median seed's daily
returns with SPY.

## Plain answer rule (stated now)

"FinRL works for us" only if the candidate rule is met and the lag-1 stress keeps median Sharpe above SPY's; a candidate that
is not supported (coin-flip) or only beats SPY at 1x is "not shown"; a result that needs the best seed is not a result. Any
outcome is reported. **No orders are placed by this study.**

## Deliverables

`src/finrl_data.py`, `src/finrl16.py` (+ the stub loader), `tests/test_finrl16.py` (stub loader, data-split dates, cost
accounting, weights/permutation null, benchmark maths), `results/finrl16.json` (per-seed daily returns at 1x and 2x for
quantstats, benchmarks, the tables above). Run in `.venv-finrl`.
