"""Point-in-time feature cube for the intraday agent. cube[d, i, f] is the value of FEATURES[f] when
deciding on the CLOSE of bar m-1 (m = DECISIONS[i], minutes after 09:30) on day d; the trade fills at
P[d, m]. Only bars < m of day d and earlier days are used. NaN where not computable (warm-up, or
half days that end before m)."""
from __future__ import annotations

import numpy as np

DECISIONS = list(range(30, 361, 30))   # 10:00 .. 15:30 every half hour
FEATURES = ["gap", "ret_open", "ret_prev_close", "ret_30", "vwap_dist", "range_pos", "noise_z",
            "vol_ratio", "prev_day_ret", "prev_day_ibs", "rv_5d",
            # day-trader scanner setup families (appended: the older features keep their positions)
            "orb5", "orb15", "orb30",                       # opening-range breakout: distance beyond the 5/15/30-min range
            "run_10d", "range_5d", "consol_30", "hi20_dist",  # bull flag / consolidation breakout
            "rsi_14d", "z_20d",                             # oversold bounce
            "gap_fill", "rvol_30"]                          # gap-and-go / gap-fade, relative-volume spike
SCANNER_FEATURES = FEATURES[11:]
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


def _rsi_prior(close: np.ndarray, n: int = 14) -> np.ndarray:
    """out[d] = Wilder RSI of the daily closes up to and including day d-1 (the scanner's rsi, vectorised over days)."""
    out = np.full(len(close), np.nan)
    ok = np.flatnonzero(np.isfinite(close))
    if len(ok) < n + 2:
        return out
    c = close[ok]
    ch = np.diff(c)
    g, lo = np.maximum(ch, 0), np.maximum(-ch, 0)
    ag, al = g[:n].mean(), lo[:n].mean()
    r = np.full(len(c), np.nan)
    for i in range(n, len(ch) + 1):                      # r[i] uses changes up to ch[i-1], i.e. closes up to c[i]
        if i > n:
            ag, al = (ag * (n - 1) + g[i - 1]) / n, (al * (n - 1) + lo[i - 1]) / n
        r[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    out[ok[1:]] = r[:-1]                                 # shift: day d sees the RSI as of the close of d-1
    return out


def _roll(x: np.ndarray, k: int, fn) -> np.ndarray:
    """out[d] = fn(x[d-k:d]) (window excludes day d; NaN until k earlier days)."""
    out = np.full(len(x), np.nan)
    if len(x) > k:
        w = np.lib.stride_tricks.sliding_window_view(x, k)[:len(x) - k]
        out[k:] = fn(w, axis=1)
    return out


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
    # daily-history features of the scanner (day d sees closes up to d-1 only)
    cl = D.close
    run10 = _div(sh(cl, 6), sh(cl, 16)) - 1                         # up-move over the 10 sessions before the last 5
    rng5 = _div(_roll(cl, 5, np.max) - _roll(cl, 5, np.min), sh(cl, 1))   # tightness of the last 5 closes
    hi20 = _roll(cl, 20, np.max)
    mean20, std20 = _roll(cl, 20, np.mean), _roll(cl, 20, np.std)
    rsi = _rsi_prior(cl)
    rng_cum = cmax - cmin
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
        # opening range: first k minutes (bars 0..k-1, all complete before the decision close of bar m-1 since m >= 30)
        orb = []
        for k in (5, 15, 30):
            hi, lo = cmax[:, k - 1], cmin[:, k - 1]
            orb.append(np.where(c > hi, _div(c - hi, hi), np.where(c < lo, _div(c - lo, lo), 0.0)))
        c30 = D.C[:, m - 31] if m - 31 >= 0 else O0
        win_h, win_l = D.H[:, m - 30:m].max(axis=1), D.L[:, m - 30:m].min(axis=1)
        gdist = O0 - D.prev_close
        v30 = cumv[:, m - 1] - (cumv[:, m - 31] if m - 31 >= 0 else 0.0)
        f += orb + [run10, rng5, _div(win_h - win_l, c), _div(c, hi20) - 1, rsi, _div(c - mean20, std20),
                    np.where(np.abs(gap) > 0.001, _div(O0 - c, gdist), np.nan), _div(v30, _prev_mean(v30))]
        cube[:, i, :] = np.stack(f, axis=1)
        cube[m > last, i, :] = np.nan   # the fill price P[d, m] must be a real bar open
    cube[~np.isfinite(cube)] = np.nan
    return cube
