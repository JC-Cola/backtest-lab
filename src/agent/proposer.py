"""Proposers: where new strategy ideas come from. Both read the memory first.

- EvolutionProposer (default, free): mutates and recombines the best-scoring specs so far,
  explores new random ones, and samples features by the weights the memory has learned.
  Never re-proposes anything already tried (including rejected ideas).
- ClaudeProposer (optional): asks Claude for new specs, giving it the grammar, lessons.md and
  the recent results. Goes through the local Claude Code CLI (subscription login, no API key).
- WebResearchProposer: like ClaudeProposer, but Claude first searches the web for recent published trading
  ideas and translates them into the grammar, with the source of each spec.
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
    name = "claude"

    def __init__(self, memory: Memory):
        self.mem = memory
        self.meta = {}  # spec hash -> source_url/source_title and the bull/bear/risk review (web research only)
        self.skipped = []  # (spec, reason, detail) of the ideas the risk review refused to test

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

    def _ask(self, k, markets):
        return claude_cli.ask(self._prompt(k, markets), SPEC_SCHEMA)

    def propose(self, k: int, markets: list[str]) -> list[dict]:
        resp = self._ask(k, markets)
        self.skipped = []
        if not resp:
            return []
        out, hashes = [], set()
        for item in resp.get("specs", []):
            raw, src = (item.get("spec"), {x: str(item.get(x) or "")[:300] for x in ("source_url", "source_title")}
                        | {x: item[x] for x in ("bull_case", "bear_case", "risk_review") if x in item}) \
                if isinstance(item, dict) else (item, {})
            try:
                s = self.S.canonical(self.S.validate(json.loads(raw)))
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                self.mem.add({"status": "rejected", "reason": f"invalid spec: {e}", "raw": str(raw), "spec": None,
                              "proposer": self.name} | src)
                continue
            h = self.S.spec_hash(s)
            if h in self.mem.seen or h in hashes:
                continue
            hashes.add(h)
            rr = src.get("risk_review")
            if isinstance(rr, dict) and rr.get("verdict") == "skip":
                why = "risk review: " + "; ".join(str(x) for x in rr.get("reasons") or ["skip"])
                self.mem.add({"status": "rejected", "reason": why, "hash": h, "spec": s, "proposer": self.name} | src)
                self.skipped.append((s, why, src))
                continue
            out.append(s)
            if src:
                self.meta[h] = src
        return out


WEB_SCHEMA = {
    "type": "object",
    "properties": {"specs": {"type": "array", "items": {
        "type": "object",
        "properties": {"spec": {"type": "string"}, "source_url": {"type": "string"}, "source_title": {"type": "string"},
                       "bull_case": {"type": "string"}, "bear_case": {"type": "string"},
                       "risk_review": {"type": "object", "properties": {
                           "verdict": {"type": "string", "enum": ["test", "skip"]},
                           "reasons": {"type": "array", "items": {"type": "string"}},
                           "cost_sensitivity": {"type": "string", "enum": ["low", "medium", "high"]},
                           "lookahead_risk": {"type": "string", "enum": ["low", "medium", "high"]}},
                           "required": ["verdict", "reasons", "cost_sensitivity", "lookahead_risk"], "additionalProperties": False}},
        "required": ["spec", "source_url", "source_title", "bull_case", "bear_case", "risk_review"], "additionalProperties": False}}},
    "required": ["specs"],
    "additionalProperties": False,
}


class WebResearchProposer(ClaudeProposer):
    """Claude searches the web (WebSearch/WebFetch only) for recent trading ideas and turns each into a spec."""
    name = "web"
    FOCUS = ("daily long-only rules on SPY, QQQ, IWM, DIA, GLD, TLT and BTC-USD (decide at the close, trade the next open)")

    def _prompt(self, k, markets):
        return f"""FIRST do web research. Use WebSearch and WebFetch to find recent (roughly the last 3 years) published or
well-documented trading ideas relevant to this lab: {self.FOCUS}. Look at SSRN, arXiv (q-fin), quant blogs and
practitioner write-ups. Actually read the pages, and only use ideas whose rule you understood. THEN translate each idea
into one spec in the grammar below (a faithful simplification is fine). Avoid ideas the lab already tested (see the
lessons and trials at the end).

Each returned item is an object: "spec" (the spec as a JSON string), "source_url" (the page you read) and
"source_title" (its title). Return at most {k} items, each from a different idea.
Before returning, review each idea twice in the same answer. "bull_case": why it could work, with the source's evidence.
"bear_case": why it could fail (crowding or decay since publication, costs, data-snooping in the source, look-ahead risk,
overlap with ideas in the lessons). Then act as a risk manager: "risk_review" = {{verdict "test" or "skip", reasons,
cost_sensitivity low/medium/high, lookahead_risk low/medium/high}}. Only "test" ideas are backtested; "skip" ones are
recorded as rejected, so skip what you doubt.

{super()._prompt(k, markets)}"""

    def _ask(self, k, markets):
        return claude_cli.ask(self._prompt(k, markets), WEB_SCHEMA, allow_web=True)
