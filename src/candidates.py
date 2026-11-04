"""Candidates awaiting approval: strategies the agents found that passed their one-shot held-out test.
Written to agent_memory/candidates.json. "better" = held-out Sharpe at 2x costs beats what the matching
paper account trades now. Nothing here ever trades; the user approves by hand (status "approved")."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PATH = REPO / "agent_memory" / "candidates.json"
NOT_BETTER = "not better than what is traded now"


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def benchmarks(results_dir: Path | None = None, live: list[tuple[str, str]] | None = None) -> dict:
    """Test-year Sharpe at 2x costs of what each paper account trades now.
    intraday: mean oos_cost2x_sharpe of id_noise_breakout on SPY and QQQ (results/intraday.json).
    long-term: mean oos_cost2x_sharpe of the live daily strategies (results/results.json); live is a list of
    (strategy id, market), default build_lab.BOOK entries whose code is in build_lab.LAB_DAILY."""
    rd = results_dir or REPO / "results"
    if live is None:
        import build_lab
        live = [(b["id"], b["market"]) for b in build_lab.BOOK if b["code"] in build_lab.LAB_DAILY]
    load = lambda f: json.loads((rd / f).read_text()).get("results", []) if (rd / f).exists() else []
    daily = {(r["strategy"], r["asset"]): r for r in load("results.json")}
    long_ = [(daily[k].get("oos_cost2x_sharpe", daily[k]["oos"]["sharpe"])) for k in live if k in daily]
    intra = [r.get("oos_cost2x_sharpe") for r in load("intraday.json")
             if r.get("strategy") == "id_noise_breakout" and r.get("asset") in ("SPY", "QQQ")]
    return {"intraday": _mean(intra), "long-term": _mean(long_)}


def _from(mem, account: str, bench: float | None, old: dict) -> list[dict]:
    out = []
    for v in mem.holdout_verdicts():
        if not v["pass"]:
            continue
        h = mem.trials[v["n"] - 1]["holdout"]
        better = bench is not None and h["sharpe_2x_cost"] > bench
        status = "approved" if old.get(v["hash"]) == "approved" else ("awaiting approval" if better else NOT_BETTER)
        out.append({"account": account, "hash": v["hash"], "description": mem.S.describe(v["spec"]),
                    "sharpe": h["sharpe"], "sharpe_2x_cost": h["sharpe_2x_cost"], "p_value": h["p_value"],
                    "trades": h["trades"], "window": h["window"], "q": v["q"], "better": better, "status": status})
    return out


def update(daily_mem=None, intra_mem=None, path: Path | None = None, results_dir: Path | None = None,
           live: list[tuple[str, str]] | None = None) -> dict:
    from agent.memory import Memory
    from iagent.memory import IMemory
    path = path or PATH
    old = {c["hash"]: c.get("status") for c in load(path)["candidates"]}
    bm = benchmarks(results_dir, live)
    cands = (_from(daily_mem or Memory(), "long-term", bm["long-term"], old)
             + _from(intra_mem or IMemory(), "intraday", bm["intraday"], old))
    doc = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "benchmarks": bm, "candidates": cands}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, default=float) + "\n")
    return doc


def load(path: Path | None = None) -> dict:
    p = path or PATH
    try:
        d = json.loads(p.read_text())
        return {"generated": d.get("generated"), "benchmarks": d.get("benchmarks", {}), "candidates": d.get("candidates", [])}
    except (OSError, ValueError):
        return {"generated": None, "benchmarks": {}, "candidates": []}
