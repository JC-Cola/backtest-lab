"""Crypto study 9: weekly cross-sectional rotation (with its random-pick coin-flip) and the two hourly negative controls.
Rules as in CRYPTO_PLAN_9.md; shared constants and helpers come from crypto9_base."""
from __future__ import annotations

import numpy as np
import pandas as pd

from crypto9_base import (ANN, ANN_H, COST, IS_END, IS_START, MIN_IS_TRADES, N_SIMS, OOS_START, ROT_GRID, STRESS,
                          _seed, grid, is_risk_reducer, perf)
from engine import count_trades, extract_trades, returns_np
from indicators import ema
from stats import coin_flip, deflated_sharpe, metrics, select_best, sharpe_np

# ---------------------------------------------------------------------------------------------- rotation

class Panel:
    """Several coins on one daily calendar. O/C are filled (1.0 before listing); `listed` marks real bars."""

    def __init__(self, dfs):
        self.names = list(dfs)
        self.dates = pd.date_range(min(d.index[0] for d in dfs.values()), max(d.index[-1] for d in dfs.values()), freq="D")
        raw_c = pd.DataFrame({k: d.Close for k, d in dfs.items()}).reindex(self.dates)
        raw_o = pd.DataFrame({k: d.Open for k, d in dfs.items()}).reindex(self.dates)
        self.listed = raw_c.notna().values
        self.rawC = raw_c.values
        self.sma50 = raw_c.rolling(50).mean().values
        self.C = raw_c.ffill().fillna(1.0).values
        self.O = raw_o.where(raw_o.notna(), raw_c).ffill().fillna(1.0).values
        self.n, self.m = self.C.shape
        self.start = max(pd.Timestamp(IS_START), min(d.index[0] for d in dfs.values()) + pd.Timedelta(days=365))
        self.I = slice(int(self.dates.searchsorted(self.start)), int(self.dates.searchsorted(pd.Timestamp(IS_END), "right")))
        self.X = slice(int(self.dates.searchsorted(pd.Timestamp(OOS_START))), self.n)
        self.sundays = np.flatnonzero(np.asarray(self.dates.dayofweek) == 6)

    @property
    def tunable(self):
        return self.I.stop > self.I.start


def rotation_ranks(P, L, t):
    """Returns (coins in rank order best first, their L-week returns) for coins with an L-week return at close t."""
    j = t - 7 * L
    if j < 0:
        return np.array([], int), np.array([])
    ok = P.listed[t] & P.listed[j]
    idx = np.flatnonzero(ok)
    ret = P.rawC[t, idx] / P.rawC[j, idx] - 1
    o = np.argsort(-ret, kind="stable")
    return idx[o], ret[o]


def rotation_weights(P, L, k):
    """Weekly (Sunday close) decision: the top-k coins by L-week return each get 1/k, unless the coin is not above its own
    SMA(50) (then that slot is cash). Held until the next Sunday. Row t = the weights decided on close t."""
    W = np.zeros((P.n, P.m))
    cur = np.zeros(P.m)
    sun = set(P.sundays.tolist())
    for t in range(P.n):
        if t in sun:
            cur = np.zeros(P.m)
            order, _ = rotation_ranks(P, L, t)
            for i in order[:k]:
                if P.listed[t, i] and P.rawC[t, i] > P.sma50[t, i]:   # NaN SMA compares False
                    cur[i] = 1.0 / k
        W[t] = cur
    return W


def rot_returns(P, W, cost, sl):
    r = np.zeros(sl.stop - sl.start)
    for i in range(P.m):
        r += returns_np(P.O[sl, i], P.C[sl, i], W[sl, i], cost)
    return r


def rot_trades(P, W, cost, sl):
    ent, ret = [], []
    for i in range(P.m):
        t = extract_trades(P.O[sl, i], P.C[sl, i], W[sl, i], cost)
        ent += t["entry"].tolist()
        ret += t["ret"].tolist()
    o = np.argsort(ent, kind="stable")
    return {"entry": np.array(ent, int)[o], "ret": np.array(ret)[o]}


def rot_count(P, W, sl):
    return sum(count_trades(W[sl, i]) for i in range(P.m))


def rot_perf(P, W, cost, sl):
    r = rot_returns(P, W, cost, sl)
    t = rot_trades(P, W, cost, sl)
    gross = np.abs(W[sl]).sum(axis=1)
    m = perf(r, gross, {"ret": t["ret"], "hold": np.zeros(len(t["ret"]))})
    m["n_trades"] = rot_count(P, W, sl)
    m["trades_per_year"] = float(m["n_trades"] / (len(r) / ANN))
    m["avg_hold_days"] = None
    return m


