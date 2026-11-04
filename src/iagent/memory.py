"""The intraday agent's memory (agent_memory/intraday/): journal.jsonl + lessons.md, same as the daily
agent, with the intraday grammar."""
from __future__ import annotations

import json
from pathlib import Path

from agent.memory import Memory
from iagent import spec as S

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "agent_memory" / "intraday"
PRIOR = ["intraday.json", "intraday3.json", "intraday4.json"]


class IMemory(Memory):
    S = S
    TITLE = "What the intraday agent has learned so far"

    def __init__(self, root: Path = ROOT):
        super().__init__(root)

    def prior_window_pvalues(self) -> list[float]:
        """Coin-flip p-values of every earlier intraday study on the same SPY/QQQ 2021+ window."""
        out = []
        for f in PRIOR:
            p = REPO / "results" / f
            if p.exists():
                out += [r["coin_flip"]["p_value"] for r in json.loads(p.read_text()).get("results", [])
                        if r.get("coin_flip") and r["coin_flip"].get("p_value") is not None]
        return out
