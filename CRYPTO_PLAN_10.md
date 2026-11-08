# Crypto study 10: weekend-entry skip on the crypto trend filter (confirmatory, pre-registered)

Written and committed **before** any of this study's data was downloaded or any result computed.

## Why this study exists

Study 9 noticed, descriptively and NOT pre-registered as a hypothesis, that "skip new entries on Saturday and Sunday"
improved the BTC/ETH/SOL trend filter (close > SMA) in the 2023+ test years. Those years and those three coins are now
SEEN for this question and cannot confirm it. This study tests it on data nobody has looked at for it.

## Hypothesis

**H1:** for the long-or-flat trend filter (long when the daily UTC close > SMA(n), else cash; the same definition as
study 9, `strategies_trend.trend_filter`), blocking NEW entries on weekends improves risk-adjusted results.
**Null:** no improvement.

**Weekend rule (kept exactly as study 9's `crypto9_base.execute(..., weekend=True)`):** the position s_t is decided on the
close of UTC day t and filled at the open of day t+1. A new entry from flat is NOT filled when the FILL day is a Saturday
or Sunday (UTC). In signal-day terms that is a signal on the Friday close or the Saturday close; a Sunday-close signal fills
on Monday and is allowed. A blocked entry is taken on the first later fill day that is not a weekend if the signal is
still on at that day's decision (checked every day, so the entry waits for Monday's open). An existing position is kept
over the weekend and exits normally on any day; exits are never delayed. (Wording note: the task brief said "signal day
Saturday or Sunday"; the binding definition is study 9's code, fill day Saturday/Sunday, as written above.)

## Fixed parameters, no tuning

- **n = 150 PRIMARY** (the live BTC filter). **n = 50 secondary** (descriptive; carries no gate).
- Costs **0.2% per side** (main) and **0.5% per side** (stress), charged on every change of position; fills at the next
  day's open (`engine.returns_np`); 365-day annualisation. Daily gaps are filled with the previous close, as in study 9.
- Each coin: full available Coinbase Exchange daily history, evaluated from bar index 150 (after a 150-day warm-up, the
  same start for n = 150 and n = 50 and for both versions). Returns are computed on the full series and then sliced, so
  both versions share one identical window.

## Markets (exact set, fixed now)

LTC-USD, BCH-USD, LINK-USD, AVAX-USD (the study-9 lockbox coins, never downloaded), plus XRP-USD, ADA-USD, DOGE-USD,
DOT-USD, XLM-USD, ATOM-USD. Ten coins; none appears in any earlier study's data or code except as the study-9 lockbox
names. Source: Coinbase Exchange public candles (fallback Binance spot klines, stated in the results if used).
**Inclusion rule:** a coin is excluded if, at download time, it has fewer than 3 full years (1,095 bars) AFTER the 150-day
warm-up, i.e. fewer than 1,245 daily bars; exclusions and the reason are listed in the results. No coin is added or
dropped for any other reason, and no result is looked at before the set is final.
**BTC-USD** (and nothing else of BTC/ETH/SOL) is also reported, **descriptive only** (seen), and is excluded from every
combined test and median.

## Primary statistic and tests

- Per coin, per n, per cost level: d_t = (skip daily return) - (no-skip daily return) on the shared window.
- **Paired stationary bootstrap** (Politis-Romano, mean block length 20 days, circular, 10,000 resamples, fixed seed per
  coin/n/cost) of the mean of d_t, centred under the null: one-sided p = (1 + #{resamples with mean* - mean_obs >=
  mean_obs}) / (10,000 + 1) for the alternative mean(d) > 0.
- Also reported for both versions: Sharpe, Calmar, max drawdown, total return, trades per year (and the Sharpe
  difference).
- **Combined test (PRIMARY, alpha 0.05):** Fisher's combined p across the included untouched coins for n = 150 at 0.2%
  costs (`run.fisher_p`). Reported descriptively (no gate): Fisher at 0.5% and for n = 50.
- **Sign test (descriptive):** the number of coins (of those included) whose Sharpe AND max drawdown (closer to zero) are
  both better with the skip, n = 150 at each cost, with a one-sided binomial p against 0.5. Cross-coin correlation is high,
  so Fisher's independence assumption is generous and the sign test is only a sanity read.

## Decision rule (stated now)

Adopt the weekend skip on the live BTC filter **only if all of**:
1. Fisher combined p < 0.05 for n = 150 at 0.2% costs, AND
2. the median (across included coins) Sharpe difference (skip minus no-skip) > 0 for n = 150 at 0.2% costs, AND
3. the median Sharpe difference > 0 for n = 150 at 0.5% costs.

Otherwise the live filter is left unchanged. BTC itself does not enter the decision.

## Forward check

If adopted: paper-trade the skip version side by side with the unchanged live BTC filter for at least 3 months before
switching any real money. No change to live trading comes from this study alone.

## Deliverables

`src/crypto10.py` (runner and helpers: weekend positions, stationary bootstrap), `results/crypto10.json`, tests for the
weekend rule and the bootstrap, a Strategy Lab entry (Long-term family) with display text, and a rebuilt
`report/lab.html`. Every result is reported whatever the outcome.
