# Intraday study 3: seven strategies not tested before (pre-registered)

Written and committed **before** any of this study's results were computed. The minute bars for SPY and QQQ were
downloaded again for this study, but no study-3 rule has been run on them.

## Question

Do any of seven further published or popular intraday rules (none of them tested in studies 1 or 2) beat
random timing after costs, on data they were not tuned on?

## Data, windows, execution, costs

The same as study 1 (`INTRADAY_PLAN.md`): Alpaca SIP minute bars, adjusted, regular hours, 2016 to today;
tune on 2016–2020, test once on 2021–today; signals on a bar's close fill at the next bar's open; every
position is flat by the close; 1 bp per side, re-run at 2×.

- **Discovery markets:** SPY, QQQ.
- **Lockbox markets (fresh: never used in any study, not downloaded until a candidate exists):** XLK
  (technology), SMH (semiconductors), XLF (financials), XLE (energy).

## Strategies (grids fixed now)

| id | Source | Rule | Grid |
|---|---|---|---|
| `id_vol_breakout` | Larry Williams, *Long-Term Secrets to Short-Term Trading* (1999) | Bands at today's open ± k × yesterday's high–low range. The first bar that closes beyond a band is traded in that direction from the next bar to the close. One trade a day. | k = 0.25, 0.5, 0.75; long-short or long-only (6) |
| `id_prev_day_breakout` | Practitioner (prior-day high/low breakout) | The first bar that closes above yesterday's high goes long, below yesterday's low goes short, to the close. Optional stop at the broken level. | checks start 09:30 or 10:00; stop none or at the level (4) |
| `id_vwap_reversion` | Practitioner (VWAP mean reversion) | From 10:00 to 15:30, when the close is more than thr away from the session VWAP, trade back towards it. Exit when a bar closes on the other side of VWAP, or at the close. Re-entry allowed. | thr = 0.3%, 0.5%, 0.8%; exit at VWAP or at the close (6) |
| `id_bollinger_5m` | John Bollinger, *Bollinger on Bollinger Bands* (2001) | On 5-minute closes, a close below the lower band (mean − k sd of the last n closes) goes long, above the upper band goes short. Exit when a close crosses the middle band, or at the close. | n = 20, 40; k = 2, 2.5 (4) |
| `id_donchian` | Richard Donchian / Turtle channel breakout, on minutes | A close above the high of the previous N minutes goes long, below their low goes short (stop and reverse). Flat at the close. | N = 30, 60, 120 (3) |
| `id_late_momentum` | Baltussen, Da, Lammers & Martens (2021), *Hedging demand and market intraday momentum*, JFE | The sign of the return from yesterday's close (or today's open) to 15:00 (or 15:30), traded from then to the close. | reference close or open; signal to 15:00 or 15:30 (4) |
| `id_fomc_drift` | Lucca & Moench (2015), *The pre-FOMC announcement drift*, Journal of Finance | On FOMC announcement days only (`data/fomc_dates.csv`), long from 09:30 (or 10:00) to 14:00. | entry 09:30 or 10:00 (2) |

The FOMC rule trades about 8 days a year, so the tuning window has fewer than 50 trades; as in study 1, the best
setting is then picked from all of them. Its coin-flip test only moves the entry minute within FOMC days, so it
tests the timing, not whether FOMC days are special. It is also compared with buy-and-hold.

## Tests and decision rule

The same as study 1: best tuning-year Sharpe (at least 50 trades where possible); one run on 2021–today; the
coin-flip test with 2,000 random re-timings; Benjamini–Hochberg q-values across this study's 14 tests.

- **Candidate:** p < 0.05, q < 0.10, and Sharpe > 0 at both 1× and 2× costs.
- **Confirmed:** the same strategy, with fresh tuning on XLK, SMH, XLF and XLE, gives Fisher's combined p < 0.05
  and a median test Sharpe > 0.

Every result is reported, whatever it is, and added to the Strategy Lab page.
