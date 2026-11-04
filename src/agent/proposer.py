"""Proposers: where new strategy ideas come from. Both read the memory first.

- EvolutionProposer (default, free): mutates and recombines the best-scoring specs so far,
  explores new random ones, and samples features by the weights the memory has learned.
  Never re-proposes anything already tried (including rejected ideas).
- ClaudeProposer (optional): asks Claude for new specs, giving it the grammar, lessons.md and
  the recent results. Goes through the local Claude Code CLI (subscription login, no API key).
"""
from __future__ import annotations

import json
import random

from agent import claude_cli
from agent import spec as S
from agent.features import CALENDAR, CONTEXT, PRICE
from agent.memory import Memory


class EvolutionProposer:
    S = S  # spec module; subclasses swap in another grammar

    def __init__(self, memory: Memory, seed: int = 0, explore: float = 0.3):
        self.mem, self.rng, self.explore = memory, random.Random(seed + len(memory.trials)), explore

    def propose(self, k: int, markets: list[str]) -> list[dict]:
        w = self.mem.feature_weights()
        out, hashes = [], set()
        parents = [t["spec"] for t in self.mem.top(20) if t["spec"]["market"] in markets]
        tries = 0
        while len(out) < k and tries < k * 50:
            tries += 1
            r = self.rng.random()
            if not parents or r < self.explore:
                s = self.S.random_spec(self.rng, w, self.rng.choice(markets))
            elif r < 0.85 or len(parents) < 2:
                s = self.S.mutate(self.rng.choice(parents[: max(3, len(parents) // 2)]), self.rng, w)
            else:
                a, b = self.rng.sample(parents, 2)
                s = self.S.mutate(self.S.crossover(a, b, self.rng), self.rng, w)
            h = self.S.spec_hash(s)
            if h in self.mem.seen or h in hashes:
                continue
            hashes.add(h)
            out.append(self.S.canonical(s))
        return out


SPEC_SCHEMA = {
    "type": "object",
    "properties": {"specs": {"type": "array", "items": {"type": "string"}}},
    "required": ["specs"],
    "additionalProperties": False,
}


class ClaudeProposer:
    """Asks Claude (through the local Claude Code CLI) for the next experiments. Returned specs are validated;
    invalid ones are recorded as rejected with the reason, so Claude sees its own mistakes next round."""
    S = S  # spec module; subclasses swap in another grammar

    def __init__(self, memory: Memory):
        self.mem = memory

    def _lessons_and_recent(self) -> tuple[str, str]:
        f = self.mem.root / "lessons.md"
        rec = "\n".join(
            f"- {t.get('status')}: {self.S.describe(t['spec']) if t.get('spec') else t.get('raw', '')[:120]} | "
            f"reward {t.get('reward')} folds {t.get('fold_sharpes')} {t.get('reason') or ''}" for t in self.mem.trials[-25:])
        return (f.read_text() if f.exists() else "(none yet)"), rec

    def _prompt(self, k, markets):
        lessons, rec = self._lessons_and_recent()
        return f"""You are the research agent of a trading backtest lab. Propose {k} NEW long-only daily strategy specs
for these markets: {markets}. Each spec is scored on in-sample years only, split into 3 time folds, deflated by
the total number of trials ever run, so only propose ideas you have a real reason to expect to be robust.
Do not repeat anything already tried.

Grammar (return each spec as a JSON string):
{S.__doc__}
Features (all known at the decision close): price {PRICE}; context {CONTEXT}; calendar {CALENDAR}.
"q" is a percentile 1-99 of that feature over the search years.

What the lab has learned so far:
{lessons}

Most recent trials:
{rec}
"""

    def propose(self, k: int, markets: list[str]) -> list[dict]:
        resp = claude_cli.ask(self._prompt(k, markets), SPEC_SCHEMA)
        if not resp:
            return []
        out, hashes = [], set()
        for raw in resp.get("specs", []):
            try:
                s = self.S.canonical(self.S.validate(json.loads(raw)))
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                self.mem.add({"status": "rejected", "reason": f"invalid spec: {e}", "raw": str(raw), "spec": None,
                              "proposer": "claude"})
                continue
            h = self.S.spec_hash(s)
            if h not in self.mem.seen and h not in hashes:
                hashes.add(h)
                out.append(s)
        return out
