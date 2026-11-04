# Intraday study 7: the day-trader-skill rules (pre-registered)

Written and committed **before** any of this study's results were computed. The rules are taken as written in
github.com/Jakeschincariol/day-trader-skill (MIT): `trader-entry`, `trader-exit` and `trader-scanner` (read
2026-11-04). Their pack is long-only, and so is this test.

## Rules

**`id_dts_orb`: their opening-range breakout.** On 5-minute bars, the opening range is the high and low of the first
15 minutes (the 09:30, 09:35 and 09:40 bars). Entry: the first completed 5-minute bar after the range that CLOSES
above the range high, filled at the next minute's open; no entries before 09:45 or after 15:30. Stop: the range
midpoint (their default) or the range low; target: entry + R × (entry − stop). Stops and targets fill at their level,
or at a bar's open if it opens through them; if both are touched in one bar, the stop. Flat at 15:55 either way.
One trade a day. **Grid (4):** stop = midpoint or low; R = 1 or 2.

**`id_dts_orb_setup`: the same breakout, only on days their daily scanner flags.** Their defaults (midpoint stop,
2R target, flat 15:55), taken only when the previous session's daily closes show their setup:
`breakout_20d` (the last close above every close of the 20 sessions before it), `bull_flag` (up more than 10% over
the 10 sessions before the last 5, then the last 5 closes inside a 5% range), `reclaim_ma20` (the close before was
under its 20-day average, the last close above it) or `oversold_bounce` (RSI 14, Wilder, under 30 the session
before, and the last session closed up). **Grid (4):** one setup each.

Their scanner picks from about 5,900 US stocks; this test uses SPY and QQQ, so it tests the rules, not their stock
selection.

## Data, windows, costs, tests

The same as studies 1, 3-6: minute bars, tune on 2016–2020, test once on 2021–today, 1 bp per side (re-run at 2×),
coin-flip test with 2,000 random re-timings, Benjamini–Hochberg across this study's 4 tests.

- **Candidate:** p < 0.05, q < 0.10, Sharpe > 0 at 1× and 2× costs.
- **Confirmed:** fresh tuning on XLK, SMH, XLF and XLE (reserved by studies 3 and 5, never downloaded) gives Fisher's
  combined p < 0.05 and a median test Sharpe > 0.

Every result is reported and added to the Strategy Lab page if it made money in its test years.
