"""Cross-check helper: daily target weights (date x symbol) + daily prices -> vectorbt Portfolio (target-percent orders).

Meant to audit a system's own backtest (e.g. the qlib system, results/qlib15.json) with an independent accountant:

    pf, ret = run_weights(weights, prices)                  # weights decided at close t, filled at prices of t+lag
    ref = reference_returns(weights, prices)                # plain-numpy version of the same book (no vectorbt)
    compare(ret, system_daily_returns)                      # max/mean abs diff, total return diff
    crosscheck_qlib()                                       # reads results/qlib15.json if it holds weights

Conventions: the weight row of date t is the target the system wants AFTER trading at t's price `price` (default 'close'); with
lag=1 (default) a row decided on t is filled at the NEXT date's price, so a system that already shifted its weights passes lag=0.
Costs are `fee` per side on traded value (1 bp = the studies' COST). Weights that do not sum to 1 leave the rest in cash; gross exposure above 1 is refused (vectorbt is cash-constrained).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from vbt_noise import COST, _vbt

ROOT = Path(__file__).resolve().parent.parent
QLIB_JSON = ROOT / "results" / "qlib15.json"


def _align(weights: pd.DataFrame, prices: pd.DataFrame, lag: int):
    px = prices.sort_index()
    w = weights.reindex(index=px.index, columns=px.columns).fillna(0.0)
    if lag:
        w = w.shift(lag).fillna(0.0)
    return w, px


def run_weights(weights: pd.DataFrame, prices: pd.DataFrame, fee: float = COST, lag: int = 1,
                init_cash: float = 1_000_000.0, rebalance: str = "daily"):
    """Run the book through vbt.Portfolio.from_orders(size_type='targetpercent', cash_sharing). Returns (portfolio, daily returns).
    rebalance 'daily' re-sends the target every date (drift is traded back, like a target-weight backtest);
    'on_change' only sends orders on dates where the target row changes."""
    vbt = _vbt()
    w, px = _align(weights, prices, lag)
    if (w.abs().sum(axis=1) > 1 + 1e-9).any():
        raise ValueError("gross weight > 1: vectorbt from_orders is cash-constrained (no leverage), so the result would not "
                         "follow the targets; scale the weights or compare against reference_returns instead")
    size = w.copy()
    if rebalance == "on_change":
        size = size.where((w != w.shift(1)).any(axis=1), np.nan)
        size.iloc[0] = w.iloc[0]
    elif rebalance != "daily":
        raise ValueError("rebalance must be 'daily' or 'on_change'")
    pf = vbt.Portfolio.from_orders(px, size=size, size_type="targetpercent", direction="both", fees=fee,
                                   init_cash=init_cash, cash_sharing=True, group_by=True, call_seq="auto", freq="1D")
    ret = pf.returns()
    ret = ret.iloc[:, 0] if isinstance(ret, pd.DataFrame) else ret
    return pf, ret


def reference_returns(weights: pd.DataFrame, prices: pd.DataFrame, fee: float = COST, lag: int = 1) -> pd.Series:
    """Plain-numpy book with daily rebalancing: equity_t = equity_{t-1} * (1 + sum_i w_i r_i) minus fee * traded value, where the
    weights held over (t-1, t] were set at t-1 and traded at price t-1; trade = target minus the drifted holding."""
    w, px = _align(weights, prices, lag)
    P, W = px.values, w.values
    n = len(P)
    cash, sh, prev = 1.0, np.zeros(P.shape[1]), 1.0
    out = np.zeros(n)
    for t in range(n):
        eq = cash + sh @ P[t]                      # mark to date t's price
        tgt = W[t] * eq / P[t]
        trade = tgt - sh
        cash -= (trade * P[t]).sum() + fee * np.abs(trade * P[t]).sum()
        sh = tgt
        eq = cash + sh @ P[t]                      # equity after fees
        out[t], prev = eq / prev - 1, eq
    return pd.Series(out, index=px.index)


def compare(a: pd.Series, b: pd.Series) -> dict:
    """Daily-return agreement between two series (aligned on the common dates)."""
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    d = j.iloc[:, 0] - j.iloc[:, 1]
    return {"days": int(len(j)), "max_abs_daily_diff": float(d.abs().max()) if len(j) else None,
            "mean_abs_daily_diff": float(d.abs().mean()) if len(j) else None,
            "worst_day": str(d.abs().idxmax().date()) if len(j) else None,
            "total_return_a": float((1 + j.iloc[:, 0]).prod() - 1), "total_return_b": float((1 + j.iloc[:, 1]).prod() - 1),
            "total_return_diff": float((1 + j.iloc[:, 0]).prod() - (1 + j.iloc[:, 1]).prod()),
            "corr": float(j.iloc[:, 0].corr(j.iloc[:, 1]))}


def crosscheck_qlib(path: Path = QLIB_JSON, prices: pd.DataFrame | None = None, **kw) -> dict:
    """Run the cross-check on a qlib system result. Expects the file to hold 'weights' (date -> {symbol: weight} or a records
    list) and 'daily_returns' (date -> return); `prices` (date x symbol close) must be given. Returns a status dict if the file
    has no weights yet (the qlib system is being built by another agent)."""
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    j = json.loads(path.read_text())
    wraw, rraw = j.get("weights") or j.get("target_weights"), j.get("daily_returns") or j.get("returns")
    if not wraw:
        return {"status": "no_weights_in_file", "keys": sorted(j)[:30]}
    if prices is None:
        return {"status": "need_prices", "symbols": len({s for v in (wraw.values() if isinstance(wraw, dict) else []) for s in v})}
    w = pd.DataFrame(wraw).T if isinstance(wraw, dict) else pd.DataFrame(wraw)
    w.index = pd.to_datetime(w.index)
    pf, ret = run_weights(w, prices, **kw)
    out = {"status": "ok", "vbt_total_return": float((1 + ret).prod() - 1)}
    if rraw:
        r = pd.Series(rraw) if isinstance(rraw, dict) else pd.Series(*rraw)
        r.index = pd.to_datetime(r.index)
        out["vs_system"] = compare(ret, r)
    return out
