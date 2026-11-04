"""Intraday study 7 (INTRADAY_PLAN_7.md): the day-trader-skill opening-range breakout, alone and gated by its
daily scanner setups, on SPY and QQQ. Same clock, costs, windows and tests as intraday.py; the runner is intraday3.main.

    python src/intraday7.py              # discovery on SPY and QQQ
    python src/intraday7.py --lockbox    # also run a candidate on XLK, SMH, XLF, XLE (downloads them)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import intraday as I
import intraday3 as S3
from indicators import rsi
from intraday import _ret, _trades, grid

LOCKBOX = ["XLK", "SMH", "XLF", "XLE"]
OUT = I.ROOT / "results" / "intraday7.json"

BAR = 5          # minutes per bar
OR_BARS = 3      # opening range = first 15 minutes
FIRST_FILL = 15  # no entries before 09:45
LAST_FILL = 360  # none after 15:30
FLAT = 385       # flat at 15:55


def _exit(D, d, e, stop, tgt, end):
    """Long trade filled at the open of minute e: stop/target fill at their level, or at a bar's open if it opens
    through them (stop checked first); otherwise out at minute `end`."""
    for j in range(e, end):
        o = D.O[d, j]
        if j > e and o <= stop: return j, o
        if D.L[d, j] <= stop: return j, stop
        if j > e and o >= tgt: return j, o
        if D.H[d, j] >= tgt: return j, tgt
    return end, D.P[d, end]


def _orb(D, d, stop="mid", R=2):
    """One long ORB trade on day d (or None): the first 5-minute bar after the range that closes above the range high,
    filled at the next minute's open."""
    last = D.last[d]
    if last < OR_BARS * BAR - 1:
        return None
    hi, lo = D.H[d, :OR_BARS * BAR].max(), D.L[d, :OR_BARS * BAR].min()
    for k in range(OR_BARS, (LAST_FILL - BAR) // BAR + 1):
        e = (k + 1) * BAR
        if e < FIRST_FILL or e > min(LAST_FILL, last):
            break
        if D.C[d, e - 1] > hi:
            s = (hi + lo) / 2 if stop == "mid" else lo
            px = D.O[d, e]
            if px - s <= 0:
                return None
            end = min(FLAT, last + 1)
            x, xp = _exit(D, d, e, s, px + R * (px - s), end)
            return (d, e, max(x, e + 1), 1.0, _ret(1.0, px, xp))
    return None


def id_dts_orb(D, stop="mid", R=1):
    """day-trader-skill opening-range breakout: long the first 5-minute close above the 15-minute range, stop at
    the range midpoint or low, target R x risk, flat at 15:55."""
    return _trades([t for d in range(D.n) if (t := _orb(D, d, stop, R))])


SETUPS = ["breakout_20d", "bull_flag", "reclaim_ma20", "oversold_bounce"]


def setup_flags(close, setup):
    """flag[d] = the scanner setup holds on the closes of days < d (the previous session is the last close)."""
    c = pd.Series(np.asarray(close, float))
    prev = c.shift(1)  # prev[d] = the last close seen on day d
    if setup == "breakout_20d":  # last close above every close of the 20 sessions before it
        f = prev > c.shift(2).rolling(20).max()
    elif setup == "bull_flag":  # up >10% over the 10 sessions before the last 5; last 5 closes inside a 5% range
        up = c.shift(6) / c.shift(16) - 1
        w = c.shift(1).rolling(5)
        f = (up > 0.10) & ((w.max() - w.min()) / w.min() < 0.05)
    elif setup == "reclaim_ma20":  # close before under its 20-day average, the last close above it
        ma = c.rolling(20).mean().shift(1)
        f = (c.shift(2) < ma.shift(1)) & (prev > ma)
    elif setup == "oversold_bounce":  # Wilder RSI14 under 30 the session before, last session closed up
        f = (rsi(c, 14).shift(2) < 30) & (prev > c.shift(2))
    else:
        raise ValueError(setup)
    return f.fillna(False).values.astype(bool)


def id_dts_orb_setup(D, setup="breakout_20d", stop="mid", R=2):
    """The same breakout, only on days the previous session's daily closes show the scanner setup."""
    flag = setup_flags(D.close, setup)
    return _trades([t for d in range(D.n) if flag[d] and (t := _orb(D, d, stop, R))])


STRATEGIES = [
    {"id": "id_dts_orb", "name": "Day-Trader ORB", "fn": id_dts_orb, "grid": grid(stop=["mid", "low"], R=[1, 2]),
     "coin": True, "study": 7, "source": "day-trader-skill (Jakeschincariol, MIT), trader-entry/exit/scanner rules",
     "plain": "Buy the first 5-minute close above the opening 15-minute range, stop in the range, take profit at 1 or 2 times the risk."},
    {"id": "id_dts_orb_setup", "name": "Day-Trader ORB + Scanner", "fn": id_dts_orb_setup, "grid": grid(setup=SETUPS),
     "coin": True, "study": 7, "source": "day-trader-skill (Jakeschincariol, MIT), trader-entry/exit/scanner rules",
     "plain": "The same opening-range breakout, taken only after a daily scanner setup on the previous session."},
]


if __name__ == "__main__":
    S3.main(STRATEGIES, LOCKBOX, OUT, "INTRADAY_PLAN_7.md")
