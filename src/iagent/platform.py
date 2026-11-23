"""Data platform + backtest for the intraday agent. Same discipline as src/agent/platform.py:
features are known at the decision close, trades fill at the next open, thresholds and the score
come from the SEARCH window only (2016-2020), the reward is deflated by every trial ever run on the
market, and the held-out window (2021+) is touched only by the one-shot holdout_test."""
from __future__ import annotations

import math
import zlib

import numpy as np
import pandas as pd

import intraday as I
from iagent import bars5
from iagent import features as F
from iagent import spec as S
from stats import deflated_sharpe

MIN_TRADES, MIN_FOLD_TRADES, MIN_COVERAGE = I.MIN_IS_TRADES, 10, 0.70
HOLDOUT_SIMS = 20000


class Market:
    """Everything the platform knows about one market, built once per session."""

    def __init__(self, symbol: str, D=None):
        self.symbol = symbol
        self.D = D if D is not None else bars5.load_days(symbol)
        D = self.D
        self.cube = F.build(D)
        hi = int(D.dates.searchsorted(pd.Timestamp(I.IS_END), "right"))
        self.search = slice(0, hi)
        self.holdout = slice(int(D.dates.searchsorted(pd.Timestamp(I.OOS_START))), D.n)
        cuts = np.linspace(0, hi, 4).astype(int)
        self.folds = [slice(cuts[i], cuts[i + 1]) for i in range(3)]
        self.th = S.thresholds(self.cube, self.search)
        # mean 1-minute return of simply being in the market (open-to-open) in the search window
        step = D.P[self.search, 1:] / D.P[self.search, :-1] - 1
        self.mu_min = float(np.nanmean(step[np.isfinite(step)]))


def coverage(m: Market, s: dict) -> float:
    i = F.DECISIONS.index(s["time"])
    js = [F.FEATURES.index(f) for f in S.features_used(s)]
    w = m.cube[m.search, i][:, js]
    return float((~np.isnan(w)).all(axis=1).mean())


def precheck(m: Market, s: dict) -> str | None:
    """Cheap screen BEFORE the evaluation (None = evaluate it). Every day where the entry condition holds is exactly
    one trade, so the trade counts of score() follow from the entry mask alone: no stop/target simulation, no returns."""
    cov = coverage(m, s)
    if cov < MIN_COVERAGE:
        return f"coverage {cov:.0%} < {MIN_COVERAGE:.0%}"
    d = np.flatnonzero(S.entry_mask(s, m.cube, m.th, m.search))
    fold_nt = [int(((d >= f.start) & (d < f.stop)).sum()) for f in m.folds]
    if len(d) < MIN_TRADES or min(fold_nt) < MIN_FOLD_TRADES:
        return f"too few trades ({len(d)}, min per fold {min(fold_nt)})"
    return None


def _daily(n: int, t: dict, vals: np.ndarray) -> np.ndarray:
    r = np.zeros(n)
    np.add.at(r, t["day"], vals)
    return r


def _fold_sharpes_2x(m: Market, t: dict, ex: np.ndarray) -> list[float]:
    """Excess-return fold Sharpes with one more round trip of costs per trade (2x costs)."""
    r2 = _daily(m.search.stop, t, ex - 2 * I.COST)
    return [I.sharpe(r2[f]) for f in m.folds]


def robust_eval(m: Market, s: dict) -> dict:
    """Search-window only: the fields the robustness ranking needs (fold Sharpes at 1x and 2x costs, trades).
    Used to fill in journal records written before fold_sharpes_2x existed. Never touches the held-out years."""
    t = S.trades(s, m.D, m.cube, m.th, m.search)
    hold = (t["exit_m"] - t["entry_m"]).astype(float)
    ex = t["ret"] - t["dir"] * m.mu_min * hold
    r = _daily(m.search.stop, t, ex)
    return {"fold_sharpes": [round(I.sharpe(r[f]), 3) for f in m.folds],
            "fold_sharpes_2x": [round(x, 3) for x in _fold_sharpes_2x(m, t, ex)], "trades": int(len(t["ret"]))}


