"""One daily cycle, run after the US close (scheduled at 00:23 UTC):
1. paper trading: log each tracked strategy's decision on the latest COMPLETE bar, score old ones,
   and (when Alpaca paper keys are set) mirror the decisions into the Alpaca paper account;
2. backtesting: one more session of each self-improving agent (daily, intraday; memory carries over), then
   a few rounds where Claude (local CLI) proposes the ideas, then the candidates-awaiting-approval list;
3. rebuild the Strategy Lab page.

    python src/daily.py [--rounds 30] [--claude-rounds 2]
"""
from __future__ import annotations

import argparse

import os
import sys

import alpaca_paper
import build_lab
import candidates
import paper
from agent import claude_cli
from agent.loop import run as agent_run
from iagent.loop import run as iagent_run


def safe(label, fn, *a, **kw):
    """Run one optional step; a failure is logged and never stops the nightly job."""
    try:
        return fn(*a, **kw)
    except Exception as e:
        print(f"!! {label} failed: {type(e).__name__}: {e}")


def claude_steps(rounds):
    if rounds <= 0 or claude_cli.binary() is None:
        print("== Claude proposer: skipped (no rounds, or claude binary not found)")
        return
    print("== daily agent: Claude proposer"); safe("daily claude", agent_run, rounds=rounds, per_round=8, proposer="claude")
    print("== intraday agent: Claude proposer"); safe("intraday claude", iagent_run, rounds=rounds, per_round=8, proposer="claude")
    print(f"Claude CLI cost tonight: ${claude_cli.spent():.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--claude-rounds", type=int, default=2)
    a = ap.parse_args()
    sys.argv = sys.argv[:1]  # alpaca_paper.main() parses its own (empty) arguments
    print("== paper trading: record"); paper.record()
    print("== paper trading: report"); paper.report()
    if os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"):
        print("== Alpaca paper account: place orders"); alpaca_paper.main()
    else:
        print("== Alpaca paper account: skipped (ALPACA_API_KEY / ALPACA_SECRET_KEY not set)")
    print("== agent backtesting session"); agent_run(rounds=a.rounds, per_round=10)
    print("== intraday agent backtesting session"); safe("intraday agent", iagent_run, rounds=a.rounds, per_round=10)
    claude_steps(a.claude_rounds)
    print("== candidates awaiting approval"); safe("candidates", candidates.update)
    print("== rebuild Strategy Lab"); build_lab.main()
