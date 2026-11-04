#!/bin/zsh
# Local scheduled job runner (called by launchd; see scripts/install_launchd.sh).
#   scripts/local_run.sh daily       # after the US close: paper decisions, long-term account orders, agent, lab page
#   scripts/local_run.sh noise       # hourly SPY/QQQ noise-band check / 15:46 flatten on the intraday account
#   scripts/local_run.sh breakeven   # break-even exits on the long-term account (only while a watch list exists)
# Alpaca PAPER keys are read from ~/.config/backtest-lab/alpaca.env (outside the repo, never committed):
#   export ALPACA_API_KEY=...             export ALPACA_INTRADAY_API_KEY=...
#   export ALPACA_SECRET_KEY=...          export ALPACA_INTRADAY_SECRET_KEY=...
set -u
ROOT="${0:A:h:h}"
KEYS="$HOME/.config/backtest-lab/alpaca.env"
LOGDIR="$HOME/Library/Logs/backtest-lab"
mkdir -p "$LOGDIR"
exec >>"$LOGDIR/$1.log" 2>&1
echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') $1"

if [[ ! -f "$KEYS" ]]; then
  echo "no keys file at $KEYS; skipping"
  exit 0
fi
source "$KEYS"
cd "$ROOT" || exit 1
py() { (cd src && ../.venv/bin/python "$@") }

case "$1" in
  daily) py daily.py --rounds 30 || exit 1
         paths=(agent_memory report/lab.html) ;;
  noise) py live_noise.py --once || exit 1
         paths=(agent_memory/noise_live.csv agent_memory/noise_state_*.json(N) agent_memory/alpaca_intraday.json(N)) ;;
  breakeven) [[ -f agent_memory/breakeven_watch.json ]] || exit 0
         py breakeven_exit.py || exit 1
         paths=(agent_memory/breakeven_watch.json agent_memory/breakeven_log.csv(N)) ;;
  *) echo "usage: $0 daily|noise|breakeven"; exit 2 ;;
esac

git add -- "${paths[@]}"
git diff --cached --quiet || git commit -q -m "Local $1 run $(date '+%Y-%m-%d %H:%M')" -- "${paths[@]}"
