# Intraday study 2: many stocks and futures-tracking ETFs (pre-registered)

Written and committed **before** any of this study's data was downloaded.

## Universe (fixed now)

- **Discovery (15):** AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, JPM, XOM, UNH, V, HD, GLD (gold), TLT (bonds), USO (oil).
- **Lockbox (15, untouched until a candidate exists):** AVGO, COST, LLY, WMT, BAC, CVX, PG, KO, MA, ADBE, NFLX, AMD, SLV (silver), IEF (bonds), UNG (natural gas).
- **Futures:** no futures minute data is available for free (Alpaca has none; Yahoo keeps 60 days), so the gold,
  bond and energy ETFs stand in for those futures.

## Data and windows

Alpaca SIP minute bars, adjusted, regular hours, **2019–today**. That's shorter than study 1, so 30 symbols can be
downloaded in reasonable time. Tune on 2019–2021, test once on 2022–today.

## Strategies

- **From study 1, unchanged rules and grids:** intraday momentum, opening range breakout, VWAP trend, noise-band
  breakout, gap fade, half-hour periodicity.
- **New, from papers:**
  - `id_orb_in_play`: Zarattini, Barbon & Aziz (2024), *A profitable day trading strategy for the US equity market:
    stocks in play*. A 5-minute opening range breakout, taken only on days when the first 5 minutes' volume is
    above k × its 14-day average (k = 1.5, 2 or 3). The stop is the other end of the range; exit at 10R or the
    close (6 combinations).
  - `id_halfhour_reversal`: Heston, Korajczyk & Sadka (2010) report short-term *reversal* across recent half
    hours. After a 30-minute move larger than 0.3% or 0.5%, trade against it for the next 30 or 60 minutes
    (4 combinations). Not traded in the first or last half hour.

## Costs and tests

The same as study 1:
- **Costs:** 1 bp per side (stocks this liquid typically cost less than that), with a re-run at 2×.
- **Coin-flip test:** 2,000 random re-timings.
- **Multiple testing:** Benjamini–Hochberg across all of this study's tests, 8 strategies × 15 discovery symbols.
- **Candidate:** p < 0.05, q < 0.10, and Sharpe > 0 at both 1× and 2× costs.
- **Confirmed:** the same strategy, with fresh tuning, on all 15 lockbox symbols gives Fisher's combined
  p < 0.05 and a median Sharpe > 0.
