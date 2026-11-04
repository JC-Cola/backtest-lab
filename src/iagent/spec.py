"""Intraday strategy specs: a small rule grammar the agent writes and the platform can compile.

spec = {
  "market": "SPY" | "QQQ",
  "time":   30 | 60 | 90 | 120 | 180 | 240 | 300 | 330 | 360,   # minutes after 09:30; decide on the close of bar m-1, fill at P[m]
  "side":   "long" | "short",
  "entry":  [{"feature": "vwap_dist", "op": ">", "q": 90}, ...],  # 1-3 conditions, all must hold
  "exit":   {"type": "hold", "n": 15|30|60|120} | {"type": "close"},
  "stop_bp": null | 25 | 50 | 100
}
Thresholds are percentiles q (1-99) of the feature at that decision time over the SEARCH window only.
One trade at most per day. Costs: 1 bp per side (intraday.COST).
"""
from __future__ import annotations

import copy
import hashlib
import json
import random

import numpy as np

import intraday as I
from iagent.features import DECISIONS, FEATURES

MARKETS = ["SPY", "QQQ"]
OPS = ("<", ">")
SIDES = ("long", "short")
HOLDS = (15, 30, 60, 120)
STOPS = (None, 25, 50, 100)


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
    extra = set(s) - {"market", "time", "side", "entry", "exit", "stop_bp"}
    if extra:
        raise SpecError(f"unknown keys {sorted(extra)}")
    if s.get("market") not in MARKETS:
        raise SpecError(f"market must be one of {MARKETS}")
    if s.get("time") not in DECISIONS:
        raise SpecError(f"time must be one of {DECISIONS}")
    if s.get("side") not in SIDES:
        raise SpecError("side must be long or short")
    ent = s.get("entry")
    if not isinstance(ent, list) or not 1 <= len(ent) <= 3:
        raise SpecError("entry must be a list of 1-3 conditions")
    for c in ent:
        _cond_ok(c)
    ex = s.get("exit")
    if not isinstance(ex, dict) or ex.get("type") not in ("hold", "close"):
        raise SpecError("exit.type must be hold or close")
    if ex["type"] == "hold" and (set(ex) != {"type", "n"} or ex["n"] not in HOLDS):
        raise SpecError(f"hold exit needs n in {HOLDS}")
    if ex["type"] == "close" and set(ex) != {"type"}:
        raise SpecError("close exit takes no parameters")
    if s.get("stop_bp") not in STOPS:
        raise SpecError(f"stop_bp must be one of {STOPS}")
    return s


def canonical(s: dict) -> dict:
    s = copy.deepcopy(s)
    for c in s["entry"]:
        c["q"] = int(round(c["q"]))
    best = {}  # merge redundant conditions on the same feature and direction (keep the stricter one)
    for c in s["entry"]:
        k = (c["feature"], c["op"])
        if k not in best or (c["q"] < best[k]["q"] if c["op"] == "<" else c["q"] > best[k]["q"]):
            best[k] = c
    s["entry"] = sorted(best.values(), key=lambda c: (c["feature"], c["op"], c["q"]))
    s.setdefault("stop_bp", None)
    return s


def spec_hash(s: dict) -> str:
    return hashlib.sha1(json.dumps(canonical(s), sort_keys=True).encode()).hexdigest()[:12]


def clock(m: int) -> str:
    return f"{(570 + m) // 60:02d}:{(570 + m) % 60:02d}"


def describe(s: dict) -> str:
    cd = lambda c: f"{c['feature']} {c['op']} p{int(c['q'])}"
    ex = s["exit"]
    txt = f"{s['market']} at {clock(s['time'])}: {s['side']} when " + " and ".join(cd(c) for c in s["entry"])
    txt += f"; hold {ex['n']} min" if ex["type"] == "hold" else "; hold to the close"
    return txt + (f"; stop {s['stop_bp']} bp" if s.get("stop_bp") else "")


def features_used(s: dict) -> list[str]:
    return sorted({c["feature"] for c in s["entry"]})


# ------------------------------------------------------------------ compile
def thresholds(cube: np.ndarray, search: slice) -> dict:
    """Percentile grid per (decision minute, feature) from the SEARCH window only."""
    th = {}
    for i, m in enumerate(DECISIONS):
        for j, f in enumerate(FEATURES):
            x = cube[search, i, j]
            x = x[~np.isnan(x)]
            th[(m, f)] = np.percentile(x, np.arange(0, 101)) if len(x) > 50 else None
    return th


