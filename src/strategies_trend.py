"""Trend / breakout strategies. Each: (df, **params) -> position Series in [-1, 1].

Position at bar t uses data up to and including close t; it is traded at open t+1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import ann_factor, ema, latch, sma


def buy_hold(df):
    """Benchmark: always long 1.0."""
    return pd.Series(1.0, index=df.index)


def sma_cross(df, fast=50, slow=200):
    """Classic golden-cross: long when SMA(fast) > SMA(slow), else flat."""
    f, s = sma(df.Close, fast), sma(df.Close, slow)
    return (f > s).astype(float)


def dual_ma_long_short(df, fast=50, slow=200):
    """sma_cross variant: short (-1) when SMA(fast) < SMA(slow)."""
    f, s = sma(df.Close, fast), sma(df.Close, slow)
    return pd.Series(np.where(f > s, 1.0, np.where(f < s, -1.0, 0.0)), index=df.index)


def trend_filter(df, n=200):
    """Long when close > SMA(n) (Faber 2007 tactical asset allocation)."""
    return (df.Close > sma(df.Close, n)).astype(float)


def tsmom(df, lookback=252, mode="long_flat"):
    """Time-series momentum (Moskowitz/Ooi/Pedersen 2012): sign of the return over
    `lookback` days, skipping the most recent 21."""
    c = df.Close
    sig = np.sign(c.shift(21) / c.shift(21 + lookback) - 1).fillna(0.0)
    return sig.clip(lower=0.0) if mode == "long_flat" else sig


def donchian(df, n=55, m=20):
    """Turtle breakout (Dennis/Eckhardt): enter close > prior N-day high, exit close < prior M-day low."""
    hh = df.High.rolling(n).max().shift(1)
    ll = df.Low.rolling(m).min().shift(1)
    return pd.Series(latch(df.Close > hh, df.Close < ll), index=df.index)


def macd(df, fast=12, slow=26, signal=9):
    """MACD (Appel): long when MACD line > its signal line."""
    line = ema(df.Close, fast) - ema(df.Close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return (line > sig).astype(float)


def vol_target_trend(df, target=0.15, n=200, vol_win=20):
    """Trend filter scaled by target_vol / realized 20d vol (managed-vol idea, Moreira/Muir 2017), cap 1."""
    rv = df.Close.pct_change().rolling(vol_win).std() * np.sqrt(ann_factor(df))
    scale = (target / rv).clip(upper=1.0)
    return ((df.Close > sma(df.Close, n)) * scale).fillna(0.0)


def find_boxes(high, low, hold):
    """Darvas boxes as (confirmed_idx, top, bottom). Mirrors alpaca-box-trader/darvas.py
    find_latest_box but returns every box. A box's confirmation uses only bars <= its
    confirmed_idx, so it is causal once indexed by confirmed_idx."""
    n = len(high)
    boxes = []
    i = 0
    while i < n:
        top, top_idx, held, j = high[i], i, 0, i + 1
        while j < n and held < hold:
            if high[j] > top:
                top, top_idx, held = high[j], j, 0
            else:
                held += 1
            j += 1
        if held < hold:
            break
        bot, bot_idx, held_b, k = low[top_idx], top_idx, 0, top_idx + 1
        while k < n and held_b < hold:
            if low[k] < bot:
                bot, bot_idx, held_b = low[k], k, 0
            else:
                held_b += 1
            k += 1
        if held_b < hold:
            break
        boxes.append((k - 1, top, bot))
        i = max(top_idx, bot_idx) + 1
    return boxes


def darvas_box(df, hold=3):
    """Darvas box breakout (Darvas 1960): enter close > confirmed box top, exit close < box
    bottom (trailing as newer boxes confirm)."""
    h, l, c = df.High.values, df.Low.values, df.Close.values
    n = len(c)
    top_at, bot_at = np.full(n, np.nan), np.full(n, np.nan)
    for ci, top, bot in find_boxes(h, l, hold):
        top_at[ci], bot_at[ci] = top, bot
    top_at = pd.Series(top_at).ffill().values
    bot_at = pd.Series(bot_at).ffill().values
    top_prev = np.r_[np.nan, top_at[:-1]]  # box must be confirmed before the breakout bar
    return pd.Series(latch(c > top_prev, c < bot_at), index=df.index)
