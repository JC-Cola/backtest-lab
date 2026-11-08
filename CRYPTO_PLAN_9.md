# Crypto study 9: trend, breakout and rotation rules on spot crypto (pre-registered)

Written and committed **before** any of this study's data was downloaded or any result computed. Rules come from
published work and practitioner lore found by a literature search (Zarattini, Pagani & Barbon 2025, "Catching Crypto
Trends"; Faber 2007; new-high and cross-sectional momentum folklore). The account is spot, **long or flat only** (Alpaca
crypto cannot short or use leverage); position sizes are between 0 and 100% of the account.

## Questions

1. Does any simple long-or-flat crypto rule beat random timing on 2023-today, after realistic fees?
2. Crypto's main benefit may be smaller drawdowns, not higher return. Does any rule cut the drawdown of holding the coin
   without giving up the return per unit of drawdown?
3. Should the live BTC Trend Filter (close > SMA(150), tuned in the daily study) be changed?

**Null:** none of the rules beats random timing with the same exposure and trade count.

## Data

- **Prices:** Coinbase Exchange public candles, daily (UTC, 00:00 close) and hourly, BTC-USD, ETH-USD, SOL-USD for
  tuning and test. Lockbox coins LTC-USD, BCH-USD, LINK-USD, AVAX-USD are **not downloaded until a candidate exists**.
  All seven are also on Alpaca, so tradable. Fallback if Coinbase is blocked: data.binance.vision spot klines (BTCUSDT
  etc.), and the report says so. The Alpaca crypto history starts in 2021 only, which is why it is not used.
- A missing daily (hourly) bar is filled with the previous close as a zero-return bar; the number filled is reported.
  The final bar is the last COMPLETE UTC day before the run.
- **Funding:** Binance USD-M perpetual funding rates, monthly files from data.binance.vision
  (`futures/um/monthly/fundingRate/`), months from 2020-01 up to the last complete month: BTCUSDT for BTC, ETHUSDT for
  ETH, SOLUSDT for SOL. Cached to `data/crypto/funding/`.
- Cache: `data/crypto/*.csv`; `data/crypto/` is added to `.gitignore` (price data is never committed).

## Windows, fills, costs

- **Tune:** 2016-01-01 to 2022-12-31. **Test:** 2023-01-01 to the last complete day (the post-spot-ETF regime question).
  Each coin is evaluated from the later of 2016-01-01 and 365 days after its first bar, so every lookback (at most 360
  days) is defined. SOL (first bar 2021) therefore has a short tuning window (months, not years); that is accepted and
  reported, and tuning there is weak evidence.
- **No lookahead:** a signal uses data up to and including the daily close of day t (00:00 UTC of t+1) and fills at the
  open of day t+1 (engine convention `engine.returns_np`, position s_t decided on close t, traded at open t+1). Hourly
  rules: decided on an hourly close, filled at the next hourly open.
- **Costs:** 0.2% per side (main) charged on every change of position, proportional to the size changed, and 0.5% per
  side (stress), re-run on the test years. Crypto trades every calendar day: annualisation uses 365 days (8,760 hours
  for hourly rules).
- Holdings are held at constant weights between decisions (implicitly rebalanced daily, no cost for drift).

## Strategies, exact rules and grids

Closes are UTC daily closes. `HHc(N, t)` is the highest close over days t-N to t-1 (the prior N days, excluding t).

