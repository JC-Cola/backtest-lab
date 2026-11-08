"""Crypto study 9 (CRYPTO_PLAN_9.md), core: the rules (signals), windows, metrics, single-coin evaluation and the
weekend / funding filters. Rotation and the hourly controls are in crypto9_rot.py, the driver in crypto9.py.

A position s_t is decided on the close of day t (00:00 UTC of t+1) and traded at the open of day t+1, exactly as in
engine.returns_np; hourly rules use hourly bars the same way. Costs are bps per side on every change of position.
"""
from __future__ import annotations

import math
import zlib

import numpy as np
import pandas as pd

import crypto_data as CD
import strategies_trend as tr
from engine import count_trades, extract_trades, returns_np
from stats import coin_flip, deflated_sharpe, metrics, select_best, sharpe_np

ROOT = CD.DIR.parent.parent
OUT = ROOT / "results" / "crypto9.json"
PLAN = "CRYPTO_PLAN_9.md"
COST, STRESS = 20.0, 50.0          # bps per side (0.2% main, 0.5% stress)
ANN, ANN_H = 365, 8760
IS_START, IS_END, OOS_START = "2016-01-01", "2022-12-31", "2023-01-01"
N_SIMS, SEED, MIN_IS_TRADES = 2000, 20261107, 10
BAND = 0.10                         # ensemble rebalance band (share of the account)
LEGS_ALL = [5, 10, 20, 30, 60, 90, 180, 360]
LEGS_SLOW = [30, 60, 90, 180, 360]
DISCOVERY = ["BTC-USD", "ETH-USD", "SOL-USD"]
LOCKBOX = ["LTC-USD", "BCH-USD", "LINK-USD", "AVAX-USD"]
FILTER_SHARPE_TOL = 0.05


def grid(**kw):
    out = [{}]
    for k, vs in kw.items():
        out = [d | {k: v} for d in out for v in vs]
    return out


# ---------------------------------------------------------------------------------------------- signals (pure)

def donchian_leg(c, N):
    """One Donchian leg: long once close_t > the highest close of the prior N days; a ratcheting trailing stop starts at
    the midpoint of the last N closes (ending at t, inclusive) and only moves up; exit when close_t < the stop set by
    the day before (no re-entry on the exit day). Returns the leg state decided on each close. Flat for t < N."""
    c = np.asarray(c, float)
    n = len(c)
    on = np.zeros(n, bool)
    state, stop = False, 0.0
    for t in range(N, n):
        w = c[t - N + 1: t + 1]
        mid = (w.max() + w.min()) / 2
        if state:
            if c[t] < stop:
                state = False
            else:
                stop = max(stop, mid)
        elif c[t] > c[t - N: t].max():
            state, stop = True, mid
        on[t] = state
    return on


def newhigh_1d(c, N):
    """Long on the day after a close at its N-day high (the close equals the max of the last N closes, today included)."""
    hh = pd.Series(np.asarray(c, float)).rolling(N).max().values
    return (np.asarray(c, float) >= hh).astype(float)  # NaN compares False


def newhigh_level(c, N):
    """Enter on the first N-day-high close; the breakout level L0 is the max of the N closes BEFORE that day. Stay long
    while close_t >= L0; the first close below L0 ends the trade."""
    c = np.asarray(c, float)
    hh = pd.Series(c).rolling(N).max().values
    s = np.zeros(len(c))
    state, level = False, 0.0
    for t in range(len(c)):
        if state:
            if c[t] < level:
                state = False
        elif t >= N and c[t] >= hh[t]:
            state, level = True, c[t - N: t].max()
        s[t] = 1.0 if state else 0.0
    return s