def score(m: Market, s: dict, trial_sharpes: list[float]) -> dict:
    """Search-window evaluation: metrics, a reward in [0, 1] for the agent, and a status."""
    cov = coverage(m, s)
    out = {"coverage": round(cov, 3)}
    if cov < MIN_COVERAGE:
        return out | {"status": "rejected", "reason": f"coverage {cov:.0%} < {MIN_COVERAGE:.0%}", "reward": 0.0}
    t = S.trades(s, m.D, m.cube, m.th, m.search)
    hold = (t["exit_m"] - t["entry_m"]).astype(float)
    # excess over random timing: a long in a rising market earns mu_min per minute for free
    ex = t["ret"] - t["dir"] * m.mu_min * hold
    hi = m.search.stop
    r_raw, r = _daily(hi, t, t["ret"]), _daily(hi, t, ex)
    fold_nt = [int(((t["day"] >= f.start) & (t["day"] < f.stop)).sum()) for f in m.folds]
    fold_sh = [I.sharpe(r[f]) for f in m.folds]
    fold_sh2 = _fold_sharpes_2x(m, t, ex)
    nt = len(t["ret"])
    sd = r.std(ddof=1)
    daily_sr = float(r.mean() / sd) if sd > 1e-12 else 0.0
    z = (r - r.mean()) / sd if sd > 1e-12 else np.zeros(len(r))
    out |= {"T": len(r), "skew": float(np.mean(z**3)), "kurt": float(np.mean(z**4)),
            "sharpe": round(I.sharpe(r_raw), 3), "excess_sharpe": round(I.sharpe(r), 3),
            "fold_sharpes": [round(x, 3) for x in fold_sh], "fold_sharpes_2x": [round(x, 3) for x in fold_sh2],
            "trades": nt,
            "exposure": round(float(hold.sum() / max(1, m.D.last[:hi].sum() + hi)), 4), "daily_sr": daily_sr}
    if nt < MIN_TRADES or min(fold_nt) < MIN_FOLD_TRADES:
        return out | {"status": "rejected", "reason": f"too few trades ({nt}, min per fold {min(fold_nt)})", "reward": 0.0}
    trials = list(trial_sharpes) + [daily_sr]
    dsr = deflated_sharpe(r, trials) if len(trials) > 1 else None
    dsr = 0.0 if dsr is None or not math.isfinite(dsr) else float(dsr)
    consistent = all(x > 0 for x in fold_sh)
    return out | {"status": "scored", "dsr": round(dsr, 4), "consistent": consistent,
                  "reward": round(dsr if consistent else dsr * 0.5, 4)}


def holdout_test(m: Market, s: dict, sid: str) -> dict:
    """One shot on the held-out window: coin flip vs random entry minutes (20,000 sims), Sharpe at 1x
    and 2x costs. The agent only ever learns pass/fail from this."""
    D, X = m.D, m.holdout
    t = S.trades(s, D, m.cube, m.th, X)
    r = _daily(D.n, t, t["ret"])[X]
    r2 = _daily(D.n, t, t["ret"] - 2 * I.COST)[X]          # 2x costs: one more round trip per trade
    bh = np.r_[0.0, D.close[1:] / D.close[:-1] - 1][X]
    p = 1.0
    if len(t["ret"]):
        old, I.N_SIMS = I.N_SIMS, HOLDOUT_SIMS
        try:
            p = I.coin_flip(D, t, I.SEED + zlib.crc32(f"iagent{sid}".encode()) % 100000)["p_value"]
        finally:
            I.N_SIMS = old
    return {"p_value": p, "n_sims": HOLDOUT_SIMS, "sharpe": I.sharpe(r), "sharpe_2x_cost": I.sharpe(r2),
            "buy_hold_sharpe": I.sharpe(bh), "trades": int(len(t["ret"])),
            "window": [str(D.dates[X.start].date()), str(D.dates[-1].date())]}


LOCK_MIN_NAMES, LOCK_MIN_TRADES = 3, 20


def spy_buy_hold_sharpe(m: Market) -> float:
    """Buy-and-hold Sharpe of this market over the held-out window (SPY's, for the single-stock benchmark)."""
    D = m.D
    return I.sharpe(np.r_[0.0, D.close[1:] / D.close[:-1] - 1][m.holdout])


def lockbox_check(get_market, s: dict, names: list[str]) -> dict:
    """Second, unseen set of names: the same rule (percentile thresholds from each name's OWN search years) traded on every
    lockbox name's held-out years at 2x costs. ok = at least LOCK_MIN_NAMES names with >= LOCK_MIN_TRADES trades, median
    2x-cost Sharpe > 0 and a strict majority of them positive. No lockbox names defined -> ok (nothing to confirm).
    A name whose data cannot be loaded counts as missing, so a lockbox that cannot be read never confirms anything."""
    if not names:
        return {"ok": True, "n_names": 0, "note": "no lockbox names in the universe"}
    per = {}
    for nm in names:
        try:
            m = get_market(nm)
            t = S.trades(s | {"market": nm}, m.D, m.cube, m.th, m.holdout)
            r2 = _daily(m.D.n, t, t["ret"] - 2 * I.COST)[m.holdout]
            per[nm] = {"sharpe_2x_cost": round(I.sharpe(r2), 3), "trades": int(len(t["ret"]))}
        except Exception as ex:
            per[nm] = {"error": type(ex).__name__}
    used = {k: v for k, v in per.items() if v.get("trades", 0) >= LOCK_MIN_TRADES}
    sh = [v["sharpe_2x_cost"] for v in used.values()]
    ok = len(used) >= LOCK_MIN_NAMES and float(np.median(sh)) > 0 and sum(x > 0 for x in sh) > len(sh) / 2
    return {"ok": bool(ok), "n_names": len(names), "n_used": len(used), "median_sharpe_2x": round(float(np.median(sh)), 3) if sh else None,
            "positive": int(sum(x > 0 for x in sh)), "names": per}
