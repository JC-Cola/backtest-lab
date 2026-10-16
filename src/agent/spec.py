"""Strategy specs: a small rule grammar the agent writes and the platform can compile and check.

spec = {
  "market": "SPY",
  "entry":  [{"feature": "rsi_2", "op": "<", "q": 10}, ...],   # 1-3 conditions, all must hold
  "filter": {"feature": "vix_ratio", "op": "<", "q": 80} | None, # regime condition, also required
  "exit":   {"type": "hold", "n": 5}
          | {"type": "cond", "cond": {"feature": "ibs", "op": ">", "q": 70}, "max_hold": 10}
}
Thresholds are written as percentiles q (1-99) of the feature's distribution in the SEARCH window
only; the platform converts them to values. Long-only (flat otherwise).
"""
from __future__ import annotations

import copy
import hashlib
import json
import random

import numpy as np
import pandas as pd

from agent.features import FEATURES

MARKETS = ["SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "BTC-USD"]
OPS = ("<", ">")


class SpecError(ValueError):
    pass


def _cond_ok(c):
    if not isinstance(c, dict) or set(c) != {"feature", "op", "q"}:
        raise SpecError(f"condition must have exactly feature/op/q: {c}")
    if c["feature"] not in FEATURES:
        raise SpecError(f"unknown feature {c['feature']!r}")
    if c["op"] not in OPS:
        raise SpecError(f"op must be < or >: {c['op']!r}")
    if not (isinstance(c["q"], (int, float)) and 1 <= c["q"] <= 99):
        raise SpecError(f"q must be a percentile 1-99: {c['q']!r}")


def validate(s: dict) -> dict:
    if not isinstance(s, dict):
        raise SpecError("spec must be an object")
    extra = set(s) - {"market", "entry", "filter", "exit"}
    if extra:
        raise SpecError(f"unknown keys {sorted(extra)}")
    if s.get("market") not in MARKETS:
        raise SpecError(f"market must be one of {MARKETS}")
    ent = s.get("entry")
    if not isinstance(ent, list) or not 1 <= len(ent) <= 3:
        raise SpecError("entry must be a list of 1-3 conditions")
    for c in ent:
        _cond_ok(c)
    if s.get("filter") is not None:
        _cond_ok(s["filter"])
    ex = s.get("exit")
    if not isinstance(ex, dict) or ex.get("type") not in ("hold", "cond"):
        raise SpecError("exit.type must be hold or cond")
    if ex["type"] == "hold":
        if set(ex) != {"type", "n"} or not (isinstance(ex["n"], int) and 1 <= ex["n"] <= 60):
            raise SpecError("hold exit needs integer n in 1-60")
    else:
        if set(ex) != {"type", "cond", "max_hold"}:
            raise SpecError("cond exit needs cond and max_hold")
        _cond_ok(ex["cond"])
        if not (isinstance(ex["max_hold"], int) and 1 <= ex["max_hold"] <= 60):
            raise SpecError("max_hold must be an integer 1-60")
    return s


def canonical(s: dict) -> dict:
    s = copy.deepcopy(s)
    for c in s["entry"] + ([s["filter"]] if s.get("filter") else []) + \
            ([s["exit"]["cond"]] if s["exit"]["type"] == "cond" else []):
        c["q"] = int(round(c["q"]))
    # merge redundant conditions on the same feature and direction (keep the stricter one)
    best = {}
    for c in s["entry"]:
        k = (c["feature"], c["op"])
        if k not in best or (c["q"] < best[k]["q"] if c["op"] == "<" else c["q"] > best[k]["q"]):
            best[k] = c
    s["entry"] = sorted(best.values(), key=lambda c: (c["feature"], c["op"], c["q"]))
    s.setdefault("filter", None)
    return s


def spec_hash(s: dict) -> str:
    return hashlib.sha1(json.dumps(canonical(s), sort_keys=True).encode()).hexdigest()[:12]


def describe(s: dict) -> str:
    cd = lambda c: f"{c['feature']} {c['op']} p{int(c['q'])}"
    txt = f"{s['market']}: buy when " + " and ".join(cd(c) for c in s["entry"])
    if s.get("filter"):
        txt += f" (only if {cd(s['filter'])})"
    ex = s["exit"]
    txt += f"; hold {ex['n']} days" if ex["type"] == "hold" else \
        f"; sell when {cd(ex['cond'])} or after {ex['max_hold']} days"
    return txt


# ------------------------------------------------------------------ compile
def thresholds(table: pd.DataFrame, search: slice) -> dict:
    """Percentile grid per feature from the SEARCH window only (no held-out data)."""
    w = table.iloc[search]
    return {k: np.nanpercentile(w[k].values, np.arange(0, 101)) if w[k].notna().sum() > 50 else None
            for k in table.columns}


def _mask(table, th, c):
    grid = th.get(c["feature"])
    x = table[c["feature"]].values
    if grid is None:
        return np.zeros(len(x), bool)
    v = grid[int(round(c["q"]))]
    with np.errstate(invalid="ignore"):
        return (x < v) if c["op"] == "<" else (x > v)


def positions(s: dict, table: pd.DataFrame, th: dict) -> np.ndarray:
    enter = np.ones(len(table), bool)
    for c in s["entry"]:
        enter &= _mask(table, th, c)
    if s.get("filter"):
        enter &= _mask(table, th, s["filter"])
    ex = s["exit"]
    out_sig = _mask(table, th, ex["cond"]) if ex["type"] == "cond" else np.zeros(len(table), bool)
    max_hold = ex["n"] if ex["type"] == "hold" else ex["max_hold"]
    out = np.zeros(len(table))
    pos, age = 0, 0
    for t in range(len(table)):
        if pos:
            age += 1
            if age >= max_hold or out_sig[t]:
                pos = 0
        if not pos and enter[t]:
            pos, age = 1, 0
        out[t] = pos
    return out


def features_used(s: dict) -> list[str]:
    fs = [c["feature"] for c in s["entry"]]
    if s.get("filter"):
        fs.append(s["filter"]["feature"])
    if s["exit"]["type"] == "cond":
        fs.append(s["exit"]["cond"]["feature"])
    return sorted(set(fs))


# ------------------------------------------------------------------ variation operators
def random_cond(rng: random.Random, weights: dict | None = None):
    feats = list(FEATURES)
    w = [weights.get(f, 1.0) if weights else 1.0 for f in feats]
    return {"feature": rng.choices(feats, w)[0], "op": rng.choice(OPS), "q": rng.choice([5, 10, 20, 30, 50, 70, 80, 90, 95])}


def random_spec(rng: random.Random, weights=None, market=None) -> dict:
    s = {"market": market or rng.choice(MARKETS),
         "entry": [random_cond(rng, weights) for _ in range(rng.choice([1, 1, 2, 2, 3]))],
         "filter": random_cond(rng, weights) if rng.random() < 0.35 else None,
         "exit": {"type": "hold", "n": rng.choice([1, 2, 3, 5, 10, 20])} if rng.random() < 0.5 else
                 {"type": "cond", "cond": random_cond(rng, weights), "max_hold": rng.choice([3, 5, 10, 20])}}
    return validate(s)


def mutate(s: dict, rng: random.Random, weights=None) -> dict:
    s = copy.deepcopy(s)
    move = rng.choice(["q", "q", "feature", "op", "add", "drop", "filter", "exit"])
    conds = s["entry"]
    if move == "q":
        c = rng.choice(conds + ([s["filter"]] if s.get("filter") else []))
        c["q"] = int(min(99, max(1, c["q"] + rng.choice([-15, -10, -5, 5, 10, 15]))))
    elif move == "feature":
        rng.choice(conds)["feature"] = random_cond(rng, weights)["feature"]
    elif move == "op":
        c = rng.choice(conds)
        c["op"] = "<" if c["op"] == ">" else ">"
    elif move == "add" and len(conds) < 3:
        conds.append(random_cond(rng, weights))
    elif move == "drop" and len(conds) > 1:
        conds.pop(rng.randrange(len(conds)))
    elif move == "filter":
        s["filter"] = None if s.get("filter") else random_cond(rng, weights)
    else:
        s["exit"] = {"type": "hold", "n": rng.choice([1, 2, 3, 5, 10, 20])} if rng.random() < 0.5 else \
            {"type": "cond", "cond": random_cond(rng, weights), "max_hold": rng.choice([3, 5, 10, 20])}
    return validate(s)


def crossover(a: dict, b: dict, rng: random.Random) -> dict:
    if a["market"] != b["market"]:
        return copy.deepcopy(a)
    pool = a["entry"] + b["entry"]
    s = {"market": a["market"], "entry": copy.deepcopy(rng.sample(pool, min(len(pool), rng.choice([1, 2, 3])))),
         "filter": copy.deepcopy(rng.choice([a.get("filter"), b.get("filter")])),
         "exit": copy.deepcopy(rng.choice([a["exit"], b["exit"]]))}
    return validate(s)