def execute(tgt, wd_fill, thr, weekend=False):
    """Turn a target-weight series into the held position. The held weight moves to the target when it differs by more
    than `thr`, or when the target is 0 (full exit). With `weekend`, a new entry from flat is not filled on a Saturday or
    Sunday (wd_fill = weekday of the FILL day, 5/6 = weekend); it waits for Monday if the target is still on."""
    out = np.zeros(len(tgt))
    held = 0.0
    for t, x in enumerate(tgt):
        if (x == 0 and held != 0) or abs(x - held) > thr:
            if not (weekend and held == 0 and wd_fill[t] >= 5):
                held = x
        out[t] = held
    return out


def funding_flag(dates, rates):
    """True on days when the 7-day mean of the daily-averaged funding rate is above the 90th percentile of that mean over
    the trailing 365 days (today included). Inactive (False) until 365 days of the 7-day mean exist and for any day with no
    funding data. `rates`: Series of settlements indexed by time."""
    d = rates.groupby(rates.index.floor("D")).mean()
    d = d.reindex(pd.date_range(d.index[0], d.index[-1], freq="D"))
    f7 = d.rolling(7).mean()
    q = f7.rolling(365, min_periods=365).quantile(0.9)
    flag = (f7 > q).reindex(dates)
    return flag.fillna(False).values.astype(bool)


class Coin:
    """One market's daily bars with the windows and cached indicators the rules need."""

    def __init__(self, name, df, funding=None):
        self.name, self.df, self.funding = name, df, funding
        self.dates, self.o, self.c = df.index, df.Open.values.astype(float), df.Close.values.astype(float)
        self.n = len(df)
        self.start = max(pd.Timestamp(IS_START), df.index[0] + pd.Timedelta(days=365))
        self.I = slice(int(self.dates.searchsorted(self.start)), int(self.dates.searchsorted(pd.Timestamp(IS_END), "right")))
        self.X = slice(int(self.dates.searchsorted(pd.Timestamp(OOS_START))), self.n)
        self.wd_fill = (np.asarray(self.dates.dayofweek) + 1) % 7
        lr = np.r_[np.nan, np.diff(np.log(self.c))]
        self.vol = pd.Series(lr).rolling(30).std().values * math.sqrt(ANN)   # NaN for the first 30 days
        self._legs, self._flag = {}, None

    def leg(self, N):
        if N not in self._legs:
            self._legs[N] = donchian_leg(self.c, N)
        return self._legs[N]

    def flag(self):
        if self._flag is None:
            self._flag = (funding_flag(self.dates, self.funding) if self.funding is not None
                          else np.zeros(self.n, bool))
        return self._flag

    @property
    def tunable(self):
        return self.I.stop > self.I.start


def _mult(coin, fund):
    """Exposure multiplier from the funding filter: fund None (off), 'halve' (x0.5) or 'zero' (x0) when the flag is on."""
    if fund is None:
        return np.ones(coin.n)
    return np.where(coin.flag(), 0.5 if fund == "halve" else 0.0, 1.0)


def pos_donchian(coin, target, legs, weekend=False, fund=None):
    use = LEGS_ALL if legs == "all" else LEGS_SLOW
    with np.errstate(divide="ignore", invalid="ignore"):
        w = np.nan_to_num(np.minimum(1.0, target / coin.vol), nan=0.0)
    tgt = sum(coin.leg(N) * w for N in use) / len(use) * _mult(coin, fund)
    return execute(tgt, coin.wd_fill, BAND, weekend)


def pos_trend(coin, n, weekend=False, fund=None):
    s = tr.trend_filter(coin.df, n).values.astype(float)   # NaN-safe: comparison with NaN SMA is False
    return execute(s * _mult(coin, fund), coin.wd_fill, 0.0, weekend)


def pos_newhigh(coin, N, hold):
    return newhigh_1d(coin.c, N) if hold == "1d" else newhigh_level(coin.c, N)


