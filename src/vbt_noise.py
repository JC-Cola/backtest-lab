"""vectorbt re-implementation of the live noise-band strategy (src/intraday.py noise_breakout / noise_decisions).

Rules (identical to the engine): sigma[d, m] = mean over the previous `lookback` sessions of |close[m]/open0 - 1|;
bands = max/min(open0, prev_close) * (1 +/- band * sigma[m]); at the decision bars (checks 09:59, 10:59 ... for check=60)
go long above max(upper, VWAP), short below min(lower, VWAP), else flat; a decision on the close of bar m fills at the OPEN
of bar m+1; flat at the session close (price = last close). `vwap=False` drops the VWAP leg, `band` scales the band width
(1.0 = live). Sizing: `weight` of equity per symbol (live: 0.5), cost 1 bp per side (= engine COST).

Orders are sent only where the target position changes; vbt Portfolio.from_orders then does the accounting (fees, equity).
The decision signal is built with numpy for the whole parameter grid at once and every column goes through ONE Portfolio.

    python src/vbt_noise_study.py crosscheck | sweep
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import intraday
import intraday_data

COST = intraday.COST
WEIGHT = 0.5
LIVE = dict(lookback=14, check=60, band=1.0, vwap=True)
FIRST_FILL = 30            # first decision bar 29 (09:59) fills at the open of bar 30 (10:00)


def _vbt():
    """Import vectorbt. It hard-imports the python-telegram-bot package `telegram`, which src/telegram.py (our own
    notifier) shadows when src/ is on sys.path; import it with src/ hidden, then put the shadowing module back."""
    import sys
    import warnings
    from pathlib import Path
    warnings.filterwarnings("ignore")
    if "vectorbt" in sys.modules:
        return sys.modules["vectorbt"]
    here = str(Path(__file__).resolve().parent)
    saved_path = list(sys.path)
    saved_mods = {k: v for k, v in sys.modules.items() if k == "telegram" or k.startswith("telegram.")}
    for k in saved_mods:
        del sys.modules[k]
    sys.path[:] = [p for p in sys.path if str(Path(p or ".").resolve()) != here]
    try:
        import vectorbt as vbt
    finally:
        sys.path[:] = saved_path
        for k in [k for k in sys.modules if k == "telegram" or k.startswith("telegram.")]:
            del sys.modules[k]
        sys.modules.update(saved_mods)
    return vbt


def load_days(symbol: str) -> intraday.Days:
    return intraday.Days.from_frame(intraday_data.load(symbol))


# ---------------------------------------------------------------- time grid (the points where a fill can happen)
class Grid:
    """Flat list of price points. For each session: the minutes `ks` that are <= last, plus the session close (k = last+1,
    price = closing price). Price at minute k = open of bar k (engine's D.P)."""

    def __init__(self, D, ks):
        ks = np.asarray(ks)
        day, kk = [], []
        for d in range(D.n):
            k = ks[ks <= D.last[d]]
            k = np.r_[k, D.last[d] + 1]
            day.append(np.full(len(k), d))
            kk.append(k)
        self.day, self.k = np.concatenate(day), np.concatenate(kk)
        self.price = D.P[self.day, self.k]
        self.is_close = self.k == D.last[self.day] + 1
        self.index = pd.DatetimeIndex(D.dates[self.day]) + pd.to_timedelta(self.k, unit="min")
        self.n_days = D.n
        self.dates = D.dates


def full_grid(D):
    return Grid(D, np.arange(0, 390))


def compact_grid(D, step=15):
    """Every fill of any check interval that is a multiple of `step` minutes (15/30/60/90/120 all are)."""
    return Grid(D, np.arange(FIRST_FILL, 390, step))


# ---------------------------------------------------------------- signals
def _sigma(D, lookback):
    """sig[d] = mean(move[d-lookback:d], axis 0), NaN for d < lookback."""
    move = np.abs(D.C / D.O[:, :1] - 1)
    w = np.lib.stride_tricks.sliding_window_view(move, lookback, axis=0).mean(-1)   # row j = mean(move[j:j+L])
    sig = np.full_like(move, np.nan)
    sig[lookback:] = w[:-1]
    return sig


def decisions(D, sig, check, band=1.0, vwap=True):
    """cur[d, i] in {-1,0,+1} at decision bar m_i = 29 + check*i (valid only while m_i < last[d]); returns (cur, nvalid)."""
    ms = np.array([m for m in range(29, 389) if (m + 1 - FIRST_FILL) % check == 0])
    hi = np.maximum(D.O[:, 0], D.prev_close)[:, None]
    lo = np.minimum(D.O[:, 0], D.prev_close)[:, None]
    up = hi * (1 + band * sig[:, ms])
    dn = lo * (1 - band * sig[:, ms])
    p, vw = D.C[:, ms], D.vwap[:, ms]
    if vwap:
        up, dn = np.maximum(up, vw), np.minimum(dn, vw)
    cur = np.where(p > up, 1.0, np.where(p < dn, -1.0, 0.0))
    nvalid = (ms[None, :] < D.last[:, None]).sum(1)
    return cur, nvalid, ms


def target_positions(D, grid, lookback=14, check=60, band=1.0, vwap=True):
    """Target position (-1/0/+1, held AFTER the fill at each grid point) for one parameter set."""
    sig = _sigma(D, lookback)
    cur, nvalid, ms = decisions(D, sig, check, band, vwap)
    k, d = grid.k, grid.day
    i = np.where(k >= FIRST_FILL, (k - FIRST_FILL) // check, -1)
    i = np.minimum(i, nvalid[d] - 1)
    pos = np.where((i >= 0) & (d >= lookback + 1) & ~grid.is_close, cur[d, np.maximum(i, 0)], 0.0)
    return np.nan_to_num(pos)          # NaN sigma (warm-up days) -> flat


def orders_from_targets(tgt, weight=WEIGHT):
    """Size matrix for from_orders(size_type='targetpercent'): target*weight, NaN (no order) where the target is unchanged."""
    prev = np.vstack([np.zeros((1, tgt.shape[1])), tgt[:-1]])
    return np.where(tgt == prev, np.nan, tgt * weight)


# ---------------------------------------------------------------- portfolio
def run(D, grid, params: list[dict], weight=WEIGHT, cost=COST, init_cash=100_000.0):
    """One vectorbt Portfolio with one column per parameter set. Returns (portfolio, daily return matrix [days x cols])."""
    vbt = _vbt()
    tgt = np.column_stack([target_positions(D, grid, **p) for p in params])
    cols = pd.RangeIndex(len(params), name="cfg")
    size = pd.DataFrame(orders_from_targets(tgt, weight), index=grid.index, columns=cols)
    close = pd.Series(grid.price, index=grid.index)
    pf = vbt.Portfolio.from_orders(close, size=size, size_type="targetpercent", direction="both", fees=cost,
                                   init_cash=init_cash, freq="1min")
    val = pf.value().values[grid.is_close]                              # equity at each session close
    prev = np.vstack([np.full((1, val.shape[1]), init_cash), val[:-1]])
    return pf, val / prev - 1, tgt


def n_trades(tgt, grid):
    """Number of round trips (position runs) per column, as the engine counts trades."""
    prev = np.vstack([np.zeros((1, tgt.shape[1])), tgt[:-1]])
    return ((tgt != prev) & (tgt != 0)).sum(0)
