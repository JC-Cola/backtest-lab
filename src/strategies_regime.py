"""Batch 2: regime filters. Long/flat. Cross-asset inputs come from `ctx` (closes aligned to the
traded market's dates, forward-filled only). NaN context (series not started yet) -> flat."""
from __future__ import annotations

import numpy as np
import pandas as pd

import calendar_util as cu
from indicators import sma


def _s(x, idx):
    return pd.Series(np.asarray(x, float), index=idx)


def _month_end(df) -> np.ndarray:
    """True on the last session of each month (calendar fact, from the extended calendar)."""
    ext = cu.extended_index(df.index)
    _, e = cu.month_ranks(ext)
    return e[: len(df)] == 0


def _monthly(df, sig_at_month_end) -> pd.Series:
    """Decide only on month-end closes and hold the decision until the next month-end."""
    me = _month_end(df)
    v = pd.Series(np.where(me, np.asarray(sig_at_month_end, float), np.nan), index=df.index)
    return v.ffill().fillna(0.0)


def vix_term(df, ctx, thr=1.0):
    """VIX term structure: long while VIX / VIX3M < thr (contango = calm regime)."""
    r = ctx["^VIX"] / ctx["^VIX3M"]
    return _s(r < thr, df.index)


def vix_vs_avg(df, ctx, n=50, k=1.0):
    """Long while VIX is below k x its n-day average (volatility not elevated)."""
    v = ctx["^VIX"]
    return _s(v < k * sma(v, n), df.index)


def vix_spike(df, ctx, x=0.3, h=10):
    """VIX spike reversion: when VIX closes more than x above its 20-day average, buy and hold
    for h sessions (fear spikes tend to mean-revert)."""
    v = ctx["^VIX"]
    trig = (v > (1 + x) * sma(v, 20)).values
    out = np.zeros(len(df))
    left = 0
    for t in range(len(df)):
        if trig[t]:
            left = h
        out[t] = 1.0 if left > 0 else 0.0
        left -= 1
    return _s(out, df.index)


def rv_regime(df, n=20, q=0.7):
    """Realized-vol regime: long while n-day realized vol is below its trailing 1-year q-quantile."""
    rv = np.log(df.Close).diff().rolling(n).std()
    thr = rv.rolling(252, min_periods=200).quantile(q)
    return _s(rv < thr, df.index)


def credit_trend(df, ctx, n=50):
    """Credit risk appetite: long while HYG/IEF is above its n-day average."""
    r = ctx["HYG"] / ctx["IEF"]
    return _s(r > sma(r, n), df.index)


def yield_curve(df, ctx, thr=0.0, mode="level"):
    """10y minus 3m Treasury yield. 'level': long while the spread > thr. 'steepening': long
    while the spread is above its 63-day average and > thr - 1."""
    sp = ctx["^TNX"] - ctx["^IRX"]
    if mode == "level":
        return _s(sp > thr, df.index)
    return _s((sp > sma(sp, 63)) & (sp > thr - 1), df.index)


def bond_momentum(df, ctx, n=50, src="^TNX"):
    """Bond trend as a macro filter: long while the 10y yield is below its n-day average
    (src='^TNX', falling yields) or while LQD is above its n-day average (src='LQD')."""
    x = ctx[src]
    sig = x < sma(x, n) if src == "^TNX" else x > sma(x, n)
    return _s(sig, df.index)


def dollar_trend(df, ctx, n=100, side="weak"):
    """Dollar trend (UUP): long while the dollar is below ('weak') / above ('strong') its n-day average."""
    u = ctx["UUP"]
    sig = u < sma(u, n) if side == "weak" else u > sma(u, n)
    return _s(sig, df.index)


def faber_monthly(df, months=10):
    """Faber (2007) monthly timing: at each month-end, long next month if the close is above the
    average of the last `months` month-end closes."""
    me = _month_end(df)
    mc = df.Close[me]
    above = (mc > mc.rolling(months).mean()).reindex(df.index)
    return _monthly(df, above.fillna(False).values)


def antonacci_abs(df, ctx, months=12):
    """Antonacci absolute momentum: at each month-end, long next month if the asset's trailing
    `months` return beats T-bills over the same period (3m yield, ^IRX, accrued monthly)."""
    me = _month_end(df)
    mc = df.Close[me]
    ret = mc / mc.shift(months) - 1
    bill = (1 + ctx["^IRX"][me] / 100 / 12).rolling(months).apply(np.prod, raw=True) - 1
    sig = (ret > bill).reindex(df.index)
    return _monthly(df, sig.fillna(False).values)
