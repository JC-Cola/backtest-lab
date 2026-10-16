"""The agent's memory on disk (agent_memory/). Read at the start of every session, appended to
after every trial, so a new session continues where the last one stopped.

- journal.jsonl : one line per trial: spec, hash, scores, status, rejection reason, holdout result
- lessons.md    : a plain-language summary rewritten at the end of each session
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from agent import spec as S
from stats import bh_qvalues, deflated_sharpe_moments

ROOT = Path(__file__).resolve().parents[2] / "agent_memory"


class Memory:
    def __init__(self, root: Path = ROOT):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "journal.jsonl"
        self.trials = [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()] \
            if self.path.exists() else []
        self.seen = {t["hash"] for t in self.trials if t.get("hash")}
        self._sr_cache = {}

    # ---------------------------------------------------------------- writing
    def add(self, rec: dict) -> dict:
        rec = {"n": len(self.trials) + 1, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")} | rec
        self.trials.append(rec)
        if rec.get("hash"):
            self.seen.add(rec["hash"])
        with self.path.open("a") as f:
            f.write(json.dumps(rec, default=float) + "\n")
        return rec

    def update(self, n: int, **fields):
        self.trials[n - 1].update(fields)
        self.path.write_text("".join(json.dumps(t, default=float) + "\n" for t in self.trials))

    # ---------------------------------------------------------------- reading
    def scored(self, market=None):
        return [t for t in self.trials if t.get("status") in ("scored", "promoted")
                and (market is None or t["spec"]["market"] == market)]

    def trial_sharpes(self, market: str) -> list[float]:
        """Every scored trial ever run on this market counts toward the deflation."""
        return [t["daily_sr"] for t in self.trials if t.get("daily_sr") is not None and t["spec"]["market"] == market]

    def current_reward(self, t: dict) -> float:
        """A trial's reward re-deflated against EVERY trial run on its market so far, so an early
        lucky find loses its shine as the search grows."""
        if "T" not in t:
            return t.get("reward", 0.0)
        srs = self._sr_cache.setdefault(t["spec"]["market"], self.trial_sharpes(t["spec"]["market"]))
        d = deflated_sharpe_moments(t["daily_sr"], t["T"], t["skew"], t["kurt"], srs)
        return float(d if t.get("consistent") else d * 0.5)

    def top(self, k=10, market=None):
        self._sr_cache = {}
        return sorted(self.scored(market), key=lambda t: -self.current_reward(t))[:k]

    def feature_weights(self) -> dict:
        """Learned preference: features that appear in well-scoring trials get proposed more."""
        acc = defaultdict(list)
        for t in self.trials:
            if t.get("status") in ("scored", "promoted"):
                for f in S.features_used(t["spec"]):
                    acc[f].append(t["reward"])
        base = np.mean([t["reward"] for t in self.scored()]) if self.scored() else 0.0  # relative weights
        return {f: float(np.exp(4 * (np.mean(v) - base))) for f, v in acc.items()}

    def holdout_tests(self):
        return [t for t in self.trials if t.get("holdout")]

    @staticmethod
    def prior_window_pvalues() -> list[float]:
        """Coin-flip p-values of every human-hunt test already run on the same held-out years."""
        f = ROOT.parent / "results" / "results.json"
        if not f.exists():
            return []
        res = json.loads(f.read_text())["results"]
        return [r["coin_flip"]["p_value"] for r in res if r.get("coin_flip")]

    def holdout_verdicts(self) -> list[dict]:
        """Benjamini-Hochberg across EVERY test ever run on the held-out years: the agent's shots
        plus all earlier hunt tests, because those years have already been searched."""
        hs = self.holdout_tests()
        if not hs:
            return []
        prior = self.prior_window_pvalues()
        qs = bh_qvalues([t["holdout"]["p_value"] for t in hs] + prior)[: len(hs)]
        out = []
        for t, q in zip(hs, qs):
            h = t["holdout"]
            ok = q < 0.10 and h["sharpe"] > 0 and h["sharpe_2x_cost"] > 0
            out.append({"n": t["n"], "hash": t["hash"], "q": float(q), "pass": bool(ok), "spec": t["spec"]})
        return out

    # ---------------------------------------------------------------- lessons
    def write_lessons(self, session_note: str = ""):
        sc = self.scored()
        rej = [t for t in self.trials if t.get("status") == "rejected"]
        reasons = defaultdict(int)
        for t in rej:
            reasons[t["reason"].split(" (")[0].split(":")[0]] += 1
        w = sorted(self.feature_weights().items(), key=lambda x: -x[1])
        hv = self.holdout_verdicts()
        lines = [
            "# What the agent has learned so far", "",
            f"Updated {datetime.now(timezone.utc).date()}. {len(self.trials)} trials in total: "
            f"{len(sc)} scored, {len(rej)} rejected, {len(hv)} sent to the held-out test, "
            f"{sum(v['pass'] for v in hv)} passed it.", "",
            "## Best ideas on the search years (deflated by every trial run)", "",
        ]
        for t in self.top(10):
            lines.append(f"- reward now {self.current_reward(t):.3f} (when found {t['reward']:.3f}) | folds {t['fold_sharpes']} | {S.describe(t['spec'])}")
        lines += ["", "## Features that tend to help (learned weights, 1 = average)", ""]
        lines += [f"- {f}: {v:.2f}" for f, v in w[:8]]
        lines += ["", "## Features that tend to hurt", ""]
        lines += [f"- {f}: {v:.2f}" for f, v in w[-5:]]
        lines += ["", "## Why ideas were rejected", ""]
        lines += [f"- {k}: {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])]
        lines += ["", f"## Held-out tests (one shot each; BH-corrected together with the "
                  f"{len(self.prior_window_pvalues())} earlier hunt tests on the same years)", ""]
        for v in hv:
            lines.append(f"- {'PASS' if v['pass'] else 'fail'} (q={v['q']:.2f}) {S.describe(v['spec'])}")
        if session_note:
            lines += ["", "## Last session", "", session_note]
        (self.root / "lessons.md").write_text("\n".join(lines) + "\n")