STRATS = {
    "c9_donchian": {"name": "Donchian Crypto Ensemble", "pos": pos_donchian, "grid": grid(target=[0.25, 0.50, 0.75], legs=["all", "slow"]),
                    "source": "Zarattini, Pagani & Barbon (2025), Catching Crypto Trends (study 9)",
                    "plain": "Eight Donchian breakout legs (5 to 360 days) with trailing stops, each sized to a volatility target and averaged, long or flat."},
    "c9_trend": {"name": "Crypto Trend Filter", "pos": pos_trend, "grid": grid(n=[20, 50, 100, 200]),
                 "source": "Faber (2007), applied to BTC, ETH and SOL (study 9)",
                 "plain": "Holds the coin while its price is above its average of the last 20, 50, 100 or 200 days, otherwise cash."},
    "c9_newhigh": {"name": "Crypto New-High Breakout", "pos": pos_newhigh, "grid": grid(N=[10, 20, 50], hold=["1d", "level"]),
                   "source": "New-high momentum folklore (study 9)",
                   "plain": "Buys the day after the coin closes at a 10, 20 or 50-day high, holding one day or until the breakout fails."},
}
ROT_GRID = grid(L=[1, 2, 4], k=[1, 2, 3])


# ---------------------------------------------------------------------------------------------- metrics

def perf(r, s, trades):
    r = np.asarray(r, float)
    m = metrics(r, s, trades, ANN)
    m["calmar"] = float(m["cagr"] / abs(m["max_dd"])) if m["max_dd"] < -1e-9 else None
    m["trades_per_year"] = float(m["n_trades"] / (len(r) / ANN)) if len(r) else 0.0
    sv = np.nan_to_num(np.asarray(s, float))
    m["turnover_per_year"] = float(np.abs(np.diff(sv, prepend=0.0)).sum() / (len(r) / ANN)) if len(r) else 0.0
    return m


def run_ret(coin, s, sl, cost):
    return returns_np(coin.o[sl], coin.c[sl], np.asarray(s, float)[sl], cost)


def run_perf(coin, s, sl, cost):
    return perf(run_ret(coin, s, sl, cost), np.asarray(s, float)[sl],
                extract_trades(coin.o[sl], coin.c[sl], np.asarray(s, float)[sl], cost))


def buy_hold(coin):
    s = np.ones(coin.n)
    return run_perf(coin, s, coin.X, COST) | {"stress": run_perf(coin, s, coin.X, STRESS)}


def is_risk_reducer(m, bh):
    """Plan: max drawdown at most 60% of buy-and-hold's AND Calmar at least buy-and-hold's (0.2% costs, test years)."""
    if m["calmar"] is None or bh["calmar"] is None:
        return False
    return bool(abs(m["max_dd"]) <= 0.6 * abs(bh["max_dd"]) and m["calmar"] >= bh["calmar"])


def _seed(sid, asset):
    return SEED + zlib.crc32(f"{sid}{asset}".encode()) % 100000


