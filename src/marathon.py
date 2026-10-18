"""Run self-improving agent sessions back to back until a time limit or agent_memory/STOP.
After every session the memory is committed and pushed, so nothing is lost if the machine stops.
Pauses 23:45-01:45 UTC so it never collides with the nightly scheduled cycle (00:23 UTC).

    python src/marathon.py --hours 2 --rounds 50
"""
from __future__ import annotations

import argparse
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from agent.loop import run

ROOT = Path(__file__).resolve().parent.parent
TRAILER = ("\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
           "Claude-Session: https://claude.ai/code/session_01AkVt9ALurp8ghRHZpuszFN")


def git(*a, check=True):
    return subprocess.run(["git", "-C", str(ROOT), *a], check=check, capture_output=True, text=True)


def in_quiet_window(now):
    m = now.hour * 60 + now.minute
    return m >= 23 * 60 + 45 or m < 105


def push(msg):
    git("add", "agent_memory")
    if not git("diff", "--cached", "--quiet", check=False).returncode:
        return "nothing to commit"
    git("commit", "-q", "-m", msg + TRAILER)
    for wait in (0, 2, 4, 8, 16):
        time.sleep(wait)
        if git("push", "-q", "origin", "main", check=False).returncode == 0:
            return "pushed"
    return "push failed (will retry next session)"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=2)
    ap.add_argument("--rounds", type=int, default=50)
    a = ap.parse_args()
    end = time.time() + a.hours * 3600
    k = 0
    while time.time() < end and not (ROOT / "agent_memory" / "STOP").exists():
        now = datetime.now(timezone.utc)
        if in_quiet_window(now):
            print(f"{now:%H:%M} UTC: quiet window for the nightly cycle, waiting", flush=True)
            time.sleep(600)
            continue
        k += 1
        mem = run(rounds=a.rounds, per_round=10, seed=int(time.time()) % 100000, quiet=True)
        best = mem.top(1)
        line = (f"session {k}: {len(mem.trials)} trials total, best now "
                f"{mem.current_reward(best[0]):.3f}, held-out shots ever {len(mem.holdout_tests())}")
        print(f"{datetime.now(timezone.utc):%H:%M} UTC {line} | {push('Agent marathon ' + line)}", flush=True)
    print("marathon finished", flush=True)
