"""Mean-reversion and seasonal strategies. (df, **params) -> long-only position Series."""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import latch, rsi, sma


def rsi2_connors(df, x=10):
    """Connors RSI(2): long when RSI(2) < x and close > SMA200; exit close > SMA5."""
    c = df.Close
    enter = (rsi(c, 2) < x) & (c > sma(c, 200))
    return pd.Series(latch(enter, c > sma(c, 5)), index=df.index)


def rsi14_reel(df, stop=0.05, sma_filter=True):
    """Instagram-reel rule: enter RSI(14)<30 (and close>SMA200 if sma_filter); exit RSI(14)>60
    or close <= entry*(1-stop). Entry reference = signal-bar close."""
    c = df.Close.values
    r = rsi(df.Close, 14).values
    ok = (c > sma(df.Close, 200).values) if sma_filter else np.ones(len(c), bool)
    out = np.zeros(len(c))
    pos, ep = 0.0, 0.0
    for t in range(len(c)):
        if pos:
            if r[t] > 60 or c[t] <= ep * (1 - stop):
                pos = 0.0
        elif r[t] < 30 and ok[t]:
            pos, ep = 1.0, c[t]
        out[t] = pos
    return pd.Series(out, index=df.index)


def bollinger_revert(df, k=2.0):
    """Bollinger band reversion: long when close < SMA20 - k*std20; exit close > SMA20."""
    c = df.Close
    m = sma(c, 20)
    lower = m - k * c.rolling(20).std(ddof=0)
    return pd.Series(latch(c < lower, c > m), index=df.index)


def ibs(df, a=0.2, b=0.7):
    """Internal bar strength (close-low)/(high-low): long when IBS < a, exit when IBS > b."""
    rng = (df.High - df.Low).replace(0, np.nan)
    v = ((df.Close - df.Low) / rng).fillna(0.5)
    return pd.Series(latch(v < a, v > b), index=df.index)


def nday_low_dip(df, n=5, h=5):
    """Buy the N-day closing low above SMA200 (Connors-style); exit after h bars or close > SMA5."""
    c = df.Close
    enter = ((c <= c.rolling(n).min()) & (c > sma(c, 200))).values
    up = (c > sma(c, 5)).values
    out = np.zeros(len(c))
    pos, et = 0.0, 0
    for t in range(len(c)):
        if pos:
            if t - et >= h or up[t]:
                pos = 0.0
        elif enter[t]:
            pos, et = 1.0, t
        out[t] = pos
    return pd.Series(out, index=df.index)


def _month_ranks(idx):
    """(rank_from_start, rank_from_end) of each bar within its month, 0-based.

    Uses actual index positions (known trading calendar - acceptable, no price data).
    The last, still-incomplete data month is extended by the remaining calendar
    business days so the final bars are not mistaken for month-end.
    """
    key = idx.year * 100 + idx.month
    pos = pd.Series(np.arange(len(idx)))
    start = pos.groupby(key).cumcount().values
    end = pos.groupby(key).cumcount(ascending=False).values.copy()
    last = idx[-1]
    crypto = (idx.dayofweek >= 5).mean() > 0.1
    month_end = last + pd.offsets.MonthEnd(0)
    if crypto:
        remaining = (month_end - last).days
    else:
        remaining = len(pd.bdate_range(last + pd.Timedelta(days=1), month_end))
    end[key == key[-1]] += remaining
    return start, end


def turn_of_month(df, n=2, m=2):
    """Turn-of-month effect (Ariel 1987; Lakonishok & Smidt 1988): long during the last n and
    first m trading days of each month. Calendar-only information."""
    start, end = _month_ranks(df.index)
    return pd.Series(((end < n) | (start < m)).astype(float), index=df.index)
