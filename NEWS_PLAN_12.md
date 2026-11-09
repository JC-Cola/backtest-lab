# Study 12: does trading big news moves work? (pre-registered)

Written and committed **before** any news was downloaded and before any result of this study was computed.

## Question

Josh asked whether trading on news works (he mentioned shorting NVDA on bad news). Test it on years of real news and
prices for large-cap stocks. **Null:** after a large price move that coincides with news, entering at the next open
(momentum or reversal, long or short) does no better than entering on random dates, after costs.

## Data

- **News:** Alpaca News API (Benzinga feed), `GET https://data.alpaca.markets/v1beta1/news?symbols=SYM&start=..&end=..&limit=50&sort=asc&include_content=false`,
  paged with `next_page_token`, from 2015-12-01 to the download date; cached to `data/news/<SYM>.jsonl` (gitignored),
  de-duplicated by article id. An article's time is its `created_at` (first publication), never `updated_at`.
- **Prices:** daily OHLC the repo already uses (`src/data.py`, Yahoo/yfinance, `auto_adjust=True`, split and dividend
  adjusted). Fills use the Open, shock detection uses the Close.
- **Universe (tune + test):** NVDA, AAPL, MSFT, AMZN, GOOGL, META, TSLA, AMD, NFLX, AVGO.
  **Lockbox (untouched, never looked at until a candidate exists):** CRM, ORCL, ADBE, INTC, QCOM.
  NVDA is also reported alone, descriptively (it is inside the 10 for every test; it is not a separate test).

## Events (decided at the close of day t, no look-ahead)

