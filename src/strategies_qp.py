"""Batch 6 (Quantpedia), single-market rules not already covered by earlier batches.
(Quantpedia entries already in the pool: time-series momentum, turn of the month, pre-holiday,
FOMC meeting effect, option-expiration week, market seasonality / Halloween.)"""
from __future__ import annotations

import numpy as np
import pandas as pd

import calendar_util as cu
from strategies_calendar import _pos
from strategies_regime import _month_end, _monthly
from strategies_tv import atr


def january_barometer(df, rule="january"):
    """Quantpedia 'January Barometer': always hold in January; from February to December stay
    invested only if January's return was positive ('january'), or if the first five sessions
    of January were positive ('first5'). Decisions use closes up to the decision bar only."""
    idx = df.index
    ext = cu.extended_index(idx)
    s_rank, _ = cu.month_ranks(ext)
    c = df.Close.values
    me = _month_end(df)
    n = len(df)
    out = np.zeros(n)
    year_ok, ref, cur_year = None, None, None
    for t in range(n):
        d = idx[t]
        if d.year != cur_year:
            cur_year, year_ok, ref = d.year, None, (c[t - 1] if t else np.nan)
        if d.month == 1 and rule == "first5" and s_rank[t] == 4 and np.isfinite(ref):
            year_ok = c[t] > ref
        if d.month == 1 and rule == "january" and me[t] and np.isfinite(ref):
            year_ok = c[t] > ref
        # position for the NEXT session (calendar of ext): January sessions always held
        nxt = ext[t + 1]
        out[t] = 1.0 if nxt.month == 1 else (1.0 if year_ok else 0.0)
    return pd.Series(out, index=idx)


def payday(df, day=16, n=1):
    """Quantpedia 'Payday Anomaly': hold the market for n sessions starting with the first
    session on or after calendar day `day` of each month."""
    def hold(ext):
        key = ext.year * 100 + ext.month
        first = pd.Series(ext.day >= day, index=ext)
        m = np.zeros(len(ext), bool)
        start = first.groupby(key).cumsum().values  # 1 on the first qualifying session, 2 next...
        m[(start >= 1) & (start <= n)] = True
        return m
    return _pos(df, hold)


def oil_predicts(df, ctx, mode="regression", min_months=36):
    """Quantpedia 'Crude Oil Predicts Equity Returns': each month regress the market's monthly
    return on the previous month's oil return (expanding window of past months only); be invested
    next month if the forecast beats the T-bill rate ('regression'), else cash. 'sign' variant:
    invested after a month in which oil fell. Oil = WTI front-month (CL=F), forward-filled."""
    me = _month_end(df)
    mc = df.Close[me]
    oil = ctx["CL=F"][me]
    rf = ctx["^IRX"][me] / 100 / 12
    eq = mc.pct_change().values
    oc = oil.pct_change().values
    sig = np.zeros(len(mc))
    for j in range(len(mc)):
        if mode == "sign":
            sig[j] = 1.0 if np.isfinite(oc[j]) and oc[j] < 0 else 0.0
            continue
        # pairs (oil return in month i-1, equity return in month i) fully known at month j
        x, y = oc[1:j], eq[2 : j + 1]
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < min_months or not np.isfinite(oc[j]):
            sig[j] = 1.0  # not enough history yet: default to invested (paper's market portfolio)
            continue
        b, a = np.polyfit(x[ok], y[ok], 1)
        sig[j] = 1.0 if a + b * oc[j] > rf.values[j] else 0.0
    full = np.full(len(df), np.nan)
    full[me] = sig
    return _monthly(df, full)


def ath_trend(df, k=10):
    """Quantpedia 'Trend-Following Effect in Stocks': enter when the close is at its all-time
    closing high (of the data so far); exit when the close falls below the highest close since
    entry minus k x ATR(10) (trailing stop)."""
    c = df.Close.values
    a = atr(df, 10).values
    run_max = np.maximum.accumulate(c)
    out = np.zeros(len(c))
    pos, hi = 0.0, 0.0
    for t in range(len(c)):
        if pos:
            hi = max(hi, c[t])
            if np.isfinite(a[t]) and c[t] < hi - k * a[t]:
                pos = 0.0
        elif c[t] >= run_max[t] and t > 252:
            pos, hi = 1.0, c[t]
        out[t] = pos
    return pd.Series(out, index=df.index)
