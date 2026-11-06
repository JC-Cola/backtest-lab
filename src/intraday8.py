"""Intraday study 8 (INTRADAY_PLAN_8.md): how often to check the noise-band breakout (5, 15, 30 or 60 minutes).
The strategy is intraday.noise_breakout, unchanged. Same clock, costs, windows and tests as intraday.py; the runner is
intraday3.main, with an extra candidate rule (must beat the hourly setting) and the plan's secondary readout.

    python src/intraday8.py              # discovery on SPY and QQQ + secondary readout
    python src/intraday8.py --lockbox    # also run a candidate on IWM, DIA
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import intraday as I
import intraday3 as S3
from intraday import daily_returns, grid, in_window, noise_breakout, sharpe
from stats import deflated_sharpe

LOCKBOX = ["IWM", "DIA"]
OUT = I.ROOT / "results" / "intraday8.json"
CHECKS = [5, 15, 30, 60]
LIVE_LOOKBACK = 14

STRATEGIES = [
    {"id": "id_noise_freq", "name": "Noise-Band Check Frequency", "fn": noise_breakout,
     "grid": grid(check=CHECKS, lookback=[10, 14]), "coin": True, "study": 8,
     "source": "Zarattini, Aziz & Barbon (2024), checked every 5, 15, 30 or 60 minutes (study 8)",
     "plain": "The noise-band breakout, looking at the price every 5, 15, 30 or 60 minutes instead of only hourly."}]

_days: dict = {}
_cache: dict = {}


def days(sym):
    if sym not in _days:
        _days[sym] = I.Days(sym)
    return _days[sym]


def _window(D):
    return slice(int(D.dates.searchsorted(pd.Timestamp(I.OOS_START))), D.n)


def _trades_for(D, sym, check, lookback):
    key = (sym, check, lookback)
    if key not in _cache:
        _cache[key] = noise_breakout(D, lookback=lookback, check=check)
    return _cache[key]


def _daily(D, sym, check, lookback, mult):
    """Test-window daily returns at mult x the base cost (mult 2 subtracts one more round trip per trade)."""
    X = _window(D)
    t = _trades_for(D, sym, check, lookback)
    r = daily_returns(D, t, X)
    if mult == 2:
        tx = {k: v[in_window(t, X)] for k, v in t.items()}
        np.subtract.at(r, tx["day"] - X.start, 2 * I.COST)
    return r


def _maxdd(r):
    eq = np.cumprod(1 + r)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def _stats(r):
    return {"sharpe": sharpe(r), "total": float(np.prod(1 + r) - 1), "worst_day": float(r.min()), "max_drawdown": _maxdd(r)}


def secondary(sym):
    D = days(sym)
    X = _window(D)
    bh = np.r_[0.0, D.close[1:] / D.close[:-1] - 1][X]
    out = {"asset": sym, "lookback": LIVE_LOOKBACK, "buy_hold": _stats(bh), "rows": []}
    base = {m: _daily(D, sym, 60, LIVE_LOOKBACK, m) for m in (1, 2)}
    for check in CHECKS:
        t = _trades_for(D, sym, check, LIVE_LOOKBACK)
        tx = {k: v[in_window(t, X)] for k, v in t.items()}
        hold = (tx["exit_m"] - tx["entry_m"]).astype(float)
        row = {"check": check, "trades": int(len(tx["ret"])),
               "win_rate": float((tx["ret"] > 0).mean()), "avg_hold_min": float(hold.mean())}
        for m, key in ((1, "cost1x"), (2, "cost2x")):
            r = _daily(D, sym, check, LIVE_LOOKBACK, m)
            d = r - base[m]
            row[key] = _stats(r) | {"avg_trade_bp": float((tx["ret"] - (2 * I.COST if m == 2 else 0)).mean() * 1e4),
                                    "vs_hourly_mean_daily_bp": float(d.mean() * 1e4),
                                    "vs_hourly_t": float(d.mean() / d.std(ddof=1) * np.sqrt(len(d))) if d.std(ddof=1) > 1e-12 else 0.0}
        out["rows"].append(row)
    return out


def hourly_2x_sharpe(sym, lookback):
    return sharpe(_daily(days(sym), sym, 60, lookback, 2))


def extra(row):
    """Candidate must check more often than hourly and beat the hourly setting (same lookback, same market) at 2x costs."""
    p = row["params"]
    h = hourly_2x_sharpe(row["asset"], p["lookback"])
    row["hourly_same_lookback_cost2x_sharpe"] = h
    return bool(p["check"] < 60 and row["oos_cost2x_sharpe"] > h)


def dsr(sym, row):
    """Deflated Sharpe of the tuned setting's test-year returns vs the 8 grid settings' tuning-year daily Sharpes."""
    D = days(sym)
    I_ = slice(0, int(D.dates.searchsorted(pd.Timestamp(I.IS_END), "right")))
    trials = []
    for p in STRATEGIES[0]["grid"]:
        r = daily_returns(D, _trades_for(D, sym, p["check"], p["lookback"]), I_)
        trials.append(r.mean() / r.std(ddof=1))
    p = row["params"]
    return deflated_sharpe(_daily(D, sym, p["check"], p["lookback"], 1), np.array(trials))


if __name__ == "__main__":
    S3.main(STRATEGIES, LOCKBOX, OUT, "INTRADAY_PLAN_8.md", extra=extra)
    res = json.loads(OUT.read_text())
    for r in res["results"]:
        r["deflated_sharpe"] = dsr(r["asset"], r)
    res["secondary"] = [secondary(s) for s in S3.DISCOVERY]
    OUT.write_text(json.dumps(res, default=float))
    for sec in res["secondary"]:
        print(f"\n{sec['asset']} lookback {sec['lookback']} test years; buy&hold {sec['buy_hold']}")
        for r in sec["rows"]:
            for k in ("cost1x", "cost2x"):
                c = r[k]
                print(f"  check {r['check']:2d} {k}: Sh {c['sharpe']:5.2f} tot {c['total']*100:7.1f}% trades {r['trades']:5d} win {r['win_rate']*100:4.1f}% "
                      f"hold {r['avg_hold_min']:5.1f} avg {c['avg_trade_bp']:6.2f}bp worst {c['worst_day']*100:5.2f}% dd {c['max_drawdown']*100:6.1f}% "
                      f"vsHourly {c['vs_hourly_mean_daily_bp']:5.2f}bp t={c['vs_hourly_t']:5.2f}")
