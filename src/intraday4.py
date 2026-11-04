"""Intraday study 4 (INTRADAY_PLAN_4.md): seven more rules not tested in studies 1-3, on SPY and QQQ.
Same clock, costs, windows and tests as intraday.py; the runner is intraday3.main.

    python src/intraday4.py              # discovery on SPY and QQQ
    python src/intraday4.py --lockbox    # also run any candidate on XLV, XLY, XLI, XLP (downloads them)
"""
from __future__ import annotations

import numpy as np

import intraday as I
import intraday3 as S3
from intraday import _ret, _trades, grid
from intraday3 import _day_hl, _hold_to_close

LOCKBOX = ["XLV", "XLY", "XLI", "XLP"]
OUT = I.ROOT / "results" / "intraday4.json"


def _exit(D, d, e, s, stop, tgt=None):
    """Walk from entry bar e: stop or target fill at their level, or at a bar's open if it opens through
    them (never on the entry bar's open, which is the fill). Otherwise exit at the session close."""
    for j in range(e, D.last[d] + 1):
        o, hi, lo = D.O[d, j], D.H[d, j], D.L[d, j]
        if j > e and (o - stop) * s <= 0: return j, o
        if (lo if s > 0 else hi) * s <= stop * s: return j, stop
        if tgt is not None:
            if j > e and (o - tgt) * s >= 0: return j, o
            if (hi if s > 0 else lo) * s >= tgt * s: return j, tgt
    return D.last[d] + 1, D.close[d]


def _with_exit(D, d, e, s, stop, tgt=None):
    px = D.O[d, e]
    if (px - stop) * s <= 0 or (tgt is not None and (px - tgt) * s >= 0):
        return None  # entry already through the stop or the target
    x, xp = _exit(D, d, e, s, stop, tgt)
    return (d, e, max(x, e + 1), s, _ret(s, px, xp))


def gap_go(D, gap=0.005, confirm=5):
    """Trade with a gap bigger than `gap` if the first `confirm` minutes close in its direction; stop at
    yesterday's close (gap filled); else hold to the close."""
    rows = []
    for d in range(1, D.n):
        pc, o = D.prev_close[d], D.O[d, 0]
        g = o / pc - 1
        if abs(g) <= gap or D.last[d] <= confirm:
            continue
        s = 1.0 if g > 0 else -1.0
        if (D.C[d, confirm - 1] - o) * s <= 0:
            continue
        t = _with_exit(D, d, confirm, s, pc)
        if t:
            rows.append(t)
    return _trades(rows)


def pivot_revert(D, level=1, target="pivot"):
    """Fade the first touch of R1/S1 (or R2/S2) from yesterday's floor pivots; stop one level out,
    target the pivot or the close. Days that open beyond a level, or touch both in one bar, are skipped."""
    H, L = _day_hl(D)
    rows = []
    for d in range(1, D.n):
        h, l, c = H[d - 1], L[d - 1], D.close[d - 1]
        p = (h + l + c) / 3
        if level == 1:
            r, s_, r_out, s_out = 2 * p - l, 2 * p - h, p + (h - l), p - (h - l)
        else:
            r, s_, r_out, s_out = p + (h - l), p - (h - l), h + 2 * (p - l), l - 2 * (h - p)
        if not (s_ < D.O[d, 0] < r):
            continue
        last = D.last[d]
        up = np.flatnonzero(D.H[d, :last] >= r)
        dn = np.flatnonzero(D.L[d, :last] <= s_)
        mu, md = (up[0] if len(up) else 10 ** 6), (dn[0] if len(dn) else 10 ** 6)
        if mu == md:
            continue
        m, s, stop = (mu, -1.0, r_out) if mu < md else (md, 1.0, s_out)
        t = _with_exit(D, d, m + 1, s, stop, p if target == "pivot" else None)
        if t:
            rows.append(t)
    return _trades(rows)


def first_hour_reversal(D, thr=0.005, hold=0):
    """If 09:30-10:30 moved more than thr, trade against it from 10:30 for `hold` minutes (0 = to the close)."""
    rows = []
    for d in range(D.n):
        if D.last[d] < 61:
            continue
        r = D.C[d, 59] / D.O[d, 0] - 1
        if abs(r) <= thr:
            continue
        s = -1.0 if r > 0 else 1.0
        x = min(60 + hold, D.last[d] + 1) if hold else D.last[d] + 1
        rows.append((d, 60, x, s, _ret(s, D.P[d, 60], D.P[d, x])))
    return _trades(rows)


WINDOWS = {"open30": (0, 30), "morning": (30, 150), "midday": (150, 270), "afternoon": (270, 360),
           "last30": (-30, None), "last60": (-60, None)}


def time_of_day(D, window="last30"):
    """Long every day during one fixed window (negative start = minutes before the close)."""
    a0, b0 = WINDOWS[window]
    rows = []
    for d in range(D.n):
        end = D.last[d] + 1
        a = end + a0 if a0 < 0 else a0
        b = end if b0 is None else min(b0, end)
        if b - a < 5:
            continue
        rows.append((d, a, b, 1.0, _ret(1.0, D.P[d, a], D.P[d, b])))
    return _trades(rows)


