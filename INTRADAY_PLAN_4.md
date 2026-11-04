# Intraday study 4: seven more strategies not tested before (pre-registered)

Written and committed **before** any of this study's results were computed. It reuses the SPY and QQQ minute
bars already downloaded; no study-4 rule has been run on them.

## Question

Do any of seven further intraday rules (none tested in studies 1–3, and none a minute-bar copy of a daily rule
already tested) beat random timing after costs, on data they were not tuned on?

## Data, windows, execution, costs

The same as study 1 (`INTRADAY_PLAN.md`): Alpaca SIP minute bars, adjusted, regular hours, 2016 to today; tune
on 2016–2020, test once on 2021–today; signals on a bar's close fill at the next bar's open; stops and targets
fill at their level (or at the bar's open if it opens through them); flat by the close; 1 bp per side, re-run at 2×.

- **Discovery markets:** SPY, QQQ.
- **Lockbox markets (fresh: never used in any study, not downloaded until a candidate exists):** XLV (health
  care), XLY (consumer discretionary), XLI (industrials), XLP (consumer staples).

## Strategies (grids fixed now)

| id | Source | Rule | Grid |
|---|---|---|---|
| `id_gap_go` | Practitioner "gap and go" (the opposite of study 1's gap fade) | If the open gaps more than thr from yesterday's close and the first N minutes close in the gap's direction, trade with the gap from minute N to the close. Stop: the gap fully fills (yesterday's close). | thr = 0.25%, 0.5%, 1%; N = 5, 15 (6) |
| `id_pivot_revert` | Floor-trader pivot points (practitioner) | From yesterday's high, low and close: P = (H+L+C)/3, R1 = 2P−L, S1 = 2P−H, R2 = P+(H−L), S2 = P−(H−L), R3 = H+2(P−L), S3 = L−2(H−P). The first touch of R1 (or R2) is shorted, of S1 (or S2) bought, from the next bar. Stop at the next level out; target P or the close. Skipped if the day opens beyond the level. | level 1 or 2; target pivot or close (4) |
| `id_first_hour_reversal` | Practitioner (morning overreaction fade) | If 09:30–10:30 moves more than thr, trade against it from 10:30, for 2 hours or to the close. | thr = 0.25%, 0.5%, 1%; hold 2 hours or to the close (6) |
| `id_time_of_day` | Heston, Korajczyk & Sadka (2010) seasonality; practitioner "closing ramp" | Long every day during one fixed window only. | 09:30–10:00, 10:00–12:00, 12:00–14:00, 14:00–15:30, last 30 min, last 60 min (6) |
| `id_volume_spike_fade` | Practitioner (liquidity-shock reversal) | A 1-minute bar with volume above k × the previous 30 bars' average and a move above 0.1% is faded for the next 15 or 30 minutes (from 10:00, one trade at a time). | k = 5, 10; hold 15 or 30 min (4) |
| `id_crabel_stretch` | Toby Crabel, *Day Trading with Short Term Price Patterns and Opening Range Breakout* (1990) | stretch = 10-day average of min(high − open, open − low). The first close beyond open ± m × stretch is traded to the close. | m = 1, 1.5, 2; long-short or long-only (6) |
| `id_lunch_breakout` | Practitioner (midday range breakout) | The high and low of a midday window; the first close beyond it afterwards is traded to the close, with an optional stop at the range's other end. | window 11:30–13:00 or 12:00–13:30; stop none or other end (4) |

## Tests and decision rule

The same as studies 1 and 3: best tuning-year Sharpe (at least 50 trades where possible); one run on 2021–today;
the coin-flip test with 2,000 random re-timings; Benjamini–Hochberg q-values across this study's 14 tests.

- **Candidate:** p < 0.05, q < 0.10, and Sharpe > 0 at both 1× and 2× costs.
- **Confirmed:** the same strategy, with fresh tuning on XLV, XLY, XLI and XLP, gives Fisher's combined p < 0.05
  and a median test Sharpe > 0.

`id_time_of_day` has fixed timing within its window, so its coin-flip test asks whether that window beats random
windows of the same length on the same days, which is exactly the claim being tested.

Every result is reported, whatever it is, and added to the Strategy Lab page.