**1. `c9_donchian`: Donchian multi-lookback ensemble (Zarattini, Pagani & Barbon 2025).** Eight legs, N in {5, 10, 20, 30,
60, 90, 180, 360}. Leg state: flat becomes long when close_t > HHc(N, t). While long, a ratcheting trailing stop starts at
mid_N(t) = (max + min of the last N closes, ending at t, inclusive) / 2 on the entry day and thereafter stop_t = max(stop_{t-1},
mid_N(t)); the leg exits when close_t < stop_{t-1} (checked before updating the stop; no re-entry on the exit day). Leg
weight = on_leg x min(1, target / sigma_t), sigma_t = stdev of the last 30 daily log returns x sqrt(365). Ensemble target
weight = the mean of the leg weights (equal weight, never above 100%). A leg with fewer than N+1 bars of history is flat
(its 1/8 stays in cash). The held position only moves to the target when |target - held| > 0.10, or when the target is 0
(full exit); this band stops daily vol-sizing noise from being charged as trading.
**Grid (6):** target volatility in {25%, 50%, 75%} x legs in {all eight, slow five (30, 60, 90, 180, 360)}.

**2. `c9_trend`: trend filter.** Long when close_t > SMA(n) of the closes ending at t, else cash, full size. This is
exactly `strategies_trend.trend_filter`, the family of the live BTC Trend Filter. **Grid (4):** n in {20, 50, 100, 200}.

**3. `c9_newhigh`: new-high breakout.** Signal on day t when close_t >= the maximum of the last N closes ending at t (a
new N-day closing high). **Variant `1d`:** long on day t+1 only (held one day; consecutive highs keep it long). **Variant
`level`:** enter at the next open after the first new high; the breakout level L0 is the maximum of the N closes before
the entry signal day; stay long while close_t >= L0, exit at the next open after the first close below L0 (a failed
breakout). Full size. **Grid (6):** N in {10, 20, 50} x hold in {`1d`, `level`}.

**4. `c9_rotation`: weekly cross-sectional rotation.** Universe BTC, ETH, SOL (each from the day it has enough history,
see Windows). Every Sunday close (00:00 UTC Monday) rank the available coins by their return over the last L weeks
(close_t / close_{t-7L} - 1); the top k each get a 1/k slot, but a slot goes to cash if that coin's close is not above its
own SMA(50). Fills at Monday's open; weights are held to the next Sunday. If fewer than k coins are available the remaining
slots are cash. **Grid (9):** L in {1, 2, 4} weeks x k in {1, 2, 3}. One market (the universe), one test.

**Filters (6 and 7), on top of 1 and 2 only, applied to each one's in-sample-tuned setting; not tuned and not part of
the candidate pool.**
- *Weekend timing:* a new entry from flat is not filled on a Saturday or Sunday (UTC); the entry waits for Monday's open
  if the signal is still on. Exits are never delayed.
- *Funding de-risk:* f_t = the mean of the Binance perpetual funding rate over the 7 days ending at day t (all 8-hourly
  settlements, averaged per day first). The flag is on when f_t is above the 90th percentile of the daily f over the
  trailing 365 days ending at day t. When on, the target weight is multiplied by 0.5 (variant "halve") or 0 (variant
  "zero"). The filter is **inactive** (no de-risk) until 365 days of f exist: that is about January 2021 for BTC/ETH
  (data used from 2020-01) and about September 2021 for SOL, and inactive for any day after the last downloaded month. It
  therefore only affects 2021 onwards: a small slice of the tuning window and the whole test window.
- *Verdict:* a filter "helps" if, on the test years at 0.2% costs, max drawdown is smaller (closer to zero) **and**
  Calmar is higher **and** Sharpe is at least the unfiltered Sharpe minus 0.05 (equal or better). Descriptive; no
  p-value and no multiple-comparison claim. The same readout is given at 0.5%.

