"""Backtest engine.

Position s_t is decided on the CLOSE of bar t and traded at the OPEN of bar t+1:
  overnight_d = open_d/close_{d-1}-1 held with s_{d-2}
  intraday_d  = close_d/open_d-1     held with s_{d-1}
  r_d = (1+s_{d-2}*on_d)*(1+s_{d-1}*id_d) - 1 - cost*|s_{d-1}-s_{d-2}|
"""
from __future__ import annotations

import numpy as np


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    out = np.zeros_like(a)
    if len(a) > k:
        out[k:] = a[: len(a) - k]
    return out


def returns_np(o, c, s, cost_bps: float = 0.0) -> np.ndarray:
    """Daily net strategy returns (flat before the first bar)."""
    o = np.asarray(o, float)
    c = np.asarray(c, float)
    s = np.nan_to_num(np.asarray(s, float))
    p1, p2 = _shift(s, 1), _shift(s, 2)
    on = np.zeros(len(s))
    on[1:] = o[1:] / c[:-1] - 1
    idn = c / o - 1
    return (1 + p2 * on) * (1 + p1 * idn) - 1 - cost_bps / 1e4 * np.abs(p1 - p2)


def count_trades(s) -> int:
    sg = np.sign(np.nan_to_num(np.asarray(s, float)))
    prev = np.r_[0.0, sg[:-1]]
    return int(np.sum((sg != 0) & (sg != prev)))


def extract_trades(o, c, s, cost_bps: float = 0.0) -> dict:
    """Trade list from a position series: a trade = run of same-sign non-zero s.

    entry/exit are bar indices of the OPEN where it is entered/exited (exit is
    clipped to the last bar for trades still open). ret is net of costs.
    """
    o = np.asarray(o, float)
    c = np.asarray(c, float)
    s = np.nan_to_num(np.asarray(s, float))
    n = len(s)
    empty = {k: np.array([]) for k in ("entry", "exit", "dir", "size", "ret", "hold")}
    if n < 3:
        return empty
    sg = np.sign(s)
    change = sg != np.r_[0.0, sg[:-1]]
    tid = np.where(sg != 0, np.cumsum(change), 0).astype(int)
    p1, p2 = _shift(s, 1), _shift(s, 2)
    t1, t2 = _shift(tid, 1), _shift(tid, 2)
    on = np.zeros(n)
    on[1:] = o[1:] / c[:-1] - 1
    idn = c / o - 1
    m = n + 2
    legs = np.bincount(t2, weights=np.log1p(p2 * on), minlength=m)
    legs = legs + np.bincount(t1, weights=np.log1p(p1 * idn), minlength=m)
    cst = cost_bps / 1e4
    same = (t1 == t2) & (t1 > 0)
    costs = np.bincount(t1, weights=cst * np.abs(p1 - p2) * same, minlength=m)
    costs += np.bincount(t2, weights=cst * np.abs(p2) * ((t2 > 0) & ~same), minlength=m)
    costs += np.bincount(t1, weights=cst * np.abs(p1) * ((t1 > 0) & ~same), minlength=m)
    chg = np.flatnonzero(change)
    ent, ext, dr, sz, rt = [], [], [], [], []
    for j, st in enumerate(chg):
        if sg[st] == 0:
            continue
        last = (chg[j + 1] if j + 1 < len(chg) else n) - 1
        e = st + 1
        if e > n - 1:
            continue
        i = tid[st]
        ent.append(e)
        ext.append(min(last + 2, n - 1))
        dr.append(sg[st])
        sz.append(np.abs(s[st : last + 1]).mean())
        rt.append(np.expm1(legs[i]) - costs[i])
    ent, ext = np.array(ent, int), np.array(ext, int)
    return {"entry": ent, "exit": ext, "dir": np.array(dr), "size": np.array(sz),
            "ret": np.array(rt), "hold": ext - ent}
