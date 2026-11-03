"""One daily cycle, run after the US close (scheduled at 00:23 UTC):
1. paper trading: log each tracked strategy's decision on the latest COMPLETE bar, score old ones,
   and (when Alpaca paper keys are set) mirror the decisions into the Alpaca paper account;
2. backtesting: one more session of the self-improving agent (memory carries over);
3. rebuild the Strategy Lab page.

    python src/daily.py [--rounds 30]
"""
from __future__ import annotations

import argparse

import os
import sys

import alpaca_paper
import build_lab
import paper
from agent.loop import run as agent_run

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=30)
    a = ap.parse_args()
    sys.argv = sys.argv[:1]  # alpaca_paper.main() parses its own (empty) arguments
    print("== paper trading: record"); paper.record()
    print("== paper trading: report"); paper.report()
    if os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"):
        print("== Alpaca paper account: place orders"); alpaca_paper.main()
    else:
        print("== Alpaca paper account: skipped (ALPACA_API_KEY / ALPACA_SECRET_KEY not set)")
    print("== agent backtesting session"); agent_run(rounds=a.rounds, per_round=10)
    print("== rebuild Strategy Lab"); build_lab.main()
