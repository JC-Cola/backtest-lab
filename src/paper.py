"""Forward paper trading: the only test on data nobody, human or agent, has seen.

    python src/paper.py record   # after the close: write down each strategy's next decision
    python src/paper.py report   # score every decision whose outcome is now known

Tracked: every strategy in the Strategy Lab book (at its tuning-year settings) plus the agent's
current top 3 ideas. Rules that keep it honest:
- only COMPLETE daily bars are used (a bar for today is dropped until the market has closed;
  crypto bars for the current UTC day are always dropped);
- each row is written with a UTC timestamp before its outcome exists; the outcome of a decision
  made on the close of day d is open(d+1) -> open(d+2), exactly like the backtest engine;
- costs are charged on every change of position, at the same rates as the backtest.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import data
from agent import platform as AP
from agent import spec as AS
from agent.memory import Memory
from build_lab import BOOK
from run import CONFIG
from strategies import STRATEGIES, positions

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "agent_memory" / "paper_log.csv"
REPORT = ROOT / "agent_memory" / "paper_report.json"
FIELDS = ["logged_at_utc", "decision_date", "key", "name", "market", "position", "close"]
BY_ID = {s["id"]: s for s in STRATEGIES}
ET = ZoneInfo("America/New_York")


def complete_bars(df: pd.DataFrame, ticker: str, now: datetime) -> pd.DataFrame:
    """Drop any bar whose day has not finished yet."""
    if ticker.endswith("-USD"):  # crypto: daily bars close at 00:00 UTC
        return df[df.index.date < now.astimezone(timezone.utc).date()]
    et = now.astimezone(ET)
    closed = (et.hour, et.minute) >= (16, 30)
    keep = df.index.date < et.date() if not closed else df.index.date <= et.date()
    return df[keep]


def tracked(mem: Memory):
    """(key, name, market, function returning a position series for a price frame)."""
    res = json.loads((ROOT / "results" / "results.json").read_text())["results"]
    out = []
    for b in BOOK:
        row = next(r for r in res if r["strategy"] == b["id"] and r["asset"] == b["market"])
        st, params = BY_ID[b["id"]], row["params"]
        out.append((b["code"], st["name"], b["market"],
                    lambda df, ctx, st=st, params=params: positions(st, df, ctx, params).values))
    for t in mem.top(3):
        s = t["spec"]
        out.append(("AGENT-" + t["hash"], "Agent: " + AS.describe(s), s["market"], ("agent", s)))
    return out


def record(now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    mem = Memory()
    ctx_raw = data.load_context(max_age_s=3600)
    have = set()
    if LOG.exists():
        with LOG.open() as f:
            have = {(r["decision_date"], r["key"]) for r in csv.DictReader(f)}
    rows = []
    for key, name, mkt, fn in tracked(mem):
        cfg = CONFIG[mkt]
        df = complete_bars(data.load(mkt, max_age_s=3600), mkt, now).loc[cfg["is_start"]:]
        ctx = data.align_ctx(ctx_raw, df.index)
        if isinstance(fn, tuple):  # agent idea: thresholds stay fixed to its search window
            m = AP.Market(mkt, ctx_raw)
            tab = AP.F.build(df, ctx)
            pos = AS.positions(fn[1], tab, m.th)
        else:
            pos = np.clip(np.nan_to_num(fn(df, ctx).astype(float)), 0, 1)
        d = str(df.index[-1].date())
        if (d, key) in have:
            continue
        rows.append({"logged_at_utc": now.isoformat(timespec="seconds"), "decision_date": d, "key": key, "name": name,
                     "market": mkt, "position": round(float(pos[-1]), 4), "close": round(float(df.Close.iloc[-1]), 4)})
        print(f"{d} {key:22s} {mkt:8s} -> {'IN ' + format(pos[-1], '.0%') if pos[-1] > 0 else 'cash'}")
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)
    print(f"logged {len(rows)} new decisions ({len(have)} already on file)")


def report():
    if not LOG.exists():
        print("no paper trades logged yet")
        return {}
    rows = list(csv.DictReader(LOG.open()))
    out = {}
    for key in dict.fromkeys(r["key"] for r in rows):
        rs = sorted((r for r in rows if r["key"] == key), key=lambda r: r["decision_date"])
        mkt = rs[0]["market"]
        df = data.load(mkt, max_age_s=3600)
        cost = CONFIG[mkt]["cost_bps"] / 1e4
        strat, hold, prev, scored = [], [], 0.0, 0
        for r in rs:
            i = df.index.searchsorted(pd.Timestamp(r["decision_date"]), "right")  # next session
            if i + 1 >= len(df):
                continue  # outcome not known yet
            p = float(r["position"])
            ret = df.Open.iloc[i + 1] / df.Open.iloc[i] - 1
            strat.append(p * ret - cost * abs(p - prev))
            hold.append(ret)
            prev, scored = p, scored + 1
        tot = float(np.prod(1 + np.array(strat)) - 1) if strat else 0.0
        bh = float(np.prod(1 + np.array(hold)) - 1) if hold else 0.0
        out[key] = {"name": rs[0]["name"], "market": mkt, "since": rs[0]["decision_date"], "decisions": len(rs),
                    "scored": scored, "paper_return": tot, "holding_return": bh,
                    "days_invested": int(sum(float(r["position"]) > 0 for r in rs[:scored])),
                    "latest": {"date": rs[-1]["decision_date"], "position": float(rs[-1]["position"])}}
        print(f"{key:22s} since {rs[0]['decision_date']}: {scored}/{len(rs)} scored, paper {tot:+.2%} vs holding {bh:+.2%}, "
              f"latest {'IN' if float(rs[-1]['position']) > 0 else 'cash'}")
    REPORT.write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "strategies": out}, indent=1))
    return out


if __name__ == "__main__":
    {"record": record, "report": report}[sys.argv[1] if len(sys.argv) > 1 else "report"]()
