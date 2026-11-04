#!/bin/zsh
# Local scheduled job runner (called by launchd; see scripts/install_launchd.sh).
#   scripts/local_run.sh daily   # after the US close: paper decisions, Alpaca orders, agent, lab page
#   scripts/local_run.sh noise   # hourly QQQ noise-band check / 15:46 flatten (live_noise.py --once)
# Alpaca PAPER keys are read from ~/.config/backtest-lab/alpaca.env (outside the repo, never committed):
#   export ALPACA_API_KEY=...
#   export ALPACA_SECRET_KEY=...
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
cd "$ROOT/src" || exit 1

case "$1" in
  daily) ../.venv/bin/python daily.py --rounds 30 || exit 1
         paths=(agent_memory report/lab.html) ;;
  noise) ../.venv/bin/python live_noise.py --once || exit 1
         paths=(agent_memory/noise_live.csv agent_memory/noise_state.json) ;;
  breakeven) [[ -f ../agent_memory/breakeven_watch.json ]] || exit 0
         ../.venv/bin/python breakeven_exit.py || exit 1
         paths=(agent_memory/breakeven_watch.json)
         [[ -f ../agent_memory/breakeven_log.csv ]] && paths+=(agent_memory/breakeven_log.csv) ;;
  *) echo "usage: $0 daily|noise|breakeven"; exit 2 ;;
esac

cd "$ROOT"
git add -- "${paths[@]}"
git diff --cached --quiet || git commit -q -m "Local $1 run $(date '+%Y-%m-%d %H:%M')" -- "${paths[@]}"