**Negative controls (8, 9), pre-registered as expected to fail, BTC-USD only, hourly bars, no tuning (one fixed rule
each):**
- `c9_ctl_hour`: hold BTC only from 21:00 to 23:00 UTC every day (filled at the 21:00 bar's open, sold at the 23:00
  bar's open). Costs of 2 x 0.2% per day make it fail by construction; its job is to check that the engine does not
  pay for a meaningless rule.
- `c9_ctl_macd`: hourly MACD(12, 26, 9) crossover (MACD line crosses above its signal line on a completed hourly
  candle), taken only when the DAILY MACD(12, 26, 9) of the last completed UTC day is above its signal; filled at the
  next hourly open; exit at the open after the first red hourly candle (close < open) on or after the entry bar.
- A control "failing" means p >= 0.05 or Sharpe <= 0 at 0.2% costs. **If a control passes (p < 0.05, q < 0.10,
  Sharpe > 0), that is treated as a red flag for leakage or an engine bug, not as a discovery.**

## Tests and decision rules (the same as the daily study, `run.py`)

- **Tuning:** best Sharpe on the tuning window among grid settings with at least 10 trades (if none, among all). One test
  run of that setting on the test years. Grid-median test Sharpe and the Sharpe at 0.5% are reported.
- **Coin-flip test:** 2,000 random-timing traders with the same number of trades, the same holding durations (shuffled)
  placed at random in the test window (`stats.coin_flip`, fixed seed); trade value ignores position size, as in the
  existing studies. Rotation uses a random-pick baseline: each week a trader holds the same number of coins as the
  strategy, chosen at random from the available coins, with the same 1/k slot size and costs; statistic = total
  log return. p = (1 + #random >= strategy) / 2001. Strategies with exposure above 95% get no p (always invested).
- **BH q-values:** Benjamini-Hochberg across **all 12 tests** of this study: ideas 1 to 3 x BTC, ETH, SOL (9), rotation
  (1), and the two controls (2); including the controls makes the pool more conservative.
- **Deflated Sharpe:** the tuned setting's tuning-window returns against the grid's tuning-window daily Sharpes
  (`dsr_prob`, as in `run.py`) and its test returns against the same trials (as in study 8); reported, not a gate.
- **Candidate (PASS):** p < 0.05, q < 0.10, test Sharpe > 0 at 0.2% **and** at 0.5% costs (stricter than the existing
  "2x costs" test, which here would be 0.4%).
- **Confirmed (lockbox):** only if a candidate exists, download LTC-USD, BCH-USD, LINK-USD, AVAX-USD and re-run the
  identical procedure (fresh tuning, one test run, coin-flip) on each; confirmed if Fisher's combined p < 0.05 and the
  median test Sharpe > 0. A coin with no tuning-window bars after its 365-day warm-up (for example AVAX, first bar
  2021) is dropped and reported; a no-trade or always-invested run counts as p = 1. A rotation candidate is re-run on the
  universe LTC, BCH, LINK, AVAX (a single run, so Fisher's p is that run's p).
- **Buy-and-hold row** for each coin and the rotation universe (equal weight, rebalanced daily): test-year total
  return, CAGR, Sharpe, max drawdown, Calmar, with one entry cost.
- **Secondary "drawdown" readout (fixed now, reported whatever the candidate outcome):** a strategy is a
  **risk-reducer** on a market if, on the test years at 0.2% costs, its max drawdown is at most 60% of buy-and-hold's
  (in absolute size) **and** its Calmar (CAGR / |max drawdown|) is at least buy-and-hold's. Reported for the tuned setting
  of each strategy and also as "x of n grid settings" to show how robust it is, and at 0.5% as well. A risk-reducer is
  not a candidate and gets no lockbox unless it is also a candidate.
- **Live-filter comparison (descriptive only):** the live BTC Trend Filter, SMA(150), is run on the same BTC test
  years at 0.2% and 0.5% and shown next to the study's tuned trend and Donchian settings.

## How the answers are given

- **Worth paper trading:** a candidate that is confirmed on the lockbox. A risk-reducer without that is reported as a
  possible drawdown tool, with the caveat that it did not clear the return test. Anything else: not shown.
- **Change the live BTC Trend Filter?** Only if a confirmed candidate in the trend family beats the live SMA(150) on
  the BTC test years at 0.5% costs (Sharpe) without a deeper max drawdown. Otherwise leave it.
- Every result is reported and, if it made money in its test years, added to the Strategy Lab page (Long-term family).
