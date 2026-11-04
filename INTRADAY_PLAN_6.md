# Intraday study 6: stop-loss and take-profit ratios on the noise-band breakout (pre-registered)

Written and committed **before** any of this study's results were computed.

## Question

Does adding a stop-loss and a take-profit at a fixed reward-to-risk ratio improve the noise-band breakout, the only
intraday rule that ever looked promising (study 1)? A ratio alone cannot create an edge from random entries (the win
rate falls to about 1/(1+R)), so the test is whether it improves entries that may have one.

## Rule

- **Entries:** the noise-band breakout exactly as tuned in study 1 for each market (`results/intraday.json`,
  id_noise_breakout: lookback and check interval). Its own exits still apply (signal turns flat or flips, flat at
  the close).
- **Overlay on every trade:** stop = entry price ∓ m × ATR, where ATR = the average daily high–low range over the
  previous 14 sessions as a fraction of price; take-profit = entry ± R × the stop distance. Stops and targets fill at
  their level, or at a bar's open if it opens through them (never on the entry bar's open). After a stop or target
  exit the trade is over; the next trade starts only when the base rule opens a new one.
- **Grid (6):** m = 0.25 or 0.5; R = 2, 3 or 4.

## Data, windows, costs, tests

SPY and QQQ minute bars; tune m and R on 2016–2020, test once on 2021–today; 1 bp per side, re-run at 2×; coin-flip
test with 2,000 random re-timings; Benjamini–Hochberg across this study's 2 tests.

- **Candidate:** p < 0.05, q < 0.10, Sharpe > 0 at 1× and 2× costs, **and** a test-years Sharpe at 2× costs higher
  than the plain noise-band breakout's on the same market (the "only if better" rule).
- **Confirmed:** with fresh tuning on XLV, XLY, XLI and XLP (reserved by study 4, never downloaded), Fisher's combined
  p < 0.05 and a median test Sharpe > 0.

A confirmed result would be proposed for the intraday paper account only after the user approves it.