def entry_mask(s: dict, cube: np.ndarray, th: dict, sl: slice | None = None) -> np.ndarray:
    """Days (within sl) where every entry condition holds at the spec's decision time."""
    cs = cube if sl is None else cube[sl]
    i = DECISIONS.index(s["time"])
    ok = np.ones(len(cs), bool)
    for c in s["entry"]:
        grid = th.get((s["time"], c["feature"]))
        if grid is None:
            return np.zeros(len(cs), bool)
        x, v = cs[:, i, FEATURES.index(c["feature"])], grid[int(round(c["q"]))]
        with np.errstate(invalid="ignore"):
            ok &= (x < v) if c["op"] == "<" else (x > v)
    return ok


def trades(s: dict, D, cube: np.ndarray, th: dict, sl: slice | None = None) -> dict:
    """Trades dict (day, entry_m, exit_m, dir, ret) for the whole history, or only days in `sl`.
    Entry at P[d, m]; hold exits at P[d, min(m+n, last+1)], close exits at P[d, last+1]. A stop exits
    at its level when the bar's L (long) / H (short) crosses it, or at the bar's open if it opens
    through it (never on the entry bar's open). Net of 2*COST."""
    m, dr = s["time"], 1.0 if s["side"] == "long" else -1.0
    off = sl.start if sl is not None and sl.start else 0
    d = np.flatnonzero(entry_mask(s, cube, th, sl)) + off
    if len(d) == 0:
        return {"day": np.array([], int), "entry_m": np.array([], int), "exit_m": np.array([], int),
                "dir": np.array([]), "ret": np.array([])}
    ex = s["exit"]
    end = D.last[d] + 1
    j = np.minimum(m + ex["n"], end) if ex["type"] == "hold" else end
    p_in, p_out = D.P[d, m], D.P[d, j]
    exit_m = j.copy()
    if s.get("stop_bp"):
        stop = p_in * (1 - dr * s["stop_bp"] / 1e4)
        k = m + np.arange(int((j - m).max()))[None, :]          # bars m .. j-1 held through
        kk = np.minimum(k, 389)
        valid = k < j[:, None]
        op, ex_px = D.O[d[:, None], kk], (D.L if dr > 0 else D.H)[d[:, None], kk]
        s_ = stop[:, None]
        thru = (k > m) & ((op <= s_) if dr > 0 else (op >= s_))
        cross = (ex_px <= s_) if dr > 0 else (ex_px >= s_)
        hit = valid & (thru | cross)
        has, f = hit.any(axis=1), hit.argmax(axis=1)
        r = np.arange(len(d))
        px = np.where(thru[r, f], op[r, f], stop)
        p_out = np.where(has, px, p_out)
        exit_m = np.where(has, np.maximum(m + f, m + 1), j)
    return {"day": d, "entry_m": np.full(len(d), m), "exit_m": exit_m, "dir": np.full(len(d), dr),
            "ret": I._ret(dr, p_in, p_out)}


# ------------------------------------------------------------------ variation operators
def random_cond(rng: random.Random, weights: dict | None = None):
    w = [weights.get(f, 1.0) if weights else 1.0 for f in FEATURES]
    return {"feature": rng.choices(FEATURES, w)[0], "op": rng.choice(OPS), "q": rng.choice([5, 10, 20, 30, 50, 70, 80, 90, 95])}


def _rand_exit(rng):
    return {"type": "hold", "n": rng.choice(HOLDS)} if rng.random() < 0.7 else {"type": "close"}


def random_spec(rng: random.Random, weights=None, market=None) -> dict:
    s = {"market": market or rng.choice(MARKETS), "time": rng.choice(DECISIONS), "side": rng.choice(SIDES),
         "entry": [random_cond(rng, weights) for _ in range(rng.choice([1, 1, 2, 2, 3]))],
         "exit": _rand_exit(rng), "stop_bp": rng.choice(STOPS)}
    return validate(s)


def mutate(s: dict, rng: random.Random, weights=None) -> dict:
    s = copy.deepcopy(s)
    move = rng.choice(["q", "q", "feature", "op", "add", "drop", "time", "side", "exit", "stop"])
    conds = s["entry"]
    if move == "q":
        c = rng.choice(conds)
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
    elif move == "time":
        s["time"] = rng.choice(DECISIONS)
    elif move == "side":
        s["side"] = "short" if s["side"] == "long" else "long"
    elif move == "stop":
        s["stop_bp"] = rng.choice(STOPS)
    else:
        s["exit"] = _rand_exit(rng)
    return validate(s)


def crossover(a: dict, b: dict, rng: random.Random) -> dict:
    if a["market"] != b["market"]:
        return copy.deepcopy(a)
    pool = a["entry"] + b["entry"]
    pick = lambda k: copy.deepcopy(rng.choice([a, b])[k])
    s = {"market": a["market"], "time": pick("time"), "side": pick("side"),
         "entry": copy.deepcopy(rng.sample(pool, min(len(pool), rng.choice([1, 2, 3])))),
         "exit": pick("exit"), "stop_bp": rng.choice([a, b]).get("stop_bp")}
    return validate(s)
