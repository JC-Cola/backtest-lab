# Intraday study (holding periods from 1 minute up to 1 day): pre-registered plan

Written and committed **before** any intraday result was computed.

Amendment 1, also before any result: the user asked for no strategy reused from earlier batches, so the 5-minute RSI(2) rule (a Connors variant already tested on daily bars) is replaced by the half-hour periodicity strategy. The noise-band rule now follows the paper's gap-adjusted bands and VWAP condition.

## Question

Do any published or popular intraday strategies (no position held longer than one day) beat random
timing after costs, on data they were not tuned on?

## Data

Alpaca historical minute bars, full-market SIP feed, split- and dividend-adjusted, regular trading hours
(09:30–16:00 New York), January 2016 to today.

- **Discovery markets:** SPY, QQQ.
- **Lockbox markets (not looked at until a candidate exists):** IWM, DIA.
- **Windows:** tune on 2016–2020, test once on 2021–today. No re-tuning.

## Execution and costs

- A signal on the close of a minute bar is filled at the **open of the next bar**.
- Stops fill at the stop price. If a bar opens through the stop, the fill is that bar's open instead.
- Every position is closed by the last bar of the session, except the overnight strategy, whose whole design
  is to hold from the close to the next open (under one day).
- Costs: **1 bp per side** (2 bp per round trip), re-run at 2× (4 bp per round trip).

## Strategies (grids of 12 combinations or fewer, fixed now)

| id | Source | Rule |
|---|---|---|
| `id_overnight` | Cliff, Cooper & Gulen (2008); Lou, Polk & Skouras (2019) | Buy at the close, sell at the next open. |
| `id_intraday_only` | same papers (the contrast) | Buy at the open, sell at the close. |
| `id_intraday_momentum` | Gao, Han, Li & Zhou (2018), *Market intraday momentum* | Sign of the return from the previous close to 10:00 (or to 10:30), traded in the last 30 or 60 minutes. Long or short. |
| `id_orb` | Zarattini, Barbon & Aziz (2023), *A profitable day trading strategy for the US equity market* | Opening range of 5, 15 or 30 minutes: trade in the direction of that range's candle, with the stop at the range's opposite end. Target 10R, or hold to the close. |
| `id_vwap_trend` | Zarattini & Aziz (2023), *VWAP: the holy grail for day trading* | On 1-, 5- or 15-minute bars, long above the session VWAP and short below it, flipping on each cross. Flat at the close. |
| `id_noise_breakout` | Zarattini, Aziz & Barbon (2024), *Beat the market: an effective intraday momentum strategy for SPY* | Noise bands: upper = max(today's open, previous close) × (1 + σ), lower = min(open, previous close) × (1 − σ). σ is the average absolute move from the open at that minute over the last 10 or 14 days. At each check (every 30 or 60 minutes), go long above max(upper band, VWAP), short below min(lower band, VWAP), otherwise flat. Flat at the close. |
| `id_gap_fade` | Practitioner gap-fill folklore | If the open gaps more than 0.25%, 0.5% or 1% from the previous close, fade it from the 09:31 bar. Exit when the gap fills, or at the close. |
| `id_halfhour_periodicity` | Heston, Korajczyk & Sadka (2010), *Intraday patterns in the cross-section of stock returns* (adapted to one fund) | For each of the 13 half-hours of the session, hold it long (or short) when that same half-hour's average return over the last 10, 20 or 40 days was positive (negative). Long-short, or long-only. |

## Tests and decision rule

For each strategy × discovery market:
1. Pick the settings with the best Sharpe on 2016–2020, needing at least 50 trades.
2. Run those settings once on 2021–today.
3. **Coin-flip test:** 2,000 random strategies with the same days, the same number of trades, the same holding
   times and the same long/short mix. Only the minute of entry is moved, uniformly within the session.
   p = share at least as good. The overnight and intraday-only strategies have fixed timing, so they get no
   coin-flip test. They are compared with buy-and-hold instead.
4. Benjamini–Hochberg q-values across all intraday tests.

A **candidate** needs p < 0.05, q < 0.10, a test-window Sharpe > 0 and a Sharpe > 0 at 2× costs.

A candidate is **confirmed** only if the same procedure, run with fresh tuning on the lockbox markets IWM
and DIA, gives Fisher's combined coin-flip p < 0.05 and a median test-window Sharpe > 0.

Results are reported for every strategy, whatever the outcome.
