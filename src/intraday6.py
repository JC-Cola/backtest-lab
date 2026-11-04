"""Intraday study 6 (INTRADAY_PLAN_6.md): a stop-loss and take-profit overlay on the study-1 noise-band breakout.
Same clock, costs, windows and tests as intraday.py; the runner is intraday3.main.

    python src/intraday6.py              # discovery on SPY and QQQ
    python src/intraday6.py --lockbox    # also run a candidate on XLV, XLY, XLI, XLP (downloads them)
"""
from __future__ import annotations

import json

import numpy as np

import intraday as I
import intraday3 as S3
from intraday import _ret, _trades, grid
from intraday3 import _day_hl

LOCKBOX = ["XLV", "XLY", "XLI", "XLP"]
OUT = I.ROOT / "results" / "intraday6.json"
_BASE_GRID = I.grid(lookback=[10, 14], check=[30, 60])
_cache: dict = {}


def atr_frac(D, n=14):
    """Mean of (high - low) / close over the previous n sessions; NaN until n sessions exist."""
    H, L = _day_hl(D)
    rng = (H - L) / D.close
    out = np.full(D.n, np.nan)
    for d in range(n, D.n):
        out[d] = rng[d - n:d].mean()
    return out


def overlay(D, t, stop_frac, R):
    """Stop at entry -/+ stop_frac[day]; target at R x the stop distance. Fill at the level, or at a bar's open if
    it opens through (never the entry bar's open); a bar touching both takes the stop. NaN stop: base exit kept."""
    ex, ret = t["exit_m"].copy(), t["ret"].copy()
    for i in range(len(ret)):
        d, e, x, s = t["day"][i], t["entry_m"][i], t["exit_m"][i], t["dir"][i]
        f = stop_frac[d]
        px = D.P[d, e]
        if not np.isfinite(f) or f <= 0 or not np.isfinite(px):
            continue
        stop, tgt = px * (1 - s * f), px * (1 + s * R * f)
        o, hi, lo = D.O[d, e:x], D.H[d, e:x], D.L[d, e:x]
        gap = np.zeros(len(o), bool)
        gap[1:] = ((o[1:] - stop) * s <= 0) | ((o[1:] - tgt) * s >= 0)
        hit_stop = ((lo if s > 0 else hi) * s <= stop * s)
        hit_tgt = ((hi if s > 0 else lo) * s >= tgt * s)
        k = np.flatnonzero(gap | hit_stop | hit_tgt)
        if not len(k):
            continue
        k = k[0]
        if gap[k]:
            xp = o[k]
        else:
            xp = stop if hit_stop[k] else tgt
        ex[i] = max(e + k, e + 1)
        ret[i] = _ret(s, px, xp)
    return {**t, "exit_m": ex, "ret": ret}


def _base_params(D):
    """Study-1 lookback/check for D's market (results/intraday.json); a market it never covered (the lockbox) is
    tuned afresh on the tuning years by Sharpe, as study 1 did. A Days with no symbol gets the rule's defaults."""
    sym = getattr(D, "symbol", None)
    if sym is None:
        return 14, 30
    if sym not in _cache:
        row = next((r for r in json.loads((I.ROOT / "results" / "intraday.json").read_text())["results"]
                    if r["strategy"] == "id_noise_breakout" and r["asset"] == sym), None)
        if row:
            _cache[sym] = tuple(row["params"][k] for k in ("lookback", "check"))
        else:
            sl = slice(0, int(D.dates.searchsorted(np.datetime64(I.IS_END), "right")))
            runs = [I.noise_breakout(D, **p) for p in _BASE_GRID]
            sh = [I.sharpe(I.daily_returns(D, t, sl)) for t in runs]
            n = [int(I.in_window(t, sl).sum()) for t in runs]
            ok = [i for i in range(len(runs)) if n[i] >= I.MIN_IS_TRADES] or list(range(len(runs)))
            b = max(ok, key=lambda i: sh[i])
            _cache[sym] = (_BASE_GRID[b]["lookback"], _BASE_GRID[b]["check"])
    return _cache[sym]


def noise_sl_tp(D, m=0.25, R=2.0, lookback=None, check=None):
    """Study-1 noise-band breakout entries (per-market tuned unless given) with the stop/target overlay;
    stop = m x the 14-day average range, target = R x the stop distance."""
    if lookback is None or check is None:
        lookback, check = _base_params(D)
    return overlay(D, I.noise_breakout(D, lookback, check), m * atr_frac(D), R)


def _base_2x(row):
    """Candidate rule: also beat the plain noise-band breakout's test-years Sharpe at 2x costs on that market."""
    base = next(r for r in json.loads((I.ROOT / "results" / "intraday.json").read_text())["results"]
                if r["strategy"] == "id_noise_breakout" and r["asset"] == row["asset"])["oos_cost2x_sharpe"]
    row["base_cost2x_sharpe"] = base
    row["beats_base"] = bool(row["oos_cost2x_sharpe"] > base)
    return row["beats_base"]


STRATEGIES = [
    {"id": "id_noise_sl", "name": "Noise Breakout + Stop/Target", "fn": noise_sl_tp, "grid": grid(m=[0.25, 0.5], R=[2, 3, 4]),
     "coin": True, "study": 6, "source": "Zarattini, Aziz & Barbon (2024) entries; ATR stop and R-multiple target are standard practice",
     "plain": "The noise-band breakout, but exit early at a stop-loss or a take-profit set from the recent daily range."},
]


if __name__ == "__main__":
    S3.main(STRATEGIES, LOCKBOX, OUT, "INTRADAY_PLAN_6.md", extra=_base_2x)
