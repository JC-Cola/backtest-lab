"""Data platform + backtest for the agent. Turns a spec into a point-in-time table, checks it,
scores it on the SEARCH window only, and runs the one-shot held-out gate when asked.

Lessons from the guide this implements:
- one clock: every feature is known at the decision close; trades fill at the next open;
- coverage is reported, not hidden: a spec whose features are missing on much of the search
  window is rejected rather than silently scored on the rows that happen to exist;
- the score is deflated by how many trials the agent has run (more searching = higher bar);
- the held-out window is never part of the score the agent optimises.
"""
from __future__ import annotations

import math
import zlib

import numpy as np
import pandas as pd

import data
from agent import features as F
from agent import spec as S
from engine import count_trades, extract_trades, returns_np
from run import CONFIG, SEED
from stats import coin_flip, deflated_sharpe, sharpe_np

MIN_TRADES, MIN_FOLD_TRADES, MIN_COVERAGE = 30, 5, 0.70
# The 2017+ window has already been searched by the human hunts, so the agent's gate is pooled with
# all of those tests (see memory.holdout_verdicts). 20,000 sims give a p-value floor of 0.00005,
# so a genuinely strong result can survive that pool. Hunt runs keep their own 2,000.
HOLDOUT_SIMS = 20000
TRUNCATION_CHECKS = 2


class Market:
    """Everything the platform knows about one market, built once per session."""

    def __init__(self, ticker: str, ctx_raw: pd.DataFrame):
        self.ticker, self.cfg = ticker, CONFIG[ticker]
        self.df = data.load(ticker).loc[self.cfg["is_start"]:]
        self.ctx_raw = ctx_raw
        self.ctx = data.align_ctx(ctx_raw, self.df.index)
        self.table = F.build(self.df, self.ctx)
        idx = self.df.index
        lo = idx.searchsorted(pd.Timestamp(self.cfg["is_start"]))
        hi = idx.searchsorted(pd.Timestamp(self.cfg["is_end"]), "right")
        self.search = slice(lo, hi)
        self.holdout = slice(idx.searchsorted(pd.Timestamp(self.cfg["oos_start"])), len(idx))
        self.th = S.thresholds(self.table, self.search)
        o = self.df.Open.values[self.search]
        c = self.df.Close.values[self.search]
        # average daily return of simply being in the market during the search window
        self.mu = float(returns_np(o, c, np.ones(len(c)), 0)[1:].mean())
        cuts = np.linspace(lo, hi, 4).astype(int)
        self.folds = [slice(cuts[i], cuts[i + 1]) for i in range(3)]


def coverage(m: Market, s: dict) -> float:
    w = m.table.iloc[m.search][S.features_used(s)]
    return float(w.notna().all(axis=1).mean())


def precheck(m: Market, s: dict) -> str | None:
    """Cheap screen BEFORE the evaluation: None when the spec can be evaluated, else the rejection reason. It applies
    the same coverage and trade-count rules as score() (same positions code, no returns, no look-ahead rebuild), so
    it only ever refuses specs that score() would reject for those reasons."""
    cov = coverage(m, s)
    if cov < MIN_COVERAGE:
        return f"coverage {cov:.0%} < {MIN_COVERAGE:.0%}"
    pos = S.positions(s, m.table, m.th)
    nt, fold_nt = count_trades(pos[m.search]), [count_trades(pos[f]) for f in m.folds]
    if nt < MIN_TRADES or min(fold_nt) < MIN_FOLD_TRADES:
        return f"too few trades ({nt}, min per fold {min(fold_nt)})"
    return None