def evaluate_single(sid, coin):
    """Tune on the tuning window (best Sharpe, >= 10 trades), one test run, 0.5% stress, coin-flip, risk-reducer."""
    st = STRATS[sid]
    pos = [np.nan_to_num(st["pos"](coin, **p)) for p in st["grid"]]
    I, X = coin.I, coin.X
    is_r = [run_ret(coin, s, I, COST) for s in pos]
    is_sh = [sharpe_np(r, ANN) for r in is_r]
    is_nt = [count_trades(s[I]) for s in pos]
    b = select_best(is_sh, is_nt, MIN_IS_TRADES)
    s = pos[b]
    r_oos = run_ret(coin, s, X, COST)
    oos = run_perf(coin, s, X, COST)
    stress = run_perf(coin, s, X, STRESS)
    trial = [r.mean() / r.std(ddof=1) for r in is_r if r.std(ddof=1) > 1e-12]
    bh = buy_hold(coin)
    grid_oos = [run_perf(coin, q, X, COST) for q in pos]
    cf = None
    if oos["exposure"] <= 0.95:
        cf = coin_flip(coin.o[X], extract_trades(coin.o[X], coin.c[X], s[X], COST), COST, N_SIMS, _seed(sid, coin.name))
    row = {"strategy": sid, "asset": coin.name, "params": st["grid"][b], "n_trials": len(pos),
           "is": perf(is_r[b], s[I], extract_trades(coin.o[I], coin.c[I], s[I], COST)),
           "oos": oos, "oos_stress": stress, "oos_cost2x_sharpe": stress["sharpe"],
           "grid_median_oos_sharpe": float(np.median([g["sharpe"] for g in grid_oos])),
           "dsr_prob": deflated_sharpe(is_r[b], trial) if len(trial) > 1 else None,
           "dsr_test": deflated_sharpe(r_oos, np.array(trial)) if len(trial) > 1 else None,
           "coin_flip": cf, "buy_hold_oos": bh,
           "risk_reducer": is_risk_reducer(oos, bh), "risk_reducer_stress": is_risk_reducer(stress, bh["stress"]),
           "risk_reducer_grid": f"{sum(is_risk_reducer(g, bh) for g in grid_oos)} of {len(grid_oos)}",
           "windows": {"tune": [str(coin.dates[I.start].date()), str(coin.dates[I.stop - 1].date())],
                       "test": [str(coin.dates[X.start].date()), str(coin.dates[-1].date())]}}
    return row, pos, b


def funding_active_from(dates, rates):
    """First day the funding filter can act (365 days of the 7-day mean exist); None if never."""
    d = rates.groupby(rates.index.floor("D")).mean()
    f7 = d.reindex(pd.date_range(d.index[0], d.index[-1], freq="D")).rolling(7).mean()
    ok = f7.rolling(365, min_periods=365).quantile(0.9).dropna()
    ok = ok[ok.index >= dates[0]]
    return str(ok.index[0].date()) if len(ok) else None


_KEYS = ("sharpe", "total_return", "max_dd", "calmar", "trades_per_year")


def filters(sid, coin, row):
    """Weekend and funding de-risk variants of the tuned setting. 'helps' (plan): smaller max drawdown AND higher Calmar AND
    Sharpe not more than FILTER_SHARPE_TOL below the unfiltered one, on the test years at 0.2% (and again at 0.5%);
    the tuning years are shown for consistency (the funding filter is mostly inactive there)."""
    p = row["params"]
    base_pos = STRATS[sid]["pos"](coin, **p)
    base, base_s, base_t = row["oos"], row["oos_stress"], run_perf(coin, base_pos, coin.I, COST)

    def helps(f, b):
        return bool(f["max_dd"] > b["max_dd"] and (f["calmar"] or -9) > (b["calmar"] or -9)
                    and f["sharpe"] >= b["sharpe"] - FILTER_SHARPE_TOL)
    out = []
    for label, kw in (("weekend", {"weekend": True}), ("funding halve", {"fund": "halve"}), ("funding zero", {"fund": "zero"})):
        s = STRATS[sid]["pos"](coin, **p, **kw)
        m, ms, mt = run_perf(coin, s, coin.X, COST), run_perf(coin, s, coin.X, STRESS), run_perf(coin, s, coin.I, COST)
        out.append({"strategy": sid, "asset": coin.name, "filter": label, "params": p,
                    "base": {k: base[k] for k in _KEYS}, "filtered": {k: m[k] for k in _KEYS},
                    "base_stress": {k: base_s[k] for k in _KEYS}, "filtered_stress": {k: ms[k] for k in _KEYS},
                    "base_tune": {k: base_t[k] for k in _KEYS}, "filtered_tune": {k: mt[k] for k in _KEYS},
                    "helps": helps(m, base), "helps_stress": helps(ms, base_s), "helps_tune": helps(mt, base_t),
                    "funding_days_on_test": int(coin.flag()[coin.X].sum()) if "fund" in kw else None,
                    "funding_days_on_tune": int(coin.flag()[coin.I].sum()) if "fund" in kw else None})
    return out
