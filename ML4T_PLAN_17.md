# Study 17: Machine Learning for Trading techniques applied to each live system (pre-registered)

Written and committed **before** any model of this study was trained and before any result of this study was computed.

## Question

Josh: "apply the machine learning for trading to all of our systems, including intraday, System C and our new one, System B
[qlib]". Source: Stefan Jansen, *Machine Learning for Algorithmic Trading* (github.com/stefan-jansen/machine-learning-for-trading):
meta-labeling and purged / embargoed cross-validation (Lopez de Prado methods, ch. 7/11/12 and the `MultipleTimeSeriesCV`
splitter), Alphalens factor evaluation (information coefficient, quantile returns, turnover, IC decay). The three parts are
independent and each has its own null.

## (a) Intraday noise band: meta-labeling filter (SPY, QQQ; lockbox IWM, DIA)

**Null:** a gradient-boosting filter that vetoes some noise-band entries does not improve the plain rule on test years after
costs (a random veto with the same retention does as well).

**Primary rule (unchanged):** `intraday.noise_breakout(lookback = 14, check = 60)` (the live schedule), 1 bp per side inside
the trade returns, and the repo's day/trade conventions (`src/intraday.py`, minute caches of `src/intraday_data.py`). A
**trade** is one run of the plain rule (entry at the OPEN of bar entry_m, exit when the rule changes side/goes flat or at the
close). The plain rule's decision for that run was taken on the CLOSE of bar `entry_m - 1`.