def lookahead_ok(m: Market, s: dict, pos: np.ndarray) -> bool:
    """Recompute the table on data cut off at a few points inside the search window; the
    positions up to each cut must not change. Thresholds stay fixed (search-window only)."""
    lo, hi = m.search.start, m.search.stop
    for k in np.linspace(lo + (hi - lo) // 3, hi - 5, TRUNCATION_CHECKS).astype(int):
        df_c = m.df.iloc[: k + 1]
        tab = F.build(df_c, data.align_ctx(m.ctx_raw, df_c.index))
        if not np.array_equal(S.positions(s, tab, m.th), pos[: k + 1]):
            return False
    return True


def score(m: Market, s: dict, trial_sharpes: list[float]) -> dict:
    """Search-window evaluation. Returns metrics, a reward in [0, 1] for the agent, and a status."""
    pos = S.positions(s, m.table, m.th)
    cov = coverage(m, s)
    out = {"coverage": round(cov, 3)}
    if cov < MIN_COVERAGE:
        return out | {"status": "rejected", "reason": f"coverage {cov:.0%} < {MIN_COVERAGE:.0%}", "reward": 0.0}
    O, C = m.df.Open.values, m.df.Close.values
    cost, ann = m.cfg["cost_bps"], m.cfg["ann"]
    I = m.search

    def excess(sl):
        """Return beyond what random days in the market would have earned (the coin-flip's
        expectation): r_t - held_t * mu. Rewards timing, not just being invested."""
        p = pos[sl]
        held = np.r_[0.0, p[:-1]]
        return returns_np(O[sl], C[sl], p, cost) - held * m.mu

    r_raw = returns_np(O[I], C[I], pos[I], cost)
    r = excess(I)
    nt = count_trades(pos[I])
    fold_sh, fold_nt = [], []
    for fs in m.folds:
        fold_sh.append(sharpe_np(excess(fs), ann))
        fold_nt.append(count_trades(pos[fs]))
    sd = r.std(ddof=1)
    daily_sr = float(r.mean() / sd) if sd > 1e-12 else 0.0
    z = (r - r.mean()) / sd if sd > 1e-12 else np.zeros(len(r))
    moments = {"T": len(r), "skew": float(np.mean(z**3)), "kurt": float(np.mean(z**4))}
    out |= moments | {"sharpe": round(sharpe_np(r_raw, ann), 3), "excess_sharpe": round(sharpe_np(r, ann), 3), "fold_sharpes": [round(x, 3) for x in fold_sh],
            "trades": nt, "exposure": round(float(pos[I].mean()), 3), "daily_sr": daily_sr}
    if nt < MIN_TRADES or min(fold_nt) < MIN_FOLD_TRADES:
        return out | {"status": "rejected", "reason": f"too few trades ({nt}, min per fold {min(fold_nt)})", "reward": 0.0}
    if not lookahead_ok(m, s, pos):
        return out | {"status": "rejected", "reason": "look-ahead: positions changed when future rows were removed",
                      "reward": 0.0}
    trials = list(trial_sharpes) + [daily_sr]
    dsr = deflated_sharpe(r, trials) if len(trials) > 1 else None
    dsr = 0.0 if dsr is None or not math.isfinite(dsr) else float(dsr)
    consistent = all(x > 0 for x in fold_sh)
    reward = dsr if consistent else dsr * 0.5
    return out | {"status": "scored", "dsr": round(dsr, 4), "consistent": consistent, "reward": round(reward, 4)}


def holdout_test(m: Market, s: dict, sid: str) -> dict:
    """One shot on the held-out window: coin-flip vs random entries, Sharpe at 1x and 2x costs.
    The agent only ever learns pass/fail from this."""
    pos = S.positions(s, m.table, m.th)
    X = m.holdout
    O, C = m.df.Open.values[X], m.df.Close.values[X]
    cost, ann = m.cfg["cost_bps"], m.cfg["ann"]
    s_x = pos[X]
    r = returns_np(O, C, s_x, cost)
    tr = extract_trades(O, C, s_x, cost)
    cf = coin_flip(O, tr, cost, HOLDOUT_SIMS, SEED + zlib.crc32(f"agent{sid}".encode()) % 100000) if len(tr["ret"]) else None
    bh = returns_np(O, C, np.ones(len(C)), cost)
    return {"p_value": cf["p_value"] if cf else 1.0, "n_sims": HOLDOUT_SIMS, "sharpe": sharpe_np(r, ann),
            "sharpe_2x_cost": sharpe_np(returns_np(O, C, s_x, 2 * cost), ann),
            "buy_hold_sharpe": sharpe_np(bh, ann), "trades": int(len(tr["ret"])),
            "window": [str(m.df.index[X][0].date()), str(m.df.index[-1].date())]}
