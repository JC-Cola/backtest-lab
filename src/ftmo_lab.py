"""FTMO 2-Step tables for the Strategy Lab's daily strategies and for the live 50/50 mix, plus the page's
"show only what made money" filter. Used by build_lab.py; every number is on the test years only.

Daily strategies (engine.returns_np convention: the overnight gap is held at the position decided two closes
back, the session at the one decided at the last close): a day's worst moment is taken at its low,
  low_t = (1 + p2 * (open_t / close_t-1 - 1)) * (1 + p1 * (low_t / open_t - 1)) - 1.
The 50/50 mix is the live paper account: the daily strategies share 50% equally, the noise-band breakout on
SPY and QQQ gets 25% each. Its worst moment adds the parts' own worst moments, which is never better than the
true combined low. Bitcoin's weekend days are folded into the next stock session.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import ftmo
import intraday as I
from engine import returns_np


def daily_paths(df, pos, cost_bps):
    o, h, l, c = (df[k].values.astype(float) for k in ("Open", "High", "Low", "Close"))
    p = np.nan_to_num(np.asarray(pos, float))
    p1, p2 = np.r_[0.0, p[:-1]], np.r_[0.0, 0.0, p[:-2]]
    on = np.r_[0.0, o[1:] / c[:-1] - 1]
    fin = returns_np(o, c, p, cost_bps)
    low = np.minimum(0.0, np.minimum(p2 * on, (1 + p2 * on) * (1 + p1 * (l / o - 1)) - 1))
    return fin, low


def daily_table(df, pos, cost_bps, oos_start):
    fin, low = daily_paths(df, pos, cost_bps)
    return ftmo.table_from_paths(fin, low, int(df.index.searchsorted(pd.Timestamp(oos_start))))


def _onto(dates, fin, low, cal):
    """Fold a daily series onto the stock calendar `cal` (weekend crypto days go to the next session)."""
    idx = np.clip(cal.searchsorted(dates), 0, len(cal) - 1)
    ok = (dates >= cal[0]) & (dates <= cal[-1])
    f = pd.Series(fin[ok]).groupby(idx[ok]).sum().reindex(range(len(cal)), fill_value=0.0).values
    lo = pd.Series(low[ok]).groupby(idx[ok]).min().reindex(range(len(cal)), fill_value=0.0).values
    return f, lo


def mix(daily, noise, days, bench_sym="SPY"):
    """daily: list of (df, pos, cost_bps); noise: {symbol: trades dict}; days: {symbol: intraday.Days}."""
    D0 = days[bench_sym]
    X = int(D0.dates.searchsorted(pd.Timestamp(I.OOS_START)))
    cal = D0.dates[X:]
    fin, low, fin2 = np.zeros(len(cal)), np.zeros(len(cal)), np.zeros(len(cal))
    for df, pos, cost in daily:
        f, lo = daily_paths(df, pos, cost)
        f2 = returns_np(df.Open.values, df.Close.values, pos, 2 * cost)
        dates = df.index.tz_localize(None) if df.index.tz is not None else df.index
        a, b = _onto(dates, f, lo, cal)
        fin += 0.5 * a / len(daily); low += 0.5 * b / len(daily)
        fin2 += 0.5 * _onto(dates, f2, lo, cal)[0] / len(daily)
    for sym, t in noise.items():
        D = days[sym]
        assert D.dates.equals(D0.dates)
        f, lo = ftmo.day_paths(D, t)
        extra = np.zeros(D.n)
        np.add.at(extra, t["day"], 2 * I.COST)
        w = 0.5 / len(noise)
        fin += w * f[X:]; low += w * lo[X:]; fin2 += w * (f - extra)[X:]
    bh = np.r_[0.0, D0.close[1:] / D0.close[:-1] - 1][X:]
    return {"name": "50/50 mix (the live paper account)",
            "members": [["Daily strategies, equal shares", "50%"]] + [["id_noise_breakout", f"{s}, 25%"] for s in noise],
            "oos": {"sharpe": I.sharpe(fin), "cagr": I.cagr(fin), "total": float(np.prod(1 + fin) - 1)},
            "oos_cost2x_sharpe": I.sharpe(fin2), "buy_hold_oos": {"sharpe": I.sharpe(bh), "cagr": I.cagr(bh)},
            "coin_flip": None, "ftmo": ftmo.table_from_paths(fin, low, 0)}


def test_total(entry, markets):
    """Compounded test-year return of an entry's chosen setting, after costs."""
    mk = markets[entry["market"]]
    a = next((i for i, d in enumerate(mk["dates"]) if d >= mk["oos_start"]), len(mk["dates"]))
    r = np.nan_to_num(np.array(entry["combos"][entry["chosen"]]["r"][a:], float))
    return float(np.prod(1 + r) - 1)


def keep_positive(book, markets):
    """Drop strategies that lost money on their test years; return (kept, hidden names)."""
    kept, hidden = [], []
    for b in book:
        (kept if test_total(b, markets) > 0 else hidden).append(b)
    return kept, [f"{b['name']} ({b['market'].replace('-1m', '')})" for b in hidden]
