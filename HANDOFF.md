# Handoff: backtest lab (state 2026-11-09)

Repo `JC-Cola/backtest-lab`, branch `main`. Everything runs in the cloud; the Mac is only for development. There is
no launchd and no Mac job (do not run `scripts/install_launchd.sh` or `scripts/local_run.sh`, see Obsolete below).

## What runs where

Timers live in **Supabase pg_cron** (project `tumnfmmnelsfswyowdqh`, jobs `bl-*`, e.g. `bl-intraday`, `bl-nightly`,
`bl-research`). Each job calls `private.gh_dispatch()`, which starts a GitHub workflow (`workflow_dispatch`) with the
token in Vault. GitHub's own cron dropped runs, so it is not used for trading; only the watchdog uses it.

| Workflow | When (UTC, weekdays unless noted) | What it does |
|---|---|---|
| `intraday.yml` | :01 and :31, 13:01-20:31; plus :56 at 19 and 20 (the 15:56 New York close) | `live_noise.py --once`: noise-band breakout on SPY + QQQ, 50% of equity each, hourly checks 10:00-15:00 NY, flat with a plain market order at 15:56. Commits log/state, sends fill alerts |
| `morning.yml` | 13:15 and 14:15 (self-gated: sends only 09:00-09:40 NY) | Telegram briefing card before the open |
| `nightly.yml` | 21:45 | Minute-data refresh, Strategy Lab build, news shadow test, fill alerts, flat guard, live-vs-backtest reconciliation, daily report (commit + issue), evening briefing |
| `research.yml` | every 30 min, all days | Self-learning agents test new ideas (needs the Claude token). Never trades |
| `system_ta.yml` | 21:15 | System C (TradingAgents on Gemini), one Telegram summary |
| `system_dts.yml` | not scheduled yet | System B (day-trader-skill); the intended cron is in the file header |
| `watchdog.yml` | GitHub cron `*/20 13-22 * * 1-5` (+ manual) | Dead-man's switch, see Alerts |

Concurrency: trading workflows share group `backtest-lab-trading`, so they never overlap. All of them can be run by
hand from the Actions tab.

## Accounts (Alpaca paper, $25k each; paper only)

- **Intraday**: the only account with the live strategy (`ALPACA_INTRADAY_*`). Risk limits in `live_noise.py`: daily
  loss stop -3%, kill switch at -12% from the peak (re-arm by running `intraday.yml` with `rearm = true`).
- **Long-term**: retired 2026-11-09 (deleted). No daily-strategy orders any more. Market data uses
  `src/alpaca_keys.py data_keys()`: first working of intraday, DTS, TA keys.
- **System B** (day-trader-skill, `ALPACA_DTS_*`) and **System C** (TradingAgents on free Gemini, `ALPACA_TA_*`):
  own paper accounts, shown next to the intraday account on the briefing card. B is not scheduled yet.

## Secrets (names only; never put values in files, chat or logs)

