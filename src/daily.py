"""One daily cycle, run after the US close (scheduled at 00:23 UTC):
1. paper trading: log each tracked strategy's decision on the latest COMPLETE bar, score old ones,
   and (when Alpaca paper keys are set) mirror the decisions into the Alpaca paper account;
2. the candidates-awaiting-approval list and the Strategy Lab page.
The self-learning agents run in research.py (research.yml, every 30 minutes), the only job that writes their memory.

    python src/daily.py [--no-orders]
"""
from __future__ import annotations

import argparse

import os
import sys

import alpaca_paper
import build_lab
import candidates
import paper


def safe(label, fn, *a, **kw):
    """Run one optional step; a failure is logged and never stops the nightly job."""
    try:
        return fn(*a, **kw)
    except Exception as e:
        print(f"!! {label} failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-orders", action="store_true", help="test run: do not place long-term account orders")
    a = ap.parse_args()
    sys.argv = sys.argv[:1]  # alpaca_paper.main() parses its own (empty) arguments
    print("== paper trading: record"); paper.record()
    print("== paper trading: report"); paper.report()
    if a.no_orders:
        print("== Alpaca paper account: skipped (--no-orders test run)")
    elif os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"):
        print("== Alpaca paper account: place orders"); alpaca_paper.main()
    else:
        print("== Alpaca paper account: skipped (ALPACA_API_KEY / ALPACA_SECRET_KEY not set)")
    print("== candidates awaiting approval"); safe("candidates", candidates.update)
    print("== rebuild Strategy Lab"); build_lab.main()
