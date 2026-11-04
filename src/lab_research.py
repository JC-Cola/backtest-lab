"""Research-tab data for the Strategy Lab page: the intraday agent's block and the candidates table."""
from __future__ import annotations

import candidates
from iagent import spec as IS
from iagent.memory import IMemory


def intraday_research(mem=None) -> dict:
    """Same shape as build_lab's daily `research` dict, from the intraday agent's memory."""
    mem = mem or IMemory()
    hv = mem.holdout_verdicts()
    return {"trials": len(mem.trials), "scored": len(mem.scored()),
            "rejected": sum(1 for t in mem.trials if t.get("status") == "rejected"),
            "holdout_shots": len(hv), "holdout_pass": sum(v["pass"] for v in hv),
            "top": [{"desc": IS.describe(t["spec"]), "now": round(mem.current_reward(t), 3),
                     "found": t["reward"], "folds": t["fold_sharpes"]} for t in mem.top(8)]}


def page_data(imem=None, cands_path=None) -> dict:
    return {"research_intraday": intraday_research(imem), "candidates": candidates.load(cands_path)}
