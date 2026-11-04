"""Point-in-time feature cube for the intraday agent. cube[d, i, f] is the value of FEATURES[f] when
deciding on the CLOSE of bar m-1 (m = DECISIONS[i], minutes after 09:30) on day d; the trade fills at
P[d, m]. Only bars < m of day d and earlier days are used. NaN where not computable (warm-up, or
half days that end before m)."""
from __future__ import annotations

import numpy as np

DECISIONS = [30, 60, 90, 120, 180, 240, 300, 330, 360]
FEATURES = ["gap", "ret_open", "ret_prev_close", "ret_30", "vwap_dist", "range_pos", "noise_z",
            "vol_ratio", "prev_day_ret", "prev_day_ibs", "rv_5d"]
LOOK = 14  # days for the noise band and the volume baseline


def _prev_mean(x: np.ndarray, k: int = LOOK) -> np.ndarray:
    """out[d] = mean of x[d-k:d] (excludes day d); NaN until k earlier days exist."""
    c = np.r_[0.0, np.cumsum(x)]
    out = np.full(len(x), np.nan)
    out[k:] = (c[k:len(x)] - c[:len(x) - k]) / k
    return out


def _div(a, b):
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(b != 0, a / b, np.nan)


def build(D) -> np.ndarray:
    n, last = D.n, D.last
    cmin, cmax = np.minimum.accumulate(D.L, axis=1), np.maximum.accumulate(D.H, axis=1)
    cumv = np.cumsum(D.V, axis=1)
    O0 = D.O[:, 0]
    # previous-day columns (day d-1 / d-2 known at today's open)
    inside = np.arange(390)[None, :] <= last[:, None]
    dh = np.where(inside, D.H, -np.inf).max(axis=1)
    dl = np.where(inside, D.L, np.inf).min(axis=1)
    sh = lambda a, k: np.r_[np.full(k, np.nan), a[:n - k]]
    dr = _div(D.close, sh(D.close, 1)) - 1   # close-to-close return of day d
    prev_ret, prev_ibs = sh(dr, 1), sh(_div(D.close - dl, dh - dl), 1)
    rv5 = np.full(n, np.nan)
    for d in range(6, n):
        rv5[d] = np.std(dr[d - 5:d], ddof=1)
    gap = _div(O0, D.prev_close) - 1
    cube = np.full((n, len(DECISIONS), len(FEATURES)), np.nan)
    for i, m in enumerate(DECISIONS):
        c = D.C[:, m - 1]
        ret_open = _div(c, O0) - 1
        ref = D.C[:, m - 31] if m - 31 >= 0 else O0
        sigma = _prev_mean(np.abs(ret_open))
        vol = cumv[:, m - 1]
        f = [gap, ret_open, _div(c, D.prev_close) - 1, _div(c, ref) - 1, _div(c, D.vwap[:, m - 1]) - 1,
             _div(c - cmin[:, m - 1], cmax[:, m - 1] - cmin[:, m - 1]), _div(ret_open, sigma),
             _div(vol, _prev_mean(vol)), prev_ret, prev_ibs, rv5]
        cube[:, i, :] = np.stack(f, axis=1)
        cube[m > last, i, :] = np.nan   # the fill price P[d, m] must be a real bar open
    cube[~np.isfinite(cube)] = np.nan
    return cube
