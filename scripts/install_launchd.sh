#!/bin/zsh
# Install (or with --remove, uninstall) the launchd jobs that run scripts/local_run.sh on weekdays.
# Times are in the Mac's local time zone; they assume New York time (the US market's).
#   noise: 10:01, 11:01, ... 15:01 checks and the 15:47 flatten
#   daily: 16:45, after the close
set -eu
ROOT="${0:A:h:h}"
AGENTS="$HOME/Library/LaunchAgents"

if [[ "${1:-}" == "--remove" ]]; then
  for job in daily noise breakeven; do
    launchctl bootout "gui/$UID/com.backtestlab.$job" 2>/dev/null || true
    rm -f "$AGENTS/com.backtestlab.$job.plist"
  done
  echo "removed"
  exit 0
fi

[[ "$(date +%Z)" == E[SD]T ]] || echo "warning: this Mac is not on New York time; the job times will be off"

times_for() {  # $1 = job; prints <dict> entries for weekdays 1-5
  local hm=()
  if [[ $1 == noise ]]; then hm=(10:1 11:1 12:1 13:1 14:1 15:1 15:47); else hm=(16:45); fi
  for d in 1 2 3 4 5; do
    for t in "${hm[@]}"; do
      echo "    <dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>${t%%:*}</integer><key>Minute</key><integer>${t##*:}</integer></dict>"
    done
  done
}

for job in daily noise breakeven; do
  plist="$AGENTS/com.backtestlab.$job.plist"
  if [[ $job == breakeven ]]; then  # every 5 minutes; it does nothing once its watch list is empty
    when="<key>StartInterval</key><integer>300</integer>"
  else
    when="<key>StartCalendarInterval</key>
  <array>
$(times_for $job)
  </array>"
  fi
  cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.backtestlab.$job</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$ROOT/scripts/local_run.sh</string><string>$job</string></array>
  $when
</dict>
</plist>
EOF
  plutil -lint "$plist" >/dev/null
  launchctl bootout "gui/$UID/com.backtestlab.$job" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$plist"
  echo "installed com.backtestlab.$job"
done
echo "logs: ~/Library/Logs/backtest-lab/"
