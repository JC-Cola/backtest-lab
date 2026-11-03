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
`report/lab.html`. Then commit `agent_memory/` and `report/lab.html`. To schedule it on the Mac, use `cron` or
`launchd`. The cloud scheduled task (00:23 UTC) still exists; it fails whenever your usage limit is reached.
Delete or pause it at claude.ai, Scheduled tasks, if you run locally.

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
