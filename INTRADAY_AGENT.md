# Intraday self-learning agent: methods change of 2026-11-23

Code: `src/iagent/` (`loop.py`, `memory.py`, `shots.py`, `platform.py`, `universe.py`, `bars5.py`, `features.py`, `spec.py`,
`proposer.py`), run by `src/research.py` every 30 minutes. Journal `agent_memory/intraday/journal.jsonl`, lessons
`agent_memory/intraday/lessons.md`, universe `agent_memory/intraday/universe.json`.

## Why the old gate was dead
Held-out test required a deflated Sharpe >= 0.95, and the deflation counts every trial ever run. After ~27.6k trials
(26,100 evolution, 1,181 Claude, 315 web; none ever reached the held-out years) every idea showed "reward now 0.000" even with
fold Sharpes of 1.2 to 2.8 on all three search folds. The deflated Sharpe stays near 0 from here on, so it is **reported for
information only** (`dsr_now`, lessons) and no longer gates anything. Multiple-testing control is kept, but moved to where it
can still work: the held-out shots (below).

The learned feature weights were also stuck at 1.00: `feature_weights()` averaged the stored deflated reward, which is 0 for
99% of trials, so every feature had the same mean and `exp(4 * 0) = 1`. It now learns from the median search-fold excess Sharpe
(`IMemory.signal`), which still varies, and is clipped to [0.2, 5].

## Budgeted held-out protocol (`shots.py`)
* Held-out years (2021+) are untouched until a shot is spent.
* At most **K = 3 shots per calendar month** (UTC), counted from the `ts` stored in each spent shot.
* Each shot goes to the best not-yet-tested idea by **robustness** on the search years: 0 unless all 3 folds have positive
  excess Sharpe at 1x costs and at 2x costs and there are at least 150 trades; else min(median fold Sharpe at 1x, median fold
  Sharpe at 2x). Old journal records have no 2x folds, so the best ones are re-scored on the search years first (`robust_eval`,
  written back to the journal).
* **Similarity rule** (no near-duplicate shots): same hash, or same market/side/time/exit/stop/target and the same
  (feature, op) conditions with every percentile within +-5 of a tested idea.
* A pass needs all of: Benjamini-Hochberg q < 0.10 across **all shots ever spent plus the earlier hunt tests on the same
  years**, Sharpe > 0 at 1x and 2x costs, beating the live noise band at 2x costs (SPY/QQQ ideas) or SPY buy-and-hold on the
  same window (single-stock ideas), and the **lockbox confirmation** below. The deflated Sharpe is shown, not used.
* Candidates still never trade by themselves: `agent_memory/candidates.json` lists passes as "awaiting approval" (or "not better
  than what is traded now"); only Josh's hand-set `approved` status counts, and the briefing flow is unchanged.

## Universe (`universe.py`, `bars5.py`)
SPY, QQQ plus the 40 most-traded liquid US stocks/ETFs (median close x volume over 250 sessions, price >= $5), picked with the
vendored day-trader-skill scanner's Yahoo readers (spark stage, then v8 daily charts; `third_party/` untouched) from the S&P 500 +
Nasdaq-100 list of `src/universe.py` plus 17 liquid ETFs. Refreshed every 30 days. About 20% (8 names) are drawn at random as the
**lockbox**: never searched, sticky (a lockbox name stays one; a searched name can never become one). A shot's rule is replayed
on every lockbox name's held-out years (percentiles from that name's own search years) at 2x costs: it must have >= 3 names with
>= 20 trades, a positive median Sharpe and a majority positive; an unreadable lockbox never confirms. Cross-sectional "top-k names"
specs were not added: specs stay single-name (one trade a day per name).
Multiple testing counts every scored trial on every name (`IMemory.trial_sharpes`).

Data: SPY/QQQ keep 1-minute bars. Other names use 5-minute SIP bars (Alpaca, `data/intraday/5m/<SYM>.pkl.gz`, about 2.7 MB per
name, ~110 MB for 40) expanded onto the 390-minute grid (fills, close-of-bar features and cumulative highs/lows are exact at 5-minute
boundaries; stops see each 5-minute bar's full range). Each 30-minute run searches SPY, QQQ and a rotating 6 other names to bound
memory (~0.3 GB each) and download time (first use of a name downloads ~10 years, roughly 30 s, then loads in ~4 s).

## New setup features (scanner families, appended after the old 11 so old spec hashes hold)
`orb5/orb15/orb30` (opening-range breakout distance), `run_10d`, `range_5d`, `consol_30`, `hi20_dist` (bull flag / 20-day
breakout), `rsi_14d`, `z_20d` (oversold bounce), `gap_fill` (gap-and-go / fade), `rvol_30` (relative-volume spike). All use bars before
the decision close and daily closes before today; tests perturb the future and check nothing changes.
