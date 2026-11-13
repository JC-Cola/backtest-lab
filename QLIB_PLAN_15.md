# Study 15: Microsoft qlib, Alpha158 features + LightGBM + top-k dropout portfolio on US stocks (pre-registered)

Written and committed **before** any qlib data was built and before any result of this study was computed.

## Question

Josh replaced "System B" (day-trader-skill, now a shadow ledger) on the dedicated Alpaca paper account with **qlib**
(https://github.com/microsoft/qlib, `pyqlib` 0.9.7): the standard `workflow_config_lightgbm_Alpha158` model and the
`TopkDropoutStrategy` (topk 50, n_drop 5), rebalanced daily, on today's S&P 500. **Null:** the model's daily top-50 portfolio,
after realistic costs, earns no better risk-adjusted return than buying SPY, and no better than random top-50 portfolios that
churn the same number of names. This study decides whether qlib is labelled a **candidate**; the live paper system runs either way
(Josh's decision) and is judged by its own results.

## Data

- **Universe:** the 517 symbols in `src/universe_fallback.txt` (current S&P 500 + Nasdaq-100 constituents, Alpaca-tradable
  and shortable). **Survivorship bias:** these are today's members, so companies that were dropped or died are missing and
  past index membership is not reproduced. Any stock-picking result, and the equal-weight benchmark, is biased UP by this. It is
  reported, not corrected.
- **Prices:** Yahoo Finance via `yfinance`, daily, `auto_adjust=True` (split and dividend adjusted open/high/low/close, split-adjusted
  volume), 2008-01-02 to the last completed session; SPY alongside as the benchmark (not part of the universe). Built into
  qlib's own binary format (`calendars/day.txt`, `instruments/*.txt`, `features/<sym>/<field>.day.bin`) under `data/qlib/`
  (gitignored). `$factor` = 1 because prices are already adjusted. qlib's hosted US data is stale and is not used.
- A symbol enters the universe on its first available day and leaves on its last. Symbols Yahoo cannot deliver are listed in the
  output, not replaced.

## Model (qlib defaults, nothing tuned)

- Features: `Alpha158` (158 factors), inference processors `RobustZScoreNorm` (fitted on the train years only, clip outliers) and
  `Fillna`; learn processors `DropnaLabel`, `CSRankNorm` on the label.
- **Label:** `Ref($open,-2)/Ref($open,-1) - 1`: the return from the next open (where the order fills) to the open after it. This is
  the one change from the benchmark config (which uses `close`), to match "signal after the close, fill at the next open".
- Model: `LGBModel` with the benchmark's hyper-parameters unchanged (mse, colsample_bytree 0.8879, learning_rate 0.2,
  subsample 0.8789, lambda_l1 205.6999, lambda_l2 580.9768, max_depth 8, num_leaves 210, early stopping 50 rounds on the
  validation segment, max 1000 rounds, seed 0).
- Strategy: `TopkDropoutStrategy(topk=50, n_drop=5, method_sell="bottom", method_buy="top", hold_thresh=1)`, equal-cash buys, daily.
  Signal of day t is traded at the **open of day t+1** (`deal_price="open"`), positions are marked at the close. Long-only, no
  leverage, no price limits, whole shares on a $1,000,000 account.

## Periods

- **Train 2010-01-01 to 2019-12-31, validation 2020-01-01 to 2021-12-31** (early stopping only), **test 2022-01-01 to the last
  completed session, run once.** No parameter, label or universe change after seeing test numbers.
- **Primary (P):** one model trained on the train years, used unchanged for the whole test (qlib's own benchmark protocol).
- **Supplementary (W), live-like:** the model is refit each calendar quarter on the trailing 8 years (validation = the last 6
  months before the refit, 2-day gap) and applied to the next quarter. W is reported next to P; the candidate rule below uses P.

## Costs

Main **0.05% per side** (`open_cost = close_cost = 0.0005`), **stress 0.10% per side** ("2x costs"); no commission, no borrow
(long-only). Each cost level is a separate qlib backtest.

## Benchmarks

- **SPY buy-and-hold** over the same test days (adjusted close to close).
- **Equal-weight universe:** daily-rebalanced equal weight of every universe member with data that day, no costs (upward biased,
  see survivorship).
- **Coin-flip:** 1,000 random top-50 portfolios that start with 50 random names and every day swap `m` random held names for `m`
  random non-held names, where `m` is the model portfolio's mean daily number of replaced names (fractional part drawn at random);
  same 0.05% per side on the swapped notional, same open-fill accounting. p = share of the 1,000 whose test Sharpe is at least the
  model's (with +1 smoothing). This tests whether the ranking carries information beyond holding 50 churned stocks.

## Statistics (test period, per calendar year and pooled)

Annual return (CAGR, net), Sharpe (mean/std of daily net returns x sqrt(252), zero risk-free rate), maximum drawdown (compounded),
annual one-way turnover (sum of daily buys and sells / 2 / equity, x252), mean daily IC and rank IC (Pearson and Spearman between
the score and the label, per day, then averaged), ICIR (mean/std x sqrt(252)). The same for validation years as context. Years:
2022, 2023, 2024, 2025 full; the current year is partial and flagged.

## Candidate rule (stated now)

**Candidate** = (P at 0.10% per side) pooled test **Sharpe > SPY's pooled test Sharpe** over the same days **and** a positive
pooled net return. **Strong candidate** = candidate **and** coin-flip p < 0.05 at 0.05% **and** the strategy's Sharpe beats SPY's in
at least 3 of the 4 full test years at 0.10%. Anything less is "not a candidate" and is reported as such, with the live paper
account treated as an out-of-sample forward test regardless.

## Outputs

`results/qlib15.json` (config, data coverage, per-year and pooled tables for P and W at both cost levels, benchmarks, IC, coin-flip,
verdict, and `daily_returns`: date to net return at 0.05% for P, W, SPY and the equal-weight universe) and
`results/qlib15_returns.csv` (date, qlib, qlib_w, spy, equal_weight) for tear sheets.

## Plain answer rule (stated now)

The answer is the pooled test table at 0.10% per side against SPY and the coin-flip p, in plain words, whichever way it goes. A
negative result is reported as negative; the pre-registered rule is not softened after the fact.
