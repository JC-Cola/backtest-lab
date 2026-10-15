"""Batch 5: combinations of earlier ideas. CHOSEN AFTER SEEING the batch 0-4 results (and the
lockbox outcomes of WEAK rows), so their p-values are optimistic even beyond what the
Benjamini-Hochberg pool corrects for. Built only from earlier causal components."""
from __future__ import annotations

import numpy as np
import pandas as pd

import strategies_calendar as cal
import strategies_tv as tv
from indicators import latch, rsi, sma


def _s(x, idx):
    return pd.Series(np.asarray(x, float), index=idx)


def _ibs(df):
    return ((df.Close - df.Low) / (df.High - df.Low).replace(0, np.nan)).fillna(0.5)


def _wr(df, n=2):
    hh, ll = df.High.rolling(n).max(), df.Low.rolling(n).min()
    return -100 * (hh - df.Close) / (hh - ll)


def _dip_votes(df):
    """Number of classic oversold signals firing today (IBS, %R(2), RSI(2), 2 down closes)."""
    c = df.Close
    v = (_ibs(df) < 0.2).astype(int) + (_wr(df) < -90).astype(int) + (rsi(c, 2) < 10).astype(int)
    v += ((c < c.shift(1)) & (c.shift(1) < c.shift(2))).astype(int)
    return v


def dip_vote(df, m=2, trend=True):
    """Dip-buy consensus: enter when at least m of 4 oversold signals agree (optionally above
    SMA200); exit when IBS > 0.7 or close > SMA5."""
    c = df.Close
    enter = (_dip_votes(df) >= m).values & ((c > sma(c, 200)).values if trend else True)
    ex = ((_ibs(df) > 0.7) | (c > sma(c, 5))).values
    return _s(latch(enter, ex), df.index)


def dip_calm(df, ctx, a=0.2, thr=1.0):
    """IBS dip-buy only while the VIX curve is in contango (VIX / VIX3M < thr); exit IBS > 0.7."""
    calm = (ctx["^VIX"] / ctx["^VIX3M"] < thr).values
    v = _ibs(df)
    return _s(latch((v < a).values & calm, (v > 0.7).values), df.index)


def dip_lowvol(df, a=0.2, q=0.7):
    """IBS dip-buy only in a low realized-vol regime (20d vol below its 1y q-quantile)."""
    rv = np.log(df.Close).diff().rolling(20).std()
    low = (rv < rv.rolling(252, min_periods=200).quantile(q)).values
    v = _ibs(df)
    return _s(latch((v < a).values & low, (v > 0.7).values), df.index)


def dip_or_calendar(df, a=0.2, cal_parts="tom"):
    """Union: in the market during an IBS dip trade OR a calendar window (turn of month, or
    turn of month + pre-holiday)."""
    v = _ibs(df)
    dip = latch((v < a).values, (v > 0.7).values)
    parts = ("tom",) if cal_parts == "tom" else ("tom", "holiday")
    calp = cal.calendar_composite(df, parts=parts).values
    return _s(np.maximum(dip, calp), df.index)


def trend_cloud(df, n=100, cloud=(9, 26, 52)):
    """Trend agreement: long while close > SMA(n) AND the Ichimoku condition holds."""
    t, k, s = cloud
    ich = tv.ichimoku(df, t=t, k=k, s=s).values
    return _s((df.Close > sma(df.Close, n)).values & (ich > 0), df.index)


def trend_dip(df, n=200, x=10):
    """Buy RSI(2) < x dips only while price is above SMA(n), plus stay long while the trend holds
    after a dip entry until close < SMA(n) (dip entry, trend exit)."""
    c = df.Close
    up = (c > sma(c, n)).values
    return _s(latch((rsi(c, 2) < x).values & up, ~up), df.index)
