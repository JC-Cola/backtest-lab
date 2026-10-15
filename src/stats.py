"""Metrics, coin-flip Monte Carlo, deflated Sharpe, Benjamini-Hochberg, walk-forward."""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

from engine import count_trades, returns_np


def sharpe_np(r, ann) -> float:
    r = np.asarray(r, float)
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return float(r.mean() / sd * math.sqrt(ann)) if sd > 1e-12 else 0.0


def cagr_np(r, ann) -> float:
    if len(r) == 0:
        return 0.0
    g = float(np.prod(1 + np.asarray(r)))
    return g ** (ann / len(r)) - 1 if g > 0 else -1.0


def metrics(r, s, trades, ann) -> dict:
    r = np.asarray(r, float)
    eq = np.cumprod(1 + r)
    full = np.r_[1.0, eq]
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    nt = len(trades["ret"])
    return {
        "cagr": cagr_np(r, ann),
        "vol": sd * math.sqrt(ann),
        "sharpe": sharpe_np(r, ann),
        "max_dd": float((full / np.maximum.accumulate(full) - 1).min()),
        "n_trades": nt,
        "win_rate": float((trades["ret"] > 0).mean()) if nt else None,
        "avg_hold_days": float(trades["hold"].mean()) if nt else None,
        "exposure": float((np.abs(np.nan_to_num(s)) > 0).mean()),
        "total_return": float(eq[-1] - 1) if len(eq) else 0.0,
    }


def select_best(sharpes, ntrades, min_trades) -> int:
    sh, nt = np.asarray(sharpes), np.asarray(ntrades)
    ok = nt >= min_trades
    pool = np.flatnonzero(ok) if ok.any() else np.arange(len(sh))
    return int(pool[np.argmax(sh[pool])])


def deflated_sharpe(r, trial_sharpes_daily) -> float | None:
    """Bailey & Lopez de Prado (2014). r: daily returns of the chosen trial; trial_sharpes_daily:
    per-period Sharpes of all grid trials. Returns P(true Sharpe > 0 | N trials)."""
    r = np.asarray(r, float)
    T = len(r)
    sd = r.std(ddof=1) if T > 2 else 0.0
    if sd < 1e-12:
        return None
    sr = r.mean() / sd
    z = (r - r.mean()) / sd
    skew, kurt = float(np.mean(z**3)), float(np.mean(z**4))
    n = len(trial_sharpes_daily)
    nd = NormalDist()
    sr0 = 0.0
    if n > 1:
        v = float(np.var(trial_sharpes_daily, ddof=1))
        g = 0.5772156649015329
        sr0 = math.sqrt(v) * ((1 - g) * nd.inv_cdf(1 - 1 / n) + g * nd.inv_cdf(1 - 1 / (n * math.e)))
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr**2))
    return nd.cdf((sr - sr0) * math.sqrt(T - 1) / denom)


def bh_qvalues(p) -> np.ndarray:
    p = np.asarray(p, float)
    m = len(p)
    if m == 0:
        return p
    o = np.argsort(p)
    q = p[o] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    out = np.empty(m)
    out[o] = np.clip(q, 0, 1)
    return out


def coin_flip(open_, trades, cost_bps, n_sims=2000, seed=0) -> dict | None:
    """Random-entry baseline. Same trade count, same (shuffled) holding durations and
    direction mix, random non-overlapping placement in the window. Value of a set of trades =
    sum(dir * (log open_exit - log open_entry)) - 2*cost per trade (size ignored)."""
    keep = trades["exit"] > trades["entry"]
    ent, ex, d = trades["entry"][keep], trades["exit"][keep], trades["dir"][keep]
    k, L = len(ent), len(open_)
    if k == 0:
        return None
    cum = np.log(np.asarray(open_, float))
    c2 = 2 * cost_bps / 1e4
    strat = float(np.sum(d * (cum[ex] - cum[ent])) - k * c2)
    h = ex - ent
    free = (L - 1) - int(h.sum())
    if free < 0:
        return None
    rng = np.random.default_rng(seed)
    perm = np.argsort(rng.random((n_sims, k)), axis=1)
    hp, dp = h[perm], d[perm]
    cuts = np.sort(rng.integers(0, free + 1, size=(n_sims, k)), axis=1)
    starts = cuts + np.cumsum(hp, axis=1) - hp
    vals = np.sum(dp * (cum[starts + hp] - cum[starts]), axis=1) - k * c2
    counts, edges = np.histogram(vals, bins=30)
    return {
        "p_value": (1 + int(np.sum(vals >= strat))) / (1 + n_sims),
        "strategy_value": strat,
        "percentiles": {str(q): float(np.percentile(vals, q)) for q in (5, 25, 50, 75, 95)},
        "hist": {"edges": edges.tolist(), "counts": counts.tolist()},
    }


def walk_forward(o, c, dates, pos_list, window, ann, cost_bps, min_trades=5) -> dict | None:
    """Rolling `window`-bar re-optimisation (best Sharpe), trade the next calendar year, roll yearly."""
    years = dates.year.values
    segs = []
    for y in np.unique(years):
        idx = np.flatnonzero(years == y)
        p0, p1 = idx[0], idx[-1] + 1
        if p0 < window or p1 - p0 < 20:
            continue
        tr = slice(p0 - window, p0)
        sh = [sharpe_np(returns_np(o[tr], c[tr], s[tr], cost_bps), ann) for s in pos_list]
        nt = [count_trades(s[tr]) for s in pos_list]
        best = pos_list[select_best(sh, nt, min_trades)]
        segs.append(returns_np(o[p0:p1], c[p0:p1], best[p0:p1], cost_bps))
    if not segs:
        return None
    r = np.concatenate(segs)
    return {"sharpe": sharpe_np(r, ann), "cagr": cagr_np(r, ann)}