- GitHub Actions secrets: `ALPACA_INTRADAY_API_KEY/_SECRET_KEY`, `ALPACA_DTS_API_KEY/_SECRET_KEY`,
  `ALPACA_TA_API_KEY/_SECRET_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `CLAUDE_CODE_OAUTH_TOKEN`,
  `GOOGLE_API_KEY`, `BACKTEST_LIVE_URL/_KEY/_TOKEN`. Set with `gh secret set NAME -R JC-Cola/backtest-lab`.
- Supabase Vault: `github_dispatch_token` (GitHub token used to dispatch; expires about Nov 2027), `alpaca_id_key/secret`
  (the live page's read access to the intraday account). `alpaca_lt_*` are obsolete (long-term account is gone).
- Local: `~/.config/backtest-lab/alpaca.env` (`export` lines; outside the repo). The legacy `ALPACA_API_KEY/_SECRET`
  pair is the retired long-term account.

## Alerts (Telegram)

- **Fill alerts**: every fill on the three accounts (`fill_alerts.py`, in intraday and nightly runs).
- **Failure alerts**: each workflow sends one message if a step fails. They live inside the workflows, so they cannot
  fire when the dispatch itself dies; that is what the watchdog is for.
- **Flat guard** (`flat_guard.py`, nightly): the intraday account must be flat after the close.
- **Watchdog** (`watchdog.py`, GitHub's own cron every 20 min, independent of Supabase): alerts when no `intraday` run
  in 45 min (10:05-16:05 NY), no `morning` run by 09:45, no `nightly` run by 17:15 NY (at least 30 min after its
  dispatch), no `research` run in 90 min, or the last two intraday/nightly runs both failed. One alert per problem per 3 h;
  state in `agent_memory/watchdog.json`, committed only after an alert. GitHub may drop a tick; the next one covers it.
- **Reconciliation** (`reconcile.py`, nightly): compares the day's live decisions and fills with the backtest's
  (SIP data, same code). Output `agent_memory/reconcile.json`, one line on the evening card, a Telegram alert on any
  decision mismatch. Agreed bar before real money: 3+ months of paper trading where live tracks the backtest and is up.
- **Proposer health**: the morning card warns when the research agents' Claude login is refused (401).

## Studies 1-14 (plans `*_PLAN_*.md`, results in `results/*.json`)

Nothing confirmed. The daily-strategy hunt (486 tests) and its confirmation found no edge that beats holding.

1. Intraday, 8 published rules on SPY/QQQ: noise-band breakout on QQQ best (Sharpe 1.42 on 2021+), failed the IWM/DIA lockbox.
2. 30 stocks/ETFs: no candidate; gap-fade follow-up not confirmed on 12 fresh stocks (Fisher p 0.20).
3. Seven more rules (lockbox XLK/SMH/XLF/XLE): no candidate.
4. Seven more rules (lockbox XLV/XLY/XLI/XLP): no candidate.
5. Darvas box on minute bars: no candidate.
6. Stop/target ratios on the noise band: QQQ candidate, lockbox not confirmed (p 0.73).
7. day-trader-skill opening-range rules: no candidate (beat 0 of 2,000 random traders).
8. Check frequency of the noise band: hourly stays.
9. Crypto trend, breakout, rotation: no candidate.
10. Weekend-entry skip on the crypto trend filter: fails on 10 untouched coins (p 0.58).
11. Vol-targeted sizing and VIX regime filter on the noise band: no improvement; live rule unchanged.
12. Trading news shocks (daily holds): not shown to work; big-move momentum missed the lockbox (p 0.058).
13. Short the open after bad news, cover in minutes: fails; short the morning after a 3x shock is a thin confirmed candidate.
14. Short the open after bad news, hold to the close: **running** (no result file yet).

Rule: pre-register each study (plan committed alone, before any data or result). The nightly news shadow test and
the research agents never trade.

## Strategy Lab page

`report/lab.html` is rebuilt by the nightly run (`src/daily.py`, template `report/lab_template.html`, builder
`src/build_lab.py`) and committed. The shared page is a Claude Artifact (https://claude.ai/artifact/WmC1CHJwfcbyQTGcH6AWzw):
to re-publish, ask Claude Code to publish `report/lab.html` to that same URL (update in place) with the Supabase
`execute_sql` capability; the page reads live Alpaca numbers through the Supabase function `private.alpaca_live('id')`.

## Develop locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest   # or: uv pip install --python .venv/bin/python -r requirements.txt pytest
.venv/bin/python -m pytest -q                                              # all tests pass
set -a; . ~/.config/backtest-lab/alpaca.env; set +a                         # only when a script needs keys
```

Price data is not in git (`data/` is a cache; minute bars come from `cd src && python intraday_data.py SPY QQQ`).
Commit with explicit paths and `git pull --rebase --autostash origin main` first: several agents share the tree.

## Known limitations

- **Live data is IEX, the backtest is SIP.** The free plan has no SIP for the last 15 minutes, so live bars, VWAP and
  bands differ slightly. Live also acts about a minute later than the backtest (the :00 bar, not :59) and exits at
  15:56 with a market order instead of the 16:00 close. `reconcile.py` measures all of this.
- No market-on-close orders: Alpaca paper let them expire.
- The GitHub dispatch token expires about Nov 2027 (renew it in Supabase Vault); a dead token is caught by the watchdog.
- Gemini free tier: the Flash models allow only about 20 requests/day; System C rate-limits itself.
- Claude subscription usage: the research agents and the nightly news review run on `CLAUDE_CODE_OAUTH_TOKEN`; hitting
  the limit makes proposer rounds fail (the morning card warns on a refused login).
- Actions minutes: intraday, research (every 30 min) and watchdog all count; check the usage page if the repo is private.
- GitHub disables schedules after 60 days without repo activity (the commits prevent that).

## Obsolete

`scripts/install_launchd.sh` and `scripts/local_run.sh` install and run the old Mac jobs (daily run with long-term
orders, hourly noise checks, break-even exits). The cloud replaced them and the long-term account is gone, so they are
obsolete and must not be run alongside the cloud jobs. They are still in the repo; delete them when convenient.