def volume_spike_fade(D, k=5.0, hold=15, move=0.001):
    """Fade a 1-minute bar whose volume is above k x the previous 30 bars' average and whose move is above
    `move`, for `hold` minutes; from 10:00, one trade at a time."""
    rows = []
    for d in range(D.n):
        end = D.last[d] + 1
        cv = np.r_[0.0, np.cumsum(D.V[d, :end])]
        m = 30
        while m + 1 + hold <= end:
            avg = (cv[m] - cv[m - 30]) / 30
            r = D.C[d, m] / D.O[d, m] - 1
            if avg > 0 and D.V[d, m] > k * avg and abs(r) > move:
                s = -1.0 if r > 0 else 1.0
                e, x = m + 1, m + 1 + hold
                rows.append((d, e, x, s, _ret(s, D.P[d, e], D.P[d, x])))
                m = x
            else:
                m += 1
    return _trades(rows)


def crabel_stretch(D, mult=1.0, mode="ls", lookback=10):
    """Crabel: stretch = average over the last 10 days of min(high - open, open - low); trade the first
    close beyond open +/- mult x stretch to the close."""
    H, L = _day_hl(D)
    o0 = D.O[:, 0]
    st = np.minimum(H - o0, o0 - L)
    rows = []
    for d in range(lookback, D.n):
        x = mult * st[d - lookback:d].mean()
        up, dn = o0[d] + x, (o0[d] - x if mode == "ls" else -np.inf)
        c = D.C[d, :D.last[d]]
        hit = np.flatnonzero((c > up) | (c < dn))
        if len(hit):
            m = hit[0]
            rows.append(_hold_to_close(D, d, m, 1.0 if c[m] > up else -1.0))
    return _trades(rows)


def lunch_breakout(D, win="1130", stop="none"):
    """Range of 11:30-13:00 (or 12:00-13:30); trade the first close beyond it afterwards to the close,
    optionally stopped at the range's other end."""
    a, b = {"1130": (120, 210), "1200": (150, 240)}[win]
    rows = []
    for d in range(D.n):
        if D.last[d] <= b + 1:
            continue
        hi, lo = D.H[d, a:b].max(), D.L[d, a:b].min()
        c = D.C[d, b:D.last[d]]
        hit = np.flatnonzero((c > hi) | (c < lo))
        if not len(hit):
            continue
        m = b + hit[0]
        s = 1.0 if D.C[d, m] > hi else -1.0
        if stop == "none":
            rows.append(_hold_to_close(D, d, m, s))
        else:
            t = _with_exit(D, d, m + 1, s, lo if s > 0 else hi)
            if t:
                rows.append(t)
    return _trades(rows)


STRATEGIES = [
    {"id": "id_gap_go", "name": "Gap and Go", "fn": gap_go, "grid": grid(gap=[0.0025, 0.005, 0.01], confirm=[5, 15]),
     "coin": True, "study": 4, "source": "Practitioner (gap continuation)",
     "plain": "When the market gaps and the first minutes agree, trade with the gap."},
    {"id": "id_pivot_revert", "name": "Pivot Point Reversion", "fn": pivot_revert, "grid": grid(level=[1, 2], target=["pivot", "close"]),
     "coin": True, "study": 4, "source": "Floor-trader pivot points (practitioner)",
     "plain": "Fade the first touch of yesterday's pivot resistance or support levels."},
    {"id": "id_first_hour_reversal", "name": "First-Hour Reversal", "fn": first_hour_reversal, "grid": grid(thr=[0.0025, 0.005, 0.01], hold=[120, 0]),
     "coin": True, "study": 4, "source": "Practitioner (morning overreaction fade)",
     "plain": "After a big first hour, bet the rest of the day goes the other way."},
    {"id": "id_time_of_day", "name": "Time-of-Day Hold", "fn": time_of_day, "grid": grid(window=list(WINDOWS)),
     "coin": True, "study": 4, "source": "Heston, Korajczyk & Sadka (2010); practitioner closing ramp",
     "plain": "Own the market only during one fixed part of every day."},
    {"id": "id_volume_spike_fade", "name": "Volume Spike Fade", "fn": volume_spike_fade, "grid": grid(k=[5.0, 10.0], hold=[15, 30]),
     "coin": True, "study": 4, "source": "Practitioner (liquidity-shock reversal)",
     "plain": "When one minute has a huge burst of volume and a sharp move, bet it snaps back."},
    {"id": "id_crabel_stretch", "name": "Crabel Stretch Breakout", "fn": crabel_stretch, "grid": grid(mult=[1.0, 1.5, 2.0], mode=["ls", "long"]),
     "coin": True, "study": 4, "source": "Toby Crabel, Day Trading with Short Term Price Patterns and ORB (1990)",
     "plain": "Trade the first move from the open bigger than the market's usual opening 'stretch'."},
    {"id": "id_lunch_breakout", "name": "Midday Range Breakout", "fn": lunch_breakout, "grid": grid(win=["1130", "1200"], stop=["none", "range"]),
     "coin": True, "study": 4, "source": "Practitioner (midday range breakout)",
     "plain": "Trade the afternoon break out of the quiet lunchtime range."},
]


if __name__ == "__main__":
    S3.main(STRATEGIES, LOCKBOX, OUT, "INTRADAY_PLAN_4.md")
