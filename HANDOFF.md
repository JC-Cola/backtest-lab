# Handoff: continue the backtest lab locally

Everything is in this repo (`JC-Cola/backtest-lab`, branch `main`). The cloud session's work is all committed.

## Set up on your Mac

```bash
git clone https://github.com/JC-Cola/backtest-lab && cd backtest-lab
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/pytest -q tests                  # all tests should pass
export ALPACA_API_KEY=...  ALPACA_SECRET_KEY=...   # your Alpaca PAPER keys (never commit them)
```

Price data is not in git. It downloads on first use: daily prices from Yahoo, and minute bars from Alpaca with
`cd src && ../.venv/bin/python intraday_data.py SPY QQQ IWM DIA`, which takes about 45 minutes and needs the keys.
Then open Claude Code in this folder (`claude`) and say: "read HANDOFF.md and carry on".

## Daily routine (after the US close, e.g. 21:30 UTC or later)

```bash
cd src && ../.venv/bin/python daily.py --rounds 30
```

This logs paper decisions, places Alpaca paper orders (they fill at the next open), runs the agent, and rebuilds
`report/lab.html`. Then commit `agent_memory/` and `report/lab.html`. The cloud scheduled task (00:23 UTC) still
exists; it fails whenever your usage limit is reached. Delete or pause it at claude.ai, Scheduled tasks, if you run
locally.

To schedule it on the Mac (New York time zone), put the paper keys in `~/.config/backtest-lab/alpaca.env` as two
`export` lines, then run `scripts/install_launchd.sh`. It runs the daily routine at 16:45 and the QQQ noise-band
checks hourly 10:01–15:01 plus the 15:47 flatten, on weekdays, and commits the updated files locally (push them
yourself). Logs go to `~/Library/Logs/backtest-lab/`. Remove it with `scripts/install_launchd.sh --remove`.

## Running in the cloud (GitHub Actions)

Two workflows in `.github/workflows/` replace the Mac's launchd jobs. Both share one concurrency group
(`backtest-lab-trading`), so they never run at the same time, and both can be started by hand from the Actions tab.

- **intraday** (`intraday.yml`): every 30 minutes on weekdays, 13:01-20:31 UTC (10:01-15:31 New York in both EST and
  EDT). Runs `live_noise.py --once` on the intraday account and commits the log, state and snapshot. GitHub may start
  a run 5-20 minutes late or twice; the script acts once per check point, sends the market-on-close order with the 15:00
  check, and closes any position left over from an earlier day.
- **nightly** (`nightly.yml`): 21:45 UTC on weekdays (16:45 EST / 17:45 EDT). Refreshes the minute data, runs
  `daily.py` (paper decisions, long-term account orders, both agents, Claude proposer rounds, Strategy Lab page), then
  `report.py`, commits `agent_memory/`, `report/lab.html` and `reports/`, and opens an issue "Trading report YYYY-MM-DD"
  labelled `daily-report`. The first run downloads about 10 years of minute bars (roughly 45 minutes); later runs reuse
  the cache.

Set the five secrets once (each command prompts for the value; never paste keys into files or chat):

```bash
gh secret set ALPACA_API_KEY -R JC-Cola/backtest-lab
gh secret set ALPACA_SECRET_KEY -R JC-Cola/backtest-lab
gh secret set ALPACA_INTRADAY_API_KEY -R JC-Cola/backtest-lab
gh secret set ALPACA_INTRADAY_SECRET_KEY -R JC-Cola/backtest-lab
gh secret set CLAUDE_CODE_OAUTH_TOKEN -R JC-Cola/backtest-lab
```

Make the Claude token with `claude setup-token` on the Mac (it prints a long-lived token for your subscription).

**Never run the Mac jobs and the cloud jobs together.** Once a manual run of each workflow works, remove the Mac
schedule with `scripts/install_launchd.sh --remove` (this also removes the break-even job, which the cloud does not run).

Where to look: the daily report is `reports/YYYY-MM-DD.md` (index in `reports/README.md`) and arrives as an issue;
the Strategy Lab page is `report/lab.html`; a failed run shows in the Actions tab. All of it is committed to `main`.

Actions minutes (private repo, free tier 2,000 minutes per month, Linux runners, each job rounded up to whole minutes):
intraday is 16 runs a day, about 1-2 minutes each, so roughly 350-700 a month; nightly at 15 rounds is about 30-40
minutes a day, so roughly 650-850 a month. Expect about 1,000-1,550 in total, plus a one-off 45-60 for the first
data download. That fits but with little room; lower `rounds` in the nightly workflow's inputs if the usage page
shows you drifting towards 2,000. GitHub also disables schedules after 60 days without repo activity (the commits prevent that).

## Where things stand (2026-11-03)

- **Daily strategies:** 486 tests, none passed. IBS held up on fresh data but earns less than holding.
- **Alpaca paper account:** holds SPY (IBS and Vol-Targeted Trend), QQQ (Williams %R and January Barometer) and
  BTC (Trend Filter). `src/alpaca_paper.py`; orders are logged in `agent_memory/alpaca_orders.csv`.
- **Intraday study** (`INTRADAY_PLAN.md`, results in `results/intraday.json`, code in `src/intraday.py`):
  - 8 strategies from papers, tuned on 2016–2020 and tested on 2021 onwards.
  - Only one candidate: the noise-band breakout on QQQ (Sharpe 1.42, p = 0.0005, q = 0.006, still positive
    at double costs). It **failed** the pre-registered IWM/DIA lockbox (median Sharpe −0.77).
  - Not confirmed.
- **Portfolios** (post-hoc idea, not pre-registered):
  - **A** (members positive on the tuning years: noise breakout on SPY and QQQ, plus ORB on QQQ): test Sharpe
    0.79, best FTMO pass rate 35% at 2× size. Its lockbox is not meaningful: on IWM the same selection rule
    picked a different strategy, and on DIA it picked none.
  - **B** (all 12): loses money.
- **FTMO 2-Step simulator** (`src/ftmo.py`): rules as found by search. The FTMO site was blocked from the cloud,
  so confirm the numbers on the site.
- **MT5:** `mt5/BacktestLab.mq5` (not compiled yet) and `mt5/README.md`. On a Mac, `mt5-quant` can compile and
  backtest it.

## Suggested next steps

1. Paper-trade portfolio A's noise breakout on QQQ forward (live data is the only clean test left), or test it
   on more markets nobody has looked at (for example XLK or SMH minute bars).
2. Keep the daily paper trading going and compare the results each month.
3. Before buying an FTMO challenge: the best simulated pass rate was about 1 in 3, at small size. The fee is a
   cost per attempt.
