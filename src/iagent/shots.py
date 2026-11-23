"""The budgeted held-out protocol of the intraday agent (methods change 2026-11-23, see INTRADAY_AGENT.md).

The deflated Sharpe over ~27.6k trials can never reach 0.95 again, so it no longer gates the held-out test. Instead:
  * at most K_PER_MONTH held-out shots per calendar month;
  * each shot goes to the best not-yet-tested idea by a ROBUSTNESS score computed on the SEARCH years only;
  * a shot may not be a near-duplicate of a tested idea (similar());
  * a pass needs BH significance across ALL shots ever + beating the live noise band at 2x costs (IMemory.holdout_verdicts).
Nothing here touches the held-out years except the injected holdout function, called once per spent shot."""
from __future__ import annotations

import heapq
from datetime import datetime, timezone

import numpy as np

from iagent import spec as S

K_PER_MONTH = 3
MIN_ROBUST_TRADES = 150     # search-year trades (about 1 per 8 trading days) for a fold-level Sharpe to mean anything
SIM_Q = 5                   # percentile tolerance of the similarity rule
MAX_EVALS = 200             # cap on 2x-cost re-evaluations of old journal records per pick


def robustness(t: dict) -> float:
    """0 = not eligible. Else min(median fold Sharpe at 1x costs, median fold Sharpe at 2x costs), where eligible means
    all 3 folds positive (at 1x and, when known, at 2x costs) and at least MIN_ROBUST_TRADES trades. Without
    fold_sharpes_2x (old records) the 1x median is a provisional upper bound (see has_2x)."""
    f1, f2 = t.get("fold_sharpes"), t.get("fold_sharpes_2x")
    if not f1 or len(f1) < 3 or t.get("trades", 0) < MIN_ROBUST_TRADES or min(f1) <= 0:
        return 0.0
    m = float(np.median(f1))
    if f2:
        if min(f2) <= 0:
            return 0.0
        m = min(m, float(np.median(f2)))
    return m


def has_2x(t: dict) -> bool:
    return bool(t.get("fold_sharpes_2x"))


def similar(a: dict, b: dict) -> bool:
    """Near-duplicate rule: same hash, or identical in market/side/time with the same (feature, op) conditions whose
    percentiles each differ by at most SIM_Q (a mere threshold tweak). The exit hold, stop_bp and target_r are ignored: the
    same idea with and without a stop (or another hold/target) is one idea and must not spend a second held-out shot."""
    if S.spec_hash(a) == S.spec_hash(b):
        return True
    a, b = S.canonical(a), S.canonical(b)
    if any(a.get(k) != b.get(k) for k in ("market", "side", "time")):
        return False
    ca, cb = sorted(a["entry"], key=lambda c: (c["feature"], c["op"])), sorted(b["entry"], key=lambda c: (c["feature"], c["op"]))
    if [(c["feature"], c["op"]) for c in ca] != [(c["feature"], c["op"]) for c in cb]:
        return False
    return all(abs(x["q"] - y["q"]) <= SIM_Q for x, y in zip(ca, cb))


def month_key(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m")


def shots_this_month(mem, now: datetime) -> int:
    k = month_key(now)
    return sum(1 for t in mem.holdout_tests() if str(t["holdout"].get("ts") or t.get("ts", ""))[:7] == k)


def next_shot(mem, evaluate, exclude=()):
    """Best untested, non-duplicate trial by robustness, or None. `evaluate(spec) -> {fold_sharpes, fold_sharpes_2x,
    trades}` fills in 2x-cost folds for old records (written back to the journal). Lazy greedy: 2x costs can only lower
    a score, so the best provisional candidate is re-scored until the top one is exact."""
    tested = [t["spec"] for t in mem.holdout_tests()] + list(exclude)
    heap = [(-robustness(t), t["n"]) for t in mem.scored() if not t.get("holdout") and robustness(t) > 0]
    heapq.heapify(heap)
    evals, upd = 0, {}
    try:
        while heap:
            _, n = heapq.heappop(heap)
            t = mem.trials[n - 1]
            if not has_2x(t):
                if evals >= MAX_EVALS:
                    continue
                evals += 1
                r = evaluate(t["spec"])
                if r:
                    t.update(r)
                    upd[n] = r
                    if robustness(t) > 0:
                        heapq.heappush(heap, (-robustness(t), n))
                continue
            if any(similar(t["spec"], x) for x in tested):
                continue
            return t
        return None
    finally:
        mem.update_many(upd)


def plan(mem, evaluate, k: int = K_PER_MONTH) -> list[dict]:
    """What the next k shots WOULD be (no held-out access): each pick excludes near-duplicates of the earlier picks."""
    out = []
    for _ in range(k):
        t = next_shot(mem, evaluate, [x["spec"] for x in out])
        if t is None:
            break
        out.append(t)
    return out


def spend(mem, market, evaluate, holdout_fn, now: datetime | None = None, log=lambda *a: None, emit=None, extra=None) -> list[int]:
    """Spend the remaining shots of this calendar month. market(spec) -> Market; holdout_fn(market, spec, hash) -> dict;
    extra(market, spec) -> dict merged into the held-out record (single-stock benchmark, lockbox confirmation)."""
    now = now or datetime.now(timezone.utc)
    spent = []
    while shots_this_month(mem, now) < K_PER_MONTH:
        t = next_shot(mem, evaluate)
        if t is None:
            break
        s, h = t["spec"], t["hash"]
        if emit:
            emit(s, 2, "busy", "one-shot held-out test", {"source": t.get("proposer"), "market": s["market"]})
        mk = market(s)
        ho = holdout_fn(mk, s, h) | (extra(mk, s) if extra else {}) | {"ts": now.isoformat(timespec="seconds"), "robustness": round(robustness(t), 3)}
        mem.update(t["n"], status="promoted", holdout=ho)
        log(f"  held-out shot #{t['n']} (robustness {robustness(t):.2f}): {S.describe(s)}")
        spent.append(t["n"])
    return spent
