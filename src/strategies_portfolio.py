"""Batch 6 (Quantpedia): rotation / cross-sectional strategies over a universe of assets.
fn(P, **params) -> weight DataFrame decided on closes (bar t uses data <= t only).
Rules follow the Quantpedia 'simple trading strategy' text for each entry, adapted to the free
daily ETF / FX data available; adaptations are noted in each docstring."""
from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio import Panel, hold_rows, month_end_mask


def _me(P: Panel):
    """Month-end bar positions and the month-end close panel (NaN before an asset starts)."""
    pos = np.flatnonzero(month_end_mask(P.index))
    mc = P.C.iloc[pos].where(P.started.iloc[pos])
    return pos, mc


def _rank_weights(score: pd.Series, k_long: int, k_short: int = 0, gross_long=1.0, gross_short=1.0):
    s = score.dropna()
    w = pd.Series(0.0, index=score.index)
    if len(s) < max(1, k_long + k_short):
        return w
    order = s.sort_values(ascending=False)
    if k_long:
        w[order.index[:k_long]] = gross_long / k_long
    if k_short:
        w[order.index[-k_short:]] = -gross_short / k_short
    return w


def _monthly(P: Panel, score_fn, k_long, k_short=0, every=1):
    """At every `every`-th month-end, rank by score_fn(month_index) and hold until the next one."""
    pos, mc = _me(P)
    rows = {}
    for j, p in enumerate(pos):
        if j % every:
            continue
        sc = score_fn(j, mc)
        if sc is None:
            continue
        rows[p] = _rank_weights(sc, k_long, k_short)
    if not rows:
        return pd.DataFrame(0.0, index=P.index, columns=P.tickers)
    return hold_rows(P.index, rows)


def _mom(months):
    def f(j, mc):
        if j < months:
            return None
        return mc.iloc[j] / mc.iloc[j - months] - 1
    return f


def asset_class_momentum(P, months=12, top=3):
    """Quantpedia 'Asset Class Momentum Rotational System': hold the `top` ETFs with the best
    `months` return, equal weight, rebalance monthly."""
    return _monthly(P, _mom(months), top)


def asset_class_trend(P, months=10):
    """Quantpedia 'Asset Class Trend-Following': each asset gets 1/N when its month-end close is
    above its `months`-month SMA, else that slice sits in cash. N = assets started so far."""
    pos, mc = _me(P)
    sma = mc.rolling(months).mean()
    rows = {}
    for j, p in enumerate(pos):
        live = mc.iloc[j].notna()
        n = int(live.sum())
        if n == 0:
            continue
        on = (mc.iloc[j] > sma.iloc[j]) & live
        rows[p] = on.astype(float) / n
    return hold_rows(P.index, rows)


def sector_momentum(P, months=12, top=3):
    """Quantpedia 'Sector Momentum Rotational System': best `top` sectors by `months` return."""
    return _monthly(P, _mom(months), top)


def country_momentum(P, months=12, top=5):
    """Quantpedia 'Momentum Factor Effect in Country Equity Indexes': top `top` country ETFs by
    `months` return, monthly."""
    return _monthly(P, _mom(months), top)


def country_reversal(P, months=36, every=36, mode="ls"):
    """Quantpedia 'Mean Reversion Effect in Country Equity Indexes': long the 4 worst countries
    by `months` return, short the 4 best ('ls') or long-only ('long'); rebalance every `every` months."""
    f = _mom(months)
    return _monthly(P, lambda j, mc: None if f(j, mc) is None else -f(j, mc), 4, 4 if mode == "ls" else 0, every)


def country_bab(P, window=252, mode="ls"):
    """Quantpedia 'Betting Against Beta in Country Equity Indexes' (simplified): monthly, rank
    countries by trailing daily beta to SPY; long the low-beta half and short the high-beta half,
    each leg scaled to beta 1 (net beta 0); 'long' = low-beta half only, scaled to beta 1.
    The market is the universe's first ticker."""
    market = P.tickers[0]  # universe convention: market index first
    r = np.log(P.C).diff()
    pos = np.flatnonzero(month_end_mask(P.index))
    rows = {}
    cols = [c for c in P.tickers if c != market]
    for p in pos:
        if p < window:
            continue
        win = r.iloc[p - window + 1 : p + 1]
        ok = [c for c in cols if P.started.iloc[p - window + 1][c]]
        if len(ok) < 6:
            continue
        mv = win[market].var()
        beta = pd.Series({c: win[c].cov(win[market]) / mv for c in ok})
        rk = beta.rank()
        z = rk - rk.mean()
        lo, hi = -z.clip(upper=0), z.clip(lower=0)
        lo, hi = lo / lo.sum(), hi / hi.sum()
        w = pd.Series(0.0, index=P.tickers)
        bl, bh = float((lo * beta).sum()), float((hi * beta).sum())
        w[lo.index] += lo / bl
        if mode == "ls":
            w[hi.index] -= hi / bh
        rows[p] = w / max(1.0, float(w.abs().sum()) / 2)  # cap gross at 2
    if not rows:
        return pd.DataFrame(0.0, index=P.index, columns=P.tickers)
    return hold_rows(P.index, rows)


