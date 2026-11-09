"""Nightly Strategy Lab build, run after the US close (nightly.yml). The long-term account was retired 2026-11-09, so this
no longer records long-term paper decisions or places any orders (paper.py / alpaca_paper.py stay as history):
1. the candidates-awaiting-approval list (agent_memory/candidates.json);
2. the Strategy Lab page (report/lab.html).
The self-learning intraday agent runs in research.py (research.yml, every 30 minutes), the only job that writes its memory.

    python src/daily.py
"""
from __future__ import annotations

import build_lab
import candidates


def safe(label, fn, *a, **kw):
    """Run one optional step; a failure is logged and never stops the nightly job."""
    try:
        return fn(*a, **kw)
    except Exception as e:
        print(f"!! {label} failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    print("== candidates awaiting approval"); safe("candidates", candidates.update)
    print("== rebuild Strategy Lab"); build_lab.main()
