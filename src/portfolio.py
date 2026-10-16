"""Multi-asset engine for rotation / cross-sectional strategies (Quantpedia batch).

Same timing as the single-asset engine: a weight row decided on the close of bar t is traded at
the open of bar t+1. Portfolio return = sum over assets of the single-asset engine return with the
weight as position size (weights are held constant between decisions, i.e. no intra-period drift;
applied identically to the strategy and to its random baseline). Costs are charged per unit of
weight change, per asset.

Coin-flip baseline for portfolios: keep the strategy's rebalance dates and its weight vector at
each rebalance, but hand those weights to randomly chosen assets among those that had started
trading. Same number of holdings, same gross/net exposure, same turnover regime. p = share of
2,000 random portfolios that did at least as well.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import calendar_util as cu
import data
from engine import returns_np

# Yahoo quotes these as foreign units per USD; invert so every FX series is the USD value of
# one unit of foreign currency (long = long that currency against the dollar).
INVERT = {"JPY=X", "CAD=X", "CHF=X", "SEK=X", "NOK=X", "DKK=X", "SGD=X", "MXN=X", "ZAR=X"}


@dataclass
class Panel:
    O: pd.DataFrame
    H: pd.DataFrame
    L: pd.DataFrame
    C: pd.DataFrame
    started: pd.DataFrame  # True once the asset has a first price (known on that date)

    @property
    def index(self):
        return self.C.index

    @property
    def tickers(self):
        return list(self.C.columns)


def _invert(df):
    return pd.DataFrame({"Open": 1 / df.Open, "High": 1 / df.Low, "Low": 1 / df.High, "Close": 1 / df.Close},
                        index=df.index)


def load_panel(tickers, start=None, end=None) -> Panel:
    frames = {}
    for t in tickers:
        df = data.load(t)
        frames[t] = _invert(df) if t in INVERT else df
    idx = frames[tickers[0]].index
    for t in tickers[1:]:
        idx = idx.union(frames[t].index)
    if start:
        idx = idx[idx >= pd.Timestamp(start)]
    if end:
        idx = idx[idx <= pd.Timestamp(end)]
    col = lambda k: pd.DataFrame({t: frames[t][k].reindex(idx) for t in tickers}, index=idx)
    C = col("Close")
    traded = C.notna()
    Cf = C.ffill()
    # A market closed on a date in the union index: open = high = low = close = previous close.
    O, H, L = (col(k).where(traded, Cf) for k in ("Open", "High", "Low"))
    return Panel(O, H, L, Cf, Cf.notna())


def month_end_mask(idx) -> np.ndarray:
    ext = cu.extended_index(idx)
    _, e = cu.month_ranks(ext)
    return e[: len(idx)] == 0


def hold_rows(idx, rows: dict) -> pd.DataFrame:
    """rows: {bar position -> weight Series}. Forward-fill decisions between rebalances."""
    W = pd.DataFrame(np.nan, index=idx, columns=next(iter(rows.values())).index if rows else [])
    for p, w in rows.items():
        W.iloc[p] = w.values
    return W.ffill().fillna(0.0)


def finalize(W: pd.DataFrame, P: Panel) -> pd.DataFrame:
    W = W.reindex(index=P.index, columns=P.tickers).fillna(0.0)
    return W.where(P.started, 0.0)


def portfolio_returns(P: Panel, W: pd.DataFrame, cost_bps: float, sl=slice(None)) -> np.ndarray:
    O, C, Wv = P.O.values[sl], P.C.values[sl], W.values[sl]
    r = np.zeros(len(O))
    for j in range(Wv.shape[1]):
        w = Wv[:, j]
        if not np.any(w):
            continue
        o, c = np.nan_to_num(O[:, j], nan=1.0), np.nan_to_num(C[:, j], nan=1.0)
        r += returns_np(o, c, w, cost_bps)
    return r


def blocks(Wv: np.ndarray):
    """(start, end) bar positions of maximal runs of identical weight rows."""
    T = len(Wv)
    if T == 0:
        return np.array([], int), np.array([], int)
    ch = np.r_[True, np.any(Wv[1:] != Wv[:-1], axis=1)]
    st = np.flatnonzero(ch)
    return st, np.r_[st[1:], T]


def pseudo_trades(P: Panel, W: pd.DataFrame, sl, cost_bps):
    """Each non-empty rebalance block counts as one 'trade' for trade counts and win rate."""
    Wv = W.values[sl]
    O = P.O.values[sl]
    st, en = blocks(Wv)
    rets, holds = [], []
    for s, e in zip(st, en):
        w = Wv[s]
        if not np.any(w):
            continue
        a, b = s + 1, min(e + 1, len(O) - 1)
        if b <= a:
            continue
        lr = np.log(O[b] / O[a])
        rets.append(float(np.nansum(w * np.nan_to_num(lr))))
        holds.append(b - a)
    return {"ret": np.array(rets), "hold": np.array(holds)}


def n_rebalances(W: pd.DataFrame, sl) -> int:
    st, _ = blocks(W.values[sl])
    return int(sum(1 for s in st if np.any(W.values[sl][s])))


def coin_flip_portfolio(P: Panel, W: pd.DataFrame, sl, cost_bps, n_sims=2000, seed=0) -> dict | None:
    Wv = W.values[sl]
    O = P.O.values[sl]
    avail = P.started.values[sl]
    T, N = Wv.shape
    st, en = blocks(Wv)
    logO = np.log(np.where(np.isfinite(O) & (O > 0), O, np.nan))
    c1 = cost_bps / 1e4
    rng = np.random.default_rng(seed)
    strat = 0.0
    vals = np.zeros(n_sims)
    prev_s = np.zeros(N)
    prev_r = np.zeros((n_sims, N))
    used = 0
    for s, e in zip(st, en):
        a, b = s + 1, min(e + 1, T - 1)
        if b <= a:
            continue
        w = Wv[s]
        R = np.nan_to_num(logO[b] - logO[a])
        A = np.flatnonzero(avail[a])
        strat += float(w @ R) - c1 * float(np.abs(w - prev_s).sum())
        prev_s = w
        cur = np.zeros((n_sims, N))
        if len(A):
            wa = np.tile(w[A], (n_sims, 1))
            cur[:, A] = rng.permuted(wa, axis=1)
        vals += cur @ R - c1 * np.abs(cur - prev_r).sum(axis=1)
        prev_r = cur
        if np.any(w):
            used += 1
    if used == 0:
        return None
    counts, edges = np.histogram(vals, bins=30)
    return {
        "p_value": (1 + int(np.sum(vals >= strat - 1e-12))) / (1 + n_sims),
        "strategy_value": strat,
        "percentiles": {str(q): float(np.percentile(vals, q)) for q in (5, 25, 50, 75, 95)},
        "hist": {"edges": edges.tolist(), "counts": counts.tolist()},
        "baseline": "random assets with the same weights at the same rebalance dates",
    }


def equal_weight(P: Panel) -> pd.DataFrame:
    """Benchmark: equal weight across every asset that has started trading."""
    s = P.started.astype(float)
    return s.div(s.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