def paired_switching(P, months=3, hold=3):
    """Quantpedia 'Paired Switching': hold whichever of the two funds (stocks, bonds) had the
    higher return over the last `months` months; re-decide every `hold` months."""
    return _monthly(P, _mom(months), 1, 0, hold)


def style_rotation(P, months=12, mode="ls"):
    """Quantpedia 'Momentum Factor and Style Rotation': long the best of six Russell style ETFs by
    `months` return and short the worst ('ls'), or long the best only ('long'); monthly."""
    return _monthly(P, _mom(months), 1, 1 if mode == "ls" else 0)


def _k(P, frac):
    return lambda n: max(1, int(round(n * frac)))


def commodity_momentum(P, months=12, mode="ls"):
    """Quantpedia 'Momentum Effect in Commodities' on commodity ETFs: long the top quintile by
    `months` return, short the bottom quintile ('ls') or long-only; monthly."""
    pos, mc = _me(P)
    rows = {}
    for j, p in enumerate(pos):
        if j < months:
            continue
        sc = (mc.iloc[j] / mc.iloc[j - months] - 1).dropna()
        if len(sc) < 5:
            continue
        k = max(1, len(sc) // 5)
        rows[p] = _rank_weights(sc.reindex(P.tickers), k, k if mode == "ls" else 0)
    return hold_rows(P.index, rows) if rows else pd.DataFrame(0.0, index=P.index, columns=P.tickers)


def _daily_stat(P, months, fn, min_assets=5):
    """Monthly scores from trailing `months` x 21 daily log returns."""
    r = np.log(P.C).diff()
    pos = np.flatnonzero(month_end_mask(P.index))
    n = months * 21
    out = {}
    for p in pos:
        if p < n:
            continue
        win = r.iloc[p - n + 1 : p + 1]
        ok = [c for c in P.tickers if P.started.iloc[p - n + 1][c]]
        if len(ok) < min_assets:
            continue
        out[p] = pd.Series({c: fn(win[c].dropna()) for c in ok}).reindex(P.tickers)
    return out


def commodity_skewness(P, months=12, mode="ls"):
    """Quantpedia 'Skewness Effect in Commodities': long the 3 most negatively skewed, short the
    3 most positively skewed (or long-only); monthly."""
    sc = _daily_stat(P, months, lambda x: x.skew())
    rows = {p: _rank_weights(-s, 3, 3 if mode == "ls" else 0) for p, s in sc.items()}
    return hold_rows(P.index, rows) if rows else pd.DataFrame(0.0, index=P.index, columns=P.tickers)


def commodity_asymmetry(P, mode="ls"):
    """Quantpedia 'Return Asymmetry Effect in Commodity Futures': IE = #days above mean+2sd minus
    #days below mean-2sd over 260 days; buy the lowest IE (7 of 22 -> ~1/3 of the universe), short
    the highest; monthly."""
    def ie(x):
        m, s = x.mean(), x.std()
        return float((x > m + 2 * s).sum() - (x < m - 2 * s).sum())
    sc = _daily_stat(P, 12, ie)
    rows = {}
    for p, s in sc.items():
        k = max(1, int(round(s.notna().sum() * 7 / 22)))
        rows[p] = _rank_weights(-s, k, k if mode == "ls" else 0)
    return hold_rows(P.index, rows) if rows else pd.DataFrame(0.0, index=P.index, columns=P.tickers)


def currency_momentum(P, months=12, k=3):
    """Quantpedia 'Currency Momentum Factor': long the k currencies with the best `months` spot
    return vs USD, short the k worst; monthly. Spot only (no carry data), a stated limitation."""
    return _monthly(P, _mom(months), k, k)


def industry_bubbles(P, window=120, t_thr=1.96):
    """Quantpedia 'Riding Industry Bubbles': monthly CAPM regression of each sector's monthly
    excess return on the market over `window` months; equal-weight every sector whose alpha
    t-stat exceeds t_thr; cash if none."""
    market = P.tickers[0]  # universe convention: market index first
    pos, mc = _me(P)
    ret = mc.pct_change()
    rows = {}
    secs = [c for c in P.tickers if c != market]
    for j, p in enumerate(pos):
        if j < window:
            rows[p] = pd.Series(0.0, index=P.tickers)
            continue
        win = ret.iloc[j - window + 1 : j + 1]
        x = win[market].values
        sel = []
        for c in secs:
            y = win[c].values
            ok = np.isfinite(x) & np.isfinite(y)
            if ok.sum() < window * 0.9:
                continue
            X = np.c_[np.ones(ok.sum()), x[ok]]
            beta, res, *_ = np.linalg.lstsq(X, y[ok], rcond=None)
            dof = ok.sum() - 2
            s2 = float(((y[ok] - X @ beta) ** 2).sum() / dof)
            se = np.sqrt(s2 * np.linalg.inv(X.T @ X)[0, 0])
            if se > 0 and beta[0] / se > t_thr:
                sel.append(c)
        w = pd.Series(0.0, index=P.tickers)
        if sel:
            w[sel] = 1 / len(sel)
        rows[p] = w
    return hold_rows(P.index, rows)


def low_vol_rotation(P, years=3, k=3):
    """Quantpedia 'Low Volatility Factor Effect' applied to sector ETFs (adaptation: sectors, not
    stocks): long the k sectors with the lowest weekly-return volatility over `years`; monthly."""
    wk = np.log(P.C).diff(5)
    pos = np.flatnonzero(month_end_mask(P.index))
    n = years * 252
    rows = {}
    for p in pos:
        if p < n:
            continue
        win = wk.iloc[p - n + 1 : p + 1 : 5]
        ok = [c for c in P.tickers if P.started.iloc[p - n + 1][c]]
        if len(ok) < k + 2:
            continue
        rows[p] = _rank_weights(-win[ok].std().reindex(P.tickers), k)
    return hold_rows(P.index, rows) if rows else pd.DataFrame(0.0, index=P.index, columns=P.tickers)


def month_cycle(P, k=3, mode="ls"):
    """Quantpedia '12-Month Cycle in Cross-Section' on ETFs: rank by the return in the same
    calendar month one year ago (month j-11 relative to the month starting now); long top k,
    short bottom k (or long-only); monthly."""
    def f(j, mc):
        if j < 12:
            return None
        return mc.iloc[j - 11] / mc.iloc[j - 12] - 1
    return _monthly(P, f, k, k if mode == "ls" else 0)


def cross_asset_tsmom(P, months=12, mode="ls"):
    """Quantpedia 'Time Series Momentum Effect' on a cross-asset ETF/fund universe: monthly, each
    asset long (short) if its `months` return is positive (negative), sized by 1/(60-day vol),
    scaled to gross exposure 1. 'long' drops the shorts."""
    pos, mc = _me(P)
    vol = np.log(P.C).diff().rolling(60).std()
    rows = {}
    for j, p in enumerate(pos):
        if j < months:
            continue
        sig = np.sign(mc.iloc[j] / mc.iloc[j - months] - 1)
        if mode == "long":
            sig = sig.clip(lower=0)
        raw = (sig / vol.iloc[p]).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        g = raw.abs().sum()
        rows[p] = raw / g if g > 0 else raw
    return hold_rows(P.index, rows) if rows else pd.DataFrame(0.0, index=P.index, columns=P.tickers)


def january_small(P, months=(1,)):
    """Quantpedia 'January Effect in Stocks': small caps in January, large caps the rest of the
    year. Universe convention: [large, small]. Calendar decision at each month's last session."""
    large, small = P.tickers[0], P.tickers[1]
    pos = np.flatnonzero(month_end_mask(P.index))
    rows = {}
    for p in pos:
        nxt = (P.index[p] + pd.offsets.MonthBegin(1)).month
        w = pd.Series(0.0, index=P.tickers)
        w[small if nxt in months else large] = 1.0
        rows[p] = w
    return hold_rows(P.index, rows)


def pairs_trading(P, formation=120, k=0.5, n_pairs=5, trade_days=20):
    """Quantpedia 'Pairs Trading with Country ETFs' (distance method): every `trade_days` bars,
    normalize the last `formation` closes to 1, pick the n_pairs with the smallest sum of squared
    differences; during the next `trade_days` bars open a pair when the normalized spread exceeds
    k formation standard deviations (long the laggard, short the leader, 1/n_pairs per leg) and
    close it when the spread crosses zero or the period ends."""
    C = P.C.values
    T, N = C.shape
    W = np.zeros((T, N))
    for s0 in range(formation, T, trade_days):
        f0 = s0 - formation
        ok = [i for i in range(N) if P.started.values[f0, i]]
        if len(ok) < 4:
            continue
        base = C[f0, ok]
        norm = C[f0 : s0 + 1, ok] / base
        best = []
        for a in range(len(ok)):
            d = ((norm[:, a : a + 1] - norm[:, a + 1 :]) ** 2).sum(axis=0)
            for b, v in enumerate(d):
                best.append((v, a, a + 1 + b))
        best.sort()
        pairs = best[:n_pairs]
        for _, a, b in pairs:
            ia, ib = ok[a], ok[b]
            sd = np.std(norm[:, a] - norm[:, b])
            if sd <= 0:
                continue
            state = 0
            for t in range(s0, min(s0 + trade_days, T)):
                spr = C[t, ia] / C[f0, ia] - C[t, ib] / C[f0, ib]
                if state == 0 and abs(spr) > k * sd:
                    state = -1 if spr > 0 else 1  # spr>0: a rich -> short a, long b
                elif state != 0 and (np.sign(spr) == state or spr == 0):
                    state = 0
                if t == s0 + trade_days - 1:
                    state = 0  # period ends: flat at the period's last decision (calendar, not data length)
                W[t, ia] += state / n_pairs
                W[t, ib] -= state / n_pairs
    return pd.DataFrame(W, index=P.index, columns=P.tickers)