def rot_stat(P, ws, d, pid, cost_frac):
    """Random-pick comparison statistic for weekly weight sets `ws` (S x J x m), decided on Sundays `d`.
    pid[day] = decision index in force for each test day; value = sum of log(1 + daily return) - turnover x cost."""
    S, J, m = ws.shape
    ret = np.vstack([np.zeros(m), P.C[1:] / P.C[:-1] - 1])
    ret[~P.listed] = 0.0
    ret[1:][~P.listed[:-1]] = 0.0
    days = ret[pid["days"]]                                         # T x m
    r = np.einsum("stm,tm->st", ws[:, pid["j"], :], days)
    turn = np.abs(np.diff(np.concatenate([np.zeros((S, 1, m)), ws], axis=1), axis=1)).sum(axis=(1, 2))
    return np.log1p(r).sum(axis=1) - turn * cost_frac


def rotation_coin_flip(P, W, k, L, seed):
    """Random-pick traders: each Sunday hold as many coins as the strategy does that week (1/k each), picked at random from
    the coins that have an L-week return; the same costs. p = (1 + #random >= strategy) / (1 + sims)."""
    X = P.X
    sun = P.sundays
    first = sun[sun <= X.start - 1].max() if (sun <= X.start - 1).any() else sun[0]
    d = sun[(sun >= first) & (sun <= P.n - 2)]
    j_of_day = np.searchsorted(d, np.arange(X.start, X.stop) - 1, side="right") - 1      # latest decision <= t-1
    days = np.arange(X.start, X.stop)
    keep = j_of_day >= 0
    pid = {"days": days[keep], "j": j_of_day[keep]}
    wk = W[d][None]                                               # strategy weights per decision, 1 x J x m
    n_j = (W[d] > 0).sum(axis=1)
    avail = np.zeros((len(d), P.m), bool)
    for q, t in enumerate(d):
        order, _ = rotation_ranks(P, L, t)
        avail[q, order] = True
    cost_frac = COST / 1e4
    strat = float(rot_stat(P, wk, d, pid, cost_frac)[0])
    rng = np.random.default_rng(seed)
    keys = rng.random((N_SIMS, len(d), P.m))
    keys[:, ~avail] = 2.0 + keys[:, ~avail]
    rank = np.argsort(np.argsort(keys, axis=2), axis=2)
    ws = (rank < n_j[None, :, None]).astype(float) / k
    ws[:, ~avail] = 0.0
    vals = rot_stat(P, ws, d, pid, cost_frac)
    return {"p_value": (1 + int(np.sum(vals >= strat))) / (1 + N_SIMS), "strategy_value": strat,
            "percentiles": {str(q): float(np.percentile(vals, q)) for q in (5, 25, 50, 75, 95)}}


def rot_buy_hold(P):
    """Equal weight of the coins listed, rebalanced daily, one entry cost (test years)."""
    W = np.zeros((P.n, P.m))
    for t in range(P.n):
        a = P.listed[t]
        if a.any():
            W[t, a] = 1.0 / a.sum()
    return W


def evaluate_rotation(P, label="ROT"):
    Ws = [rotation_weights(P, **p) for p in ROT_GRID]
    I, X = P.I, P.X
    is_r = [rot_returns(P, W, COST, I) for W in Ws]
    is_sh = [sharpe_np(r, ANN) for r in is_r]
    is_nt = [rot_count(P, W, I) for W in Ws]
    b = select_best(is_sh, is_nt, MIN_IS_TRADES)
    W = Ws[b]
    p = ROT_GRID[b]
    oos, stress = rot_perf(P, W, COST, X), rot_perf(P, W, STRESS, X)
    r_oos = rot_returns(P, W, COST, X)
    trial = [r.mean() / r.std(ddof=1) for r in is_r if r.std(ddof=1) > 1e-12]
    Wb = rot_buy_hold(P)
    bh = rot_perf(P, Wb, COST, X)
    bh["stress"] = rot_perf(P, Wb, STRESS, X)
    grid_oos = [rot_perf(P, w, COST, X) for w in Ws]
    cf = rotation_coin_flip(P, W, p["k"], p["L"], _seed("c9_rotation", label)) if oos["n_trades"] else None
    row = {"strategy": "c9_rotation", "asset": label, "params": p, "n_trials": len(Ws),
           "is": rot_perf(P, W, COST, I), "oos": oos, "oos_stress": stress, "oos_cost2x_sharpe": stress["sharpe"],
           "grid_median_oos_sharpe": float(np.median([g["sharpe"] for g in grid_oos])),
           "dsr_prob": deflated_sharpe(is_r[b], trial) if len(trial) > 1 else None,
           "dsr_test": deflated_sharpe(r_oos, np.array(trial)) if len(trial) > 1 else None,
           "coin_flip": cf, "buy_hold_oos": bh,
           "risk_reducer": is_risk_reducer(oos, bh), "risk_reducer_stress": is_risk_reducer(stress, bh["stress"]),
           "risk_reducer_grid": f"{sum(is_risk_reducer(g, bh) for g in grid_oos)} of {len(grid_oos)}",
           "universe": P.names,
           "windows": {"tune": [str(P.dates[I.start].date()), str(P.dates[I.stop - 1].date())],
                       "test": [str(P.dates[X.start].date()), str(P.dates[-1].date())]}}
    return row, Ws, b


