"""Forward paper trading: the only data nobody, human or agent, has seen yet.

    python -m agent.paper record   # after the close: log today's decision for the watched strategies
    python -m agent.paper report   # score every logged decision whose outcome is now known

Watched = strategies that passed the held-out test, else the agent's top 3 by search reward.
Each row is written with the decision date BEFORE its outcome exists, so the log is point-in-time
by construction. Outcome of a decision at close t = open(t+1) -> open(t+2), like the engine.
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone

import numpy as np

import data
from agent import platform as P
from agent import spec as S
from agent.memory import Memory

LOG = Memory().root / "paper_log.csv"
FIELDS = ["logged_at", "decision_date", "hash", "market", "position", "close"]


def watched(mem: Memory):
    passed = [v for v in mem.holdout_verdicts() if v["pass"]]
    if passed:
        return [(v["hash"], v["spec"]) for v in passed]
    return [(t["hash"], t["spec"]) for t in mem.top(3)]


def record():
    mem = Memory()
    ctx = data.load_context()
    have = set()
    if LOG.exists():
        with LOG.open() as f:
            have = {(r["decision_date"], r["hash"]) for r in csv.DictReader(f)}
    new = LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if not new:
            w.writeheader()
        for h, s in watched(mem):
            m = P.Market(s["market"], ctx)
            pos = S.positions(s, m.table, m.th)
            d = str(m.df.index[-1].date())
            if (d, h) in have:
                continue
            w.writerow({"logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "decision_date": d,
                        "hash": h, "market": s["market"], "position": int(pos[-1]), "close": m.df.Close.iloc[-1]})
            print(f"{d} {h} {S.describe(s)} -> {'LONG' if pos[-1] else 'flat'}")


def report():
    if not LOG.exists():
        print("no paper trades logged yet")
        return
    rows = list(csv.DictReader(LOG.open()))
    by = {}
    for r in rows:
        by.setdefault((r["hash"], r["market"]), []).append(r)
    for (h, mkt), rs in by.items():
        df = data.load(mkt)
        rets = []
        for r in rs:
            i = df.index.searchsorted(np.datetime64(r["decision_date"]))
            if i + 2 < len(df) and int(r["position"]):
                rets.append(df.Open.iloc[i + 2] / df.Open.iloc[i + 1] - 1)
        n_known = sum(1 for r in rs if df.index.searchsorted(np.datetime64(r["decision_date"])) + 2 < len(df))
        tot = float(np.prod(1 + np.array(rets)) - 1) if rets else 0.0
        print(f"{h} {mkt}: {len(rs)} decisions logged, {n_known} with known outcomes, "
              f"{len(rets)} long days, paper return {tot:+.2%}")


if __name__ == "__main__":
    {"record": record, "report": report}[sys.argv[1] if len(sys.argv) > 1 else "report"]()
