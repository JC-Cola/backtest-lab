"""Batch 3: popular TradingView indicators, long/flat. Every indicator uses trailing windows or
recursions over bars <= t only (Ichimoku's cloud is shifted FORWARD, i.e. uses older values)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import ema, latch, sma


def _s(x, idx):
    return pd.Series(np.asarray(x, float), index=idx)


def atr(df, n):
    pc = df.Close.shift(1)
    tr = pd.concat([df.High - df.Low, (df.High - pc).abs(), (df.Low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def wma(x, n):
    w = np.arange(1, n + 1, dtype=float)
    return x.rolling(n).apply(lambda a: np.dot(a, w) / w.sum(), raw=True)


def supertrend(df, n=10, mult=3.0):
    """Supertrend (Olivier Seban): ATR bands around (H+L)/2 that ratchet; long while in uptrend."""
    hl2 = ((df.High + df.Low) / 2).values
    a = atr(df, n).values
    c = df.Close.values
    up, dn = hl2 - mult * a, hl2 + mult * a
    out = np.zeros(len(c))
    trend, fu, fd = 0, np.nan, np.nan
    for t in range(len(c)):
        if np.isnan(a[t]):
            continue
        if np.isnan(fu):
            fu, fd, trend = up[t], dn[t], 1 if c[t] > hl2[t] else -1
        else:
            fu = max(up[t], fu) if c[t - 1] > fu else up[t]
            fd = min(dn[t], fd) if c[t - 1] < fd else dn[t]
            if trend == 1 and c[t] < fu:
                trend = -1
            elif trend == -1 and c[t] > fd:
                trend = 1
        out[t] = 1.0 if trend == 1 else 0.0
    return _s(out, df.index)


def heikin_ashi(df, k=2):
    """Heikin-Ashi: enter after k consecutive green HA candles, exit on the first red one."""
    hc = ((df.Open + df.High + df.Low + df.Close) / 4).values
    ho = np.empty(len(hc))
    ho[0] = (df.Open.iloc[0] + df.Close.iloc[0]) / 2
    for t in range(1, len(hc)):
        ho[t] = (ho[t - 1] + hc[t - 1]) / 2
    green = pd.Series(hc > ho)
    run = green.astype(int).groupby((~green).cumsum()).cumsum().values
    return _s(latch(run >= k, ~green.values), df.index)


def ttm_squeeze(df, kc=1.5, exit_="neg"):
    """TTM Squeeze (John Carter): Bollinger(20,2) inside Keltner(20, kc*ATR) = squeeze. Enter when
    the squeeze fires (on yesterday, off today) with positive momentum; exit when momentum turns
    negative ('neg') or starts falling ('down')."""
    c = df.Close
    m, sd = sma(c, 20), c.rolling(20).std(ddof=0)
    a = atr(df, 20)
    on = ((m + 2 * sd) < (m + kc * a)) & ((m - 2 * sd) > (m - kc * a))
    base = ((df.High.rolling(20).max() + df.Low.rolling(20).min()) / 2 + m) / 2
    dev = c - base
    x = np.arange(20, dtype=float)
    xm = x - x.mean()
    slope = dev.rolling(20).apply(lambda a_: np.dot(xm, a_) / np.dot(xm, xm), raw=True)
    mom = dev.rolling(20).mean() + slope * (19 - x.mean())  # linreg value at the last bar
    fired = on.shift(1, fill_value=False) & ~on
    enter = fired & (mom > 0)
    ex = (mom < 0) if exit_ == "neg" else (mom < mom.shift(1))
    return _s(latch(enter.values, ex.fillna(False).values), df.index)


def ichimoku(df, t=9, k=26, s=52):
    """Ichimoku: long while close is above the cloud and tenkan > kijun."""
    mid = lambda n: (df.High.rolling(n).max() + df.Low.rolling(n).min()) / 2
    ten, kij = mid(t), mid(k)
    span_a = ((ten + kij) / 2).shift(k)
    span_b = mid(s).shift(k)
    top = pd.concat([span_a, span_b], axis=1).max(axis=1, skipna=False)
    return _s((df.Close > top) & (ten > kij), df.index)


def psar(df, step=0.02, mx=0.2):
    """Parabolic SAR (Wilder): long while the SAR is below price."""
    h, l = df.High.values, df.Low.values
    n = len(h)
    out = np.zeros(n)
    if n < 3:
        return _s(out, df.index)
    up, af = True, step
    ep, sar = h[0], l[0]
    for t in range(1, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[t - 1], l[t - 2] if t > 1 else l[t - 1])
            if l[t] < sar:
                up, sar, ep, af = False, ep, l[t], step
            elif h[t] > ep:
                ep, af = h[t], min(mx, af + step)
        else:
            sar = max(sar, h[t - 1], h[t - 2] if t > 1 else h[t - 1])
            if h[t] > sar:
                up, sar, ep, af = True, ep, h[t], step
            elif l[t] < ep:
                ep, af = l[t], min(mx, af + step)
        out[t] = 1.0 if up else 0.0
    return _s(out, df.index)


def adx_di(df, n=14, thr=20):
    """ADX/DMI (Wilder): long while +DI > -DI and ADX > thr."""
    upm, dnm = df.High.diff(), -df.Low.diff()
    pdm = pd.Series(np.where((upm > dnm) & (upm > 0), upm, 0.0), index=df.index)
    ndm = pd.Series(np.where((dnm > upm) & (dnm > 0), dnm, 0.0), index=df.index)
    a = atr(df, n)
    w = lambda x: x.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    pdi, ndi = 100 * w(pdm) / a, 100 * w(ndm) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi)
    adx = w(dx)
    return _s((pdi > ndi) & (adx > thr), df.index)


def stochastic(df, n=14, x=20):
    """Slow stochastic oversold: long when %K(n, 3) < x above SMA200; exit when %K > 80."""
    ll, hh = df.Low.rolling(n).min(), df.High.rolling(n).max()
    k = (100 * (df.Close - ll) / (hh - ll)).rolling(3).mean()
    return _s(latch(((k < x) & (df.Close > sma(df.Close, 200))).values, (k > 80).values), df.index)


def williams_r(df, n=2, trend=True):
    """Williams %R: long when %R(n) < -90 (optionally only above SMA200); exit when %R > -20."""
    hh, ll = df.High.rolling(n).max(), df.Low.rolling(n).min()
    wr = -100 * (hh - df.Close) / (hh - ll)
    ok = (df.Close > sma(df.Close, 200)) if trend else pd.Series(True, index=df.index)
    return _s(latch(((wr < -90) & ok).values, (wr > -20).values), df.index)


def cci(df, n=20, mode="revert"):
    """Commodity Channel Index (Lambert). 'revert': buy CCI < -100, exit CCI > 0.
    'trend': buy CCI > +100, exit CCI < 0."""
    tp = (df.High + df.Low + df.Close) / 3
    m = sma(tp, n)
    mad = tp.rolling(n).apply(lambda a: np.abs(a - a.mean()).mean(), raw=True)
    v = (tp - m) / (0.015 * mad)
    if mode == "revert":
        return _s(latch((v < -100).values, (v > 0).values), df.index)
    return _s(latch((v > 100).values, (v < 0).values), df.index)


def aroon(df, n=25):
    """Aroon (Chande): long while Aroon-Up > Aroon-Down and Aroon-Up >= 70."""
    since_hi = df.High.rolling(n + 1).apply(lambda a: n - np.argmax(a), raw=True)
    since_lo = df.Low.rolling(n + 1).apply(lambda a: n - np.argmin(a), raw=True)
    up, dn = 100 * (n - since_hi) / n, 100 * (n - since_lo) / n
    return _s((up > dn) & (up >= 70), df.index)


def hull(df, n=50, rule="slope"):
    """Hull moving average (Alan Hull). 'slope': long while HMA is rising; 'price': close > HMA."""
    c = df.Close
    h = wma(2 * wma(c, n // 2) - wma(c, n), int(np.sqrt(n)))
    sig = h > h.shift(1) if rule == "slope" else c > h
    return _s(sig, df.index)


def kama(df, n=10, rule="price"):
    """Kaufman adaptive MA (fast 2, slow 30). 'price': close > KAMA; 'slope': KAMA rising."""
    c = df.Close.values
    er_num = np.abs(df.Close - df.Close.shift(n)).values
    er_den = df.Close.diff().abs().rolling(n).sum().values
    fast, slow = 2 / 3, 2 / 31
    k = np.full(len(c), np.nan)
    for t in range(n, len(c)):
        er = er_num[t] / er_den[t] if er_den[t] > 0 else 0.0
        sc = (er * (fast - slow) + slow) ** 2
        k[t] = c[t] if np.isnan(k[t - 1]) else k[t - 1] + sc * (c[t] - k[t - 1])
    ks = pd.Series(k, index=df.index)
    sig = df.Close > ks if rule == "price" else ks > ks.shift(1)
    return _s(sig, df.index)


def triple_ma(df, f=10, m=50, s=200):
    """Triple moving average stack: long while SMA(f) > SMA(m) > SMA(s)."""
    c = df.Close
    a, b, d = sma(c, f), sma(c, m), sma(c, s)
    return _s((a > b) & (b > d), df.index)


def chandelier(df, n=50, k=3.0):
    """Breakout entry (close above prior n-day high) with a Chandelier exit (Le Beau):
    exit when close < 22-day highest high - k * ATR(22)."""
    c = df.Close
    enter = c > df.High.rolling(n).max().shift(1)
    stop = df.High.rolling(22).max() - k * atr(df, 22)
    return _s(latch(enter.values, (c < stop).values), df.index)