# ---------------------------------------------------------------------------------------------- hourly controls

def ctl_hour_positions(index):
    """Hold 21:00-23:00 UTC: s_t = 1 on the bars starting 20:00 and 21:00 (decided on their close, filled at the next open)."""
    return np.isin(np.asarray(index.hour), (20, 21)).astype(float)


def ctl_macd_positions(h, d_close):
    """Hourly MACD(12,26,9) cross-up on a completed hourly candle, only if the daily MACD of the last COMPLETED UTC day is
    above its signal; filled at the next open; leave at the open after the first red hourly candle on or after the entry bar."""
    c, o = h.Close, h.Open.values
    line = ema(c, 12) - ema(c, 26)
    sig = line.ewm(span=9, adjust=False, min_periods=9).mean()
    cross = ((line > sig) & (line.shift(1) <= sig.shift(1))).values
    d = pd.DataFrame({"Close": d_close})
    dline = ema(d.Close, 12) - ema(d.Close, 26)
    dsig = dline.ewm(span=9, adjust=False, min_periods=9).mean()
    dflag = (dline > dsig)
    prev_day = h.index.floor("D") - pd.Timedelta(days=1)
    ok = dflag.reindex(prev_day).fillna(False).values.astype(bool)
    s = np.zeros(len(h))
    state = False
    for t in range(len(h)):
        if state:
            if h.Close.values[t] < o[t]:
                state = False
        elif cross[t] and ok[t]:
            state = True
        s[t] = 1.0 if state else 0.0
    return s


def evaluate_control(sid, h, s):
    """Fixed rule on BTC hourly bars, no tuning: test years at 0.2%, 0.5% and gross, coin-flip on the hourly opens."""
    o, c = h.Open.values.astype(float), h.Close.values.astype(float)
    ix = h.index
    X = slice(int(ix.searchsorted(pd.Timestamp(OOS_START))), len(h))
    I = slice(int(ix.searchsorted(pd.Timestamp(IS_START) + pd.Timedelta(days=0))), int(ix.searchsorted(pd.Timestamp(IS_END) + pd.Timedelta(days=1))))

    def pf(sl, cost):
        r = returns_np(o[sl], c[sl], s[sl], cost)
        m = metrics(r, s[sl], extract_trades(o[sl], c[sl], s[sl], cost), ANN_H)
        m["sharpe"] = sharpe_np(r, ANN_H)
        m["calmar"] = float(m["cagr"] / abs(m["max_dd"])) if m["max_dd"] < -1e-9 else None
        m["trades_per_year"] = float(m["n_trades"] / (len(r) / ANN_H))
        return m
    oos, stress, gross = pf(X, COST), pf(X, STRESS), pf(X, 0.0)
    cf = coin_flip(o[X], extract_trades(o[X], c[X], s[X], COST), COST, N_SIMS, _seed(sid, "BTC-USD"))
    return {"strategy": sid, "asset": "BTC-USD", "params": {}, "n_trials": 1, "is": pf(I, COST), "oos": oos, "oos_stress": stress,
            "oos_cost2x_sharpe": stress["sharpe"], "oos_gross_sharpe": gross["sharpe"], "coin_flip": cf,
            "grid_median_oos_sharpe": oos["sharpe"], "dsr_prob": None, "dsr_test": None, "control": True,
            "windows": {"tune": [str(ix[I.start].date()), str(ix[I.stop - 1].date())], "test": [str(ix[X.start].date()), str(ix[-1].date())]}}


CONTROLS = {"c9_ctl_hour": "Hold BTC only 21:00-23:00 UTC every day",
            "c9_ctl_macd": "Hourly MACD(12,26,9) cross-up filtered by daily MACD, exit on first red hourly candle"}