**Meta-label (López de Prado):** y = 1 if the trade's net return (1x cost) is > 0, else 0, using only prices from the entry
fill onward (the exit is the rule's own, determined by later bars; nothing before the entry enters the label). Secondary label
(descriptive only, same pipeline): y_close = 1 if direction x (session close / entry price - 1) - 2 bp > 0 ("profitable by the
close").

**Features** (all known at the close of bar entry_m - 1; signed features multiplied by the trade direction): time of day
(minutes since 09:30); distance beyond the band in sigma = (price/edge - 1)/sigma(m) for the breached band edge; distance to
VWAP; 5-, 15- and 60-minute returns (look-back clipped at the open); the day's range so far (high - low)/open; the prior day's
VIX close (`data/ctx_VIX.csv`, last date strictly before the session); the opening gap (open/prev close - 1); the prior day's
return; the direction itself.

**Model:** LightGBM binary classifier, **fixed** hyper-parameters (num_leaves 7, n_estimators 150, learning_rate 0.03,
min_child_samples 50, subsample 0.8, colsample_bytree 0.8, seed 0, no tuning). Training rows pool SPY and QQQ trades.

**Tuning (2016-01-01 to 2020-12-31 only) with purged / embargoed walk of the days:** 5 contiguous day-blocks (the
`PurgedKFold` / `MultipleTimeSeriesCV` idea), each block predicted by a model trained on the other blocks with **5 sessions
purged on each side of the test block** (labels end the same day so purging is conservative; features look back at most 14
sessions). The out-of-fold P of every tuning trade is used to pick the threshold t from {0 (= no filter), 0.45, 0.48, 0.50,
0.52, 0.54, 0.56, 0.58, 0.60}: the t with the best Sharpe at **2x costs** of the filtered, equal-weight SPY+QQQ daily-return
series, subject to keeping >= 25% of the trades (t = 0 allowed). The final model is then refit on all of 2016-2020 and frozen.

**Test (2021-01-01 to the last completed session), run once:** score each test trade with the frozen model, take it only when
P > t, otherwise flat. Per market, at 1x and 2x costs (2x = one more 1 bp-per-side round trip per trade taken): Sharpe, total
return, max drawdown (daily-return equity), trades, win rate, worst day, share of trades kept, plus the plain rule alongside,
the OOF AUC (tuning) and the AUC on test trades, and feature importances (gain).

**Coin-flip for a filter:** 2,000 random vetoes of the plain test trades keeping exactly as many as the filter kept; p =
(1 + #sims with 2x Sharpe >= the filter's) / 2,001. Benjamini-Hochberg q across the two markets. Deflated Sharpe of the
filtered test returns against the 9 thresholds' tuning Sharpes (context).

**Candidate:** on **both** SPY and QQQ: filtered 2x test Sharpe > plain 2x test Sharpe AND filtered max drawdown no worse
(|dd| <= plain's) AND filtered 2x Sharpe > 0 AND coin-flip p < 0.05 and q < 0.10. If the tuned t is 0 there is no filter and the
null stands.
**Lockbox (IWM and DIA; minute bars downloaded only if a candidate exists):** the whole procedure (pooled IWM+DIA training,
the same grid, the same fixed model, fresh tuning on 2016-2020, one test run on 2021+) run unchanged. **Confirmed** if on both
markets the filtered 2x Sharpe beats the plain 2x Sharpe with no worse max drawdown and Fisher's combined coin-flip p < 0.05.

## (b) System C (TradingAgents): information-coefficient diagnostic, no trading conclusion

Inputs: `agent_memory/system_ta.json` decisions and `results/system_ta_backtest.json` decisions (the two sets are merged,
de-duplicated by symbol and date; the backtest's own contamination split at its stated cutoff is kept: "clean" = after
2026-03-31). Rating score: Buy +2, Overweight +1, Hold 0, Underweight -1, Sell -2. Prices: the repo daily caches
(`data/SPY.csv`, `QQQ.csv`, `NVDA.csv`, or yfinance for other symbols).
Forward return for horizon h in {1, 5, 20} trading days: open of the next session after the decision date to the close of
session decision-date + h (the system's own fill rule); Alphalens-style close-to-close variant also reported. Decisions whose
horizon is not yet realised are excluded and counted. Reported for all / clean / possibly-contaminated sets: n, Spearman IC
(pooled over symbol-dates), Pearson IC, one-sided permutation p (10,000 shuffles), hit rate among non-Hold decisions with a
binomial p, mean forward return by rating. **Decisions needed for significance:** n = ((1.645 + 0.842)/atanh(IC))^2 + 3 (one
sided alpha 0.05, power 80%) at the observed IC and at the fixed reference ICs 0.02, 0.05, 0.10. Overlapping horizons and the
tiny n are stated; no threshold or verdict on trading.

## (c) qlib (System B): ML4T factor evaluation of its predictions

Waits for `results/qlib15.json` from the parallel study (daily scores per instrument and date). The evaluator is written and
unit-tested now against synthetic data; if the file has scores when this study finishes it is run unchanged, otherwise the
status is "waiting on qlib15". Metrics (Alphalens-style): daily cross-sectional Spearman rank IC, its mean, std, ICIR
(mean/std), t-stat, share of days > 0; quantile analysis with 5 quantiles by daily score (mean next-day forward return per
quantile, top-minus-bottom spread, monotonicity), top-quantile daily turnover (share of names replaced) and the quantile-1
mirror, IC decay at lags 1..10 days (score at t vs close-to-close return from t+k-1 to t+k), all computed on the file's own
out-of-sample dates only. Descriptive; no gate.

## Tests (all pass before any run)

Purged CV (no training day within 5 sessions of the test block; blocks partition the days; every day is predicted exactly
once); meta-label construction (labels do not change when pre-entry bars are perturbed; features do not change when
post-entry bars are perturbed); IC / quantile / turnover / decay computation on synthetic panels with known answers; rating
scoring and the sample-size formula.

## Plain answer rule (stated now)

"ML4T improves the live noise band" only if the candidate rule holds on SPY and QQQ AND the lockbox confirms; a filter that wins
on one market, only at 1x costs, or without beating a random veto is "not shown". (b) and (c) are diagnostics, not trading
advice. Any outcome is reported. **No orders are placed by this study.**

## Deliverables

`src/ml4t_cv.py` (purged CV), `src/ml4t_meta.py` (meta-labeling), `src/ml4t_ic.py` (IC / factor evaluation), `src/ml4t17.py`
(runner), `tests/test_ml4t17.py`, `results/ml4t17.json`.
