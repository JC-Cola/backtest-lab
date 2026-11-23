"""The intraday agent's memory (agent_memory/intraday/): journal.jsonl + lessons.md, same as the daily
agent, with the intraday grammar."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from agent.memory import Memory
from iagent import shots as H
from iagent import spec as S

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "agent_memory" / "intraday"
PRIOR = ["intraday.json", "intraday3.json", "intraday4.json"]


class IMemory(Memory):
    S = S
    TITLE = "What the intraday agent has learned so far"
    BEST_TITLE = ("Most robust ideas on the search years (all 3 folds positive, median fold Sharpe, 2x costs; "
                  "deflated reward is shown for information only)")

    def __init__(self, root: Path = ROOT):
        super().__init__(root)
        self.band = None   # live noise band: held-out Sharpe at 2x costs of what the intraday account trades now

    def prior_window_pvalues(self) -> list[float]:
        """Coin-flip p-values of every earlier intraday study on the same SPY/QQQ 2021+ window."""
        out = []
        for f in PRIOR:
            p = REPO / "results" / f
            if p.exists():
                out += [r["coin_flip"]["p_value"] for r in json.loads(p.read_text()).get("results", [])
                        if r.get("coin_flip") and r["coin_flip"].get("p_value") is not None]
        return out

    def trial_sharpes(self, market: str) -> list[float]:
        """Multiple-testing count: EVERY scored trial ever run, on every name, deflates every idea (more names = more
        chances of luck), not just the trials of the idea's own market."""
        return [t["daily_sr"] for t in self.trials if t.get("daily_sr") is not None]

    # ---- ranking and learning use the robustness / fold Sharpes: the deflated reward collapsed to 0 (27k+ trials)
    def signal(self, t: dict) -> float:
        """Feature learning signal in [-1, 1]: the median search-fold excess Sharpe (/3), capped by the worst fold
        when some fold is not positive. Varies even when the deflated reward is 0 for everything."""
        fs = t.get("fold_sharpes")
        if not fs:
            return 0.0
        s = float(np.median(fs))
        if min(fs) <= 0:
            s = min(s, min(fs))
        return max(-1.0, min(1.0, s / 3))

    def top(self, k=10, market=None):
        key = lambda t: (H.robustness(t), float(np.median(t["fold_sharpes"])) if t.get("fold_sharpes") else -9)
        return sorted(self.scored(market), key=key, reverse=True)[:k]

    def lesson_line(self, t: dict) -> str:
        f2 = f" | 2x costs {t['fold_sharpes_2x']}" if t.get("fold_sharpes_2x") else ""
        return (f"- robustness {H.robustness(t):.2f} | folds {t['fold_sharpes']}{f2} | {t.get('trades')} trades "
                f"| deflated reward now {self.current_reward(t):.3f} | {self.S.describe(t['spec'])}")

    def write_lessons(self, session_note: str = ""):
        super().write_lessons(session_note)
        now = datetime.now(timezone.utc)
        n = H.shots_this_month(self, now)
        extra = ["", "## Held-out budget", "",
                 f"{n} of {H.K_PER_MONTH} held-out shots spent in {H.month_key(now)}; {len(self.holdout_tests())} spent ever. "
                 "Each shot goes to the most robust untested, non-duplicate idea; a pass needs BH significance across all shots "
                 "ever and beating the live noise band at 2x costs. The deflated Sharpe is informational (27k+ trials keep it near 0)."]
        p = self.root / "lessons.md"
        p.write_text(p.read_text() + "\n".join(extra) + "\n")

    # ---- verdicts: BH across all shots ever (+ earlier hunt tests) AND beating the live noise band at 2x costs
    def noise_band(self):
        if self.band is None:
            import candidates
            self.band = candidates.benchmarks(live=[])["intraday"]
        return self.band

    def holdout_verdicts(self) -> list[dict]:
        out = super().holdout_verdicts()
        if not out:
            return out
        band = self.noise_band()
        for v in out:
            t = self.trials[v["n"] - 1]
            h = t["holdout"]
            v["stat_pass"] = v["pass"]                    # BH q < 0.10, Sharpe > 0 at 1x and 2x costs
            # SPY/QQQ ideas must beat the live noise band; single-stock ideas must beat SPY buy-and-hold (same window), both at 2x costs
            v["bench"] = band if t["spec"]["market"] in ("SPY", "QQQ") else h.get("spy_bh_sharpe")
            v["beats_band"] = v["bench"] is not None and h["sharpe_2x_cost"] > v["bench"]
            v["lockbox_ok"] = bool((h.get("lockbox") or {}).get("ok", False))   # unseen names; fail closed when missing
            v["pass"] = bool(v["stat_pass"] and v["beats_band"] and v["lockbox_ok"])
            v["dsr_now"] = round(self.current_reward(t), 4) if "T" in t else None   # information only, does not gate
        return out
