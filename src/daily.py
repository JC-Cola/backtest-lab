"""One daily cycle, run after the US close (scheduled at 00:23 UTC):
1. paper trading: log each tracked strategy's decision on the latest COMPLETE bar, score old ones;
2. backtesting: one more session of the self-improving agent (memory carries over);
3. rebuild the Strategy Lab page.

    python src/daily.py [--rounds 30]
"""
from __future__ import annotations

import argparse

import build_lab
import paper
from agent.loop import run as agent_run

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=30)
    a = ap.parse_args()
    print("== paper trading: record"); paper.record()
    print("== paper trading: report"); paper.report()
    print("== agent backtesting session"); agent_run(rounds=a.rounds, per_round=10)
    print("== rebuild Strategy Lab"); build_lab.main()