For stock S and trading day t (day t of S's own price series):
- `ret_t` = Close_t / Close_{t-1} - 1.
- `vol_t` = sample standard deviation (ddof = 1) of the 60 daily close-to-close returns ending on day **t-1** (day t's
  own return is NOT in it). Days with fewer than 60 prior returns are not eligible.
- **Shock day:** |ret_t| >= k x vol_t, k in {2, 3}. **Negative** shock if ret_t < 0, **positive** if ret_t > 0.
- **News window for day t:** every article for S with 16:00 US/Eastern of the previous trading day (the previous row of
  S's price series, so a Monday includes the weekend) **< created_at <= 16:00 US/Eastern of day t** (exclusive at the
  start, inclusive at the end). Standard 16:00 is used on early-close days too. After-hours articles after 16:00 on day
  t belong to day t+1's window and are not used for day t's decision.
- **News shock day (scope "all"):** a shock day with **at least 1** article in the window. The move has to coincide
  with news.
- **Earnings subset (scope "earn"):** a news shock day where at least one window article matches the earnings rule: the
  regex `\bearnings\b|\bEPS\b|\bquarterly (results|report)\b|\b(Q[1-4]|first|second|third|fourth)[- ]quarter\b` (case
  insensitive) on its headline or summary, or a stored `tags`/`channels` field containing "earnings" if the feed
  returns one. This is best effort and the matching rule is fixed here.
- **Control (descriptive only, no gate, no p):** shock days with **zero** articles in the window ("no-news shocks"),
  traded by the same rules, to see whether the news adds anything.
- The Benzinga feed tags many list-style articles to several tickers, so "at least 1 article" is a weak filter. The
  share of shock days that have news is reported, so we can see how much the news condition filters.

## Rules (each is one direction; entry at the NEXT open, t+1)

- (a) after a negative shock: **short** (momentum). Long-only version: stay out, i.e. no trade.
- (b) after a negative shock: **long** (reversal).
- (c) after a positive shock: **long** (momentum).
- (d) after a positive shock: **short** (reversal).
- Long-only family = rules b and c. Short-allowed adds a and d. Both-direction combinations (descriptive extension of
  the same tests): **MOM** = a + c (long-only version: c), **REV** = b + d (long-only version: b).

**Hold h in {1, 3, 5, 20} trading days:** enter at Open_{t+1}, exit at Open_{t+1+h}. An event whose exit open is not
yet available (end of data) is dropped. **No overlap:** within a stock, an event decided while a position is still open
(its entry day is before the open position's exit day) is skipped; the entry on the exit day itself is allowed.
Everything below uses these taken trades ("events" in the readout = taken trades).

## Costs

0.05% per side main, 0.10% per side stress (a round trip is two sides, charged on entry and exit). Shorts also pay
borrow of 0.5% a year, i.e. 0.005 x h/252 per short trade. A trade's net return: long = Open_exit/Open_entry - 1 - 2c;
short = -(Open_exit/Open_entry - 1) - 2c - borrow.

## Tested strategies and the grid

Strategy = (type in {a, b, c, d, MOM, REV}) x (scope in {all, earn}) = **12 strategies**, each with a grid of
(k, h) = 2 x 4 = **8 parameter sets**. All 12 x 8 = **96 tests** are coin-flipped and form the Benjamini-Hochberg
family. Tuning picks one (k, h) per strategy; the other 88 are reported.

## Periods

- **Tune:** signals on 2016-01-01 to 2021-12-31, only trades fully inside that window (entry and exit by 2021-12-31).
- **Test (once, no re-tuning):** signals from 2022-01-01 to the end of the data; the exit must be inside the data.

## Statistics (same machinery as studies 1-11: `stats.py`, `run.fisher_p`)

For every test in the test years: the number of events, the **event-level mean net return** (bp), the hit rate, the
strategy's **daily return series** and its Sharpe at 1x and 2x costs.
- **Daily series:** equal sleeves, each stock weighted 1/10 (lockbox 1/5), open-to-open: a position held from Open_d to
  Open_{d+1} earns that stock's open-to-open return (negated for shorts) on day d; costs on the entry and exit days;
  borrow spread over the days held; idle capital earns 0. Sharpe = mean/std (ddof 1) x sqrt(252).
- **Coin-flip test (2,000 sims):** per stock, the same number of trades with the same holding period and the same
  long/short mix at random non-overlapping entry dates inside that stock's test window; the value of a draw is the sum
  of net returns of all trades pooled over the 10 stocks. p = (1 + #sims >= strategy)/(1 + 2000). The random entries
  carry the same market drift, so a short that only loses in a bull market (or a long that only wins in one) does not
  count as skill.
- **Benjamini-Hochberg q** over all 96 coin-flip p's.
- **Deflated Sharpe** (Bailey & Lopez de Prado) of the tuned parameter set's test returns, against the 8 tuning-year
  Sharpes of that strategy as trials. Reported for context, not a gate.
- **Tuning:** best tuning-year Sharpe (1x costs) with at least 30 tuning trades; if none qualifies, the best overall.
- **Candidate** (as in studies 1, 3-11): the tuned parameter set with coin-flip p < 0.05, q < 0.10, test Sharpe > 0 at
  1x **and** at 2x costs, and a positive mean net event return at 1x.
- **Lockbox (run only if a candidate exists):** each candidate's tuned (type, scope, k, h) is frozen and run, with no
  re-tuning, on CRM, ORCL, ADBE, INTC, QCOM over **all years from 2016** (those stocks were never used for tuning, so
  every year is fresh). Per-stock coin-flip p (a stock with no trades counts p = 1), combined with Fisher's method.
  **Confirmed** if Fisher p < 0.05 and the pooled Sharpe > 0 and the pooled mean net event return > 0.

## Descriptive readouts (no gates)

- **Events per year**, 2016 to date: per k and scope, positive and negative shocks, total and per stock, plus shock
  days without news and the share of shock days with news.
- **Main-rule table:** for every rule x h at k = 2 and k = 3, scope "all", test years: events, mean net event return,
  hit rate, Sharpe (1x), p, q. Also 2x-cost Sharpe.
- **NVDA alone:** per rule x h (k = 2, 3, scope all), event counts, mean net return and hit rate, tune and test years
  separately and the full period, with a descriptive coin-flip p (not part of the BH family). Highlight rule (a), short
  NVDA after a negative news shock.
- **No-news control** for each rule x h at k = 2, 3 in the test years.

## Plain answer rule (stated now)

"News trading works" only if a candidate exists, is **confirmed** on the lockbox, and still has a positive mean event
return at 2x costs. Anything less, including a pass in the tuning years that dies in the test years, is "not shown",
and no live change comes from this study. NVDA's own events are too few to settle anything alone; the pooled tests
decide. Every result is reported whatever the outcome. Josh decides before any trading change.

## Deliverables

`src/news_data.py` (download + cache, reusable), `src/news12.py`, `results/news12.json` (same style as the other
studies, so it can go on the Strategy Lab, Long-term family), tests for: event detection using only data up to day t,
news-window boundaries, fill at the next open, short P&L and borrow cost. The lab template and `build_lab`/`lab_text`
are integrated separately.
