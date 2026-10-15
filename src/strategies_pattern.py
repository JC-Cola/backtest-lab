"""Batch 4: price patterns, long/flat, decided on the close from bars <= t only."""
from __future__ import annotations

import numpy as np
import pandas as pd

import calendar_util as cu
from indicators import latch, rsi, sma


def _s(x, idx):
    return pd.Series(np.asarray(x, float), index=idx)


def _hold_for(trig, h, exit_=None) -> np.ndarray:
    """Long for h bars after each trigger (re-triggers extend), or until exit_ fires earlier."""
    trig = np.asarray(trig, bool)
    ex = np.zeros(len(trig), bool) if exit_ is None else np.asarray(exit_, bool)
    out = np.zeros(len(trig))
    left = 0
    for t in range(len(trig)):
        if left > 0 and ex[t]:
            left = 0
        if trig[t]:
            left = h
        out[t] = 1.0 if left > 0 else 0.0
        left -= 1
    return out


def _up200(df):
    return (df.Close > sma(df.Close, 200)).values


def down_days(df, n=3, trend=True):
    """n consecutive lower closes -> buy (optionally only above SMA200); exit on the first up close."""
    c = df.Close
    dn = c < c.shift(1)
    run = dn.astype(int).groupby((~dn).cumsum()).cumsum().values
    enter = (run >= n) & (_up200(df) if trend else True)
    return _s(latch(enter, (c > c.shift(1)).values), df.index)


def double_seven(df, n=7):
    """Connors 'Double 7s': above SMA200, buy an n-day closing low, sell an n-day closing high."""
    c = df.Close
    enter = (c <= c.rolling(n).min()).values & _up200(df)
    return _s(latch(enter, (c >= c.rolling(n).max()).values), df.index)


def cum_rsi(df, w=2, x=35):
    """Connors cumulative RSI: sum of RSI(2) over w days < x, above SMA200; exit RSI(2) > 65."""
    r = rsi(df.Close, 2)
    enter = (r.rolling(w).sum() < x).values & _up200(df)
    return _s(latch(enter, (r > 65).values), df.index)


def nr7(df, n=7, h=3):
    """Narrow range (Toby Crabel): today's range is the smallest of n -> long h sessions if the
    close is above SMA200 and in the upper half of today's bar."""
    rng = df.High - df.Low
    narrow = (rng <= rng.rolling(n).min()).values
    upper = (df.Close > (df.High + df.Low) / 2).values
    return _s(_hold_for(narrow & upper & _up200(df), h), df.index)


def inside_day(df, h=3, trend=True):
    """Inside day (high < prior high, low > prior low) -> long h sessions (optionally SMA200 filter)."""
    ins = ((df.High < df.High.shift(1)) & (df.Low > df.Low.shift(1))).values
    return _s(_hold_for(ins & (_up200(df) if trend else True), h), df.index)


def week_reversal(df, x=0.02):
    """Weekly short-term reversal: on the last session of a week, if the week (last 5 sessions)
    fell more than x, hold the whole next week. Week end is a calendar fact."""
    ext = cu.extended_index(df.index)
    wk = ext.to_period("W-FRI" if not cu.is_crypto(df.index) else "W-SUN")
    last = np.r_[wk[1:] != wk[:-1], True][: len(df)]
    k = 7 if cu.is_crypto(df.index) else 5
    r = (df.Close / df.Close.shift(k) - 1).values
    sig = pd.Series(np.where(last, (r < -x).astype(float), np.nan), index=df.index)
    return sig.ffill().fillna(0.0)


def high_52w(df, x=0.05):
    """52-week-high proximity (George & Hwang 2004): long while close is within x of its
    252-day high; flat when it falls further away."""
    hh = df.Close.rolling(252).max()
    return _s(df.Close >= (1 - x) * hh, df.index)


def zscore(df, n=20, thr=2.0):
    """Z-score reversion: buy when (close - SMA n)/std n < -thr, exit when it reaches 0."""
    c = df.Close
    z = (c - sma(c, n)) / c.rolling(n).std()
    return _s(latch((z < -thr).values, (z > 0).values), df.index)


def ma_envelope(df, n=20, x=0.03):
    """Moving-average envelope: buy a close x below SMA(n), exit at the average."""
    c = df.Close
    m = sma(c, n)
    return _s(latch((c < (1 - x) * m).values, (c > m).values), df.index)


def gap_recovery(df, g=0.01, h=1):
    """Gap down at the open by more than g that has not recovered by the close -> long h sessions."""
    pc = df.Close.shift(1)
    trig = ((df.Open < (1 - g) * pc) & (df.Close < pc)).values
    return _s(_hold_for(trig, h), df.index)


def vol_breakout(df, k=0.5, h=1):
    """Larry Williams volatility breakout, close-confirmed variant (the engine cannot fill
    intraday stops): if today closed above open + k x yesterday's range, long h sessions."""
    prev_rng = (df.High - df.Low).shift(1)
    trig = (df.Close > df.Open + k * prev_rng).values
    return _s(_hold_for(trig, h), df.index)
