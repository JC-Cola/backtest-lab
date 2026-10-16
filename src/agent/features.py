"""The data platform's feature table. Every column at row t uses only information available at
the CLOSE of day t (prices <= t, context series forward-filled only, published calendars).
Decisions are made on that close and traded at the next open by the engine."""
from __future__ import annotations

import numpy as np
import pandas as pd

import calendar_util as cu
from indicators import rsi, sma

PRICE = ["ret_1", "ret_5", "ret_20", "ret_60", "ret_250", "rsi_2", "rsi_14", "ibs", "wr_2", "wr_10",
         "z_20", "dist_sma_50", "dist_sma_200", "rv_20_pct", "down_streak", "range_rank_7", "gap"]
CONTEXT = ["vix", "vix_ratio", "vix_vs_50", "credit_trend", "curve"]
CALENDAR = ["dom_next", "to_month_end", "dow_next", "pre_holiday", "to_fomc"]
FEATURES = PRICE + CONTEXT + CALENDAR


def _sessions(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Trading sessions by calendar rule from before the data starts to well after it ends."""
    lo, hi = idx[0] - pd.Timedelta(days=10), idx[-1] + pd.Timedelta(days=150)
    if cu.is_crypto(idx):
        return pd.date_range(lo, hi, freq="D")
    b = pd.bdate_range(lo, hi)
    return b[~b.isin(cu.nyse_holidays())]


def build(df: pd.DataFrame, ctx: pd.DataFrame | None) -> pd.DataFrame:
    c, h, l, o = df.Close, df.High, df.Low, df.Open
    f = pd.DataFrame(index=df.index)
    for n in (1, 5, 20, 60, 250):
        f[f"ret_{n}"] = c / c.shift(n) - 1
    f["rsi_2"], f["rsi_14"] = rsi(c, 2), rsi(c, 14)
    f["ibs"] = ((c - l) / (h - l).replace(0, np.nan))
    for n in (2, 10):
        hh, ll = h.rolling(n).max(), l.rolling(n).min()
        f[f"wr_{n}"] = -100 * (hh - c) / (hh - ll).replace(0, np.nan)
    f["z_20"] = (c - sma(c, 20)) / c.rolling(20).std()
    f["dist_sma_50"], f["dist_sma_200"] = c / sma(c, 50) - 1, c / sma(c, 200) - 1
    rv = np.log(c).diff().rolling(20).std()
    f["rv_20_pct"] = rv.rolling(252, min_periods=200).rank(pct=True)
    dn = c < c.shift(1)
    f["down_streak"] = dn.astype(int).groupby((~dn).cumsum()).cumsum().astype(float)
    rng = h - l
    f["range_rank_7"] = rng.rolling(7).rank(pct=True)
    f["gap"] = o / c.shift(1) - 1
    if ctx is not None:
        f["vix"] = ctx["^VIX"]
        f["vix_ratio"] = ctx["^VIX"] / ctx["^VIX3M"]
        f["vix_vs_50"] = ctx["^VIX"] / sma(ctx["^VIX"], 50)
        cr = ctx["HYG"] / ctx["IEF"]
        f["credit_trend"] = cr / sma(cr, 50) - 1
        f["curve"] = ctx["^TNX"] - ctx["^IRX"]
    else:
        for k in CONTEXT:
            f[k] = np.nan
    # Calendar facts about the NEXT session (the one a decision at this close trades into).
    # Built from the published calendar only (NYSE holiday rules, or every day for crypto), never
    # from which data rows exist, so they cannot change when later rows arrive.
    cal = _sessions(df.index)
    nxt_i = cal.searchsorted(df.index, "right")
    nxt = cal[nxt_i]
    f["dom_next"] = np.asarray(nxt.day, float)
    f["dow_next"] = np.asarray(nxt.dayofweek, float)
    mkey = cal.year * 100 + cal.month
    last_in_month = pd.Series(np.arange(len(cal))).groupby(mkey).transform("max").values
    f["to_month_end"] = (last_in_month[nxt_i] - nxt_i).astype(float)
    after = cal[np.minimum(nxt_i + 1, len(cal) - 1)]
    hol = cu.nyse_holidays()
    f["pre_holiday"] = np.array([float(((hol > a) & (hol < b)).any()) for a, b in zip(nxt, after)])
    fomc_pos = cal.searchsorted(cu.fomc_dates(), "left")
    fomc_pos = fomc_pos[fomc_pos < len(cal)]
    j = np.searchsorted(fomc_pos, nxt_i, "left")
    f["to_fomc"] = np.where(j < len(fomc_pos), fomc_pos[np.minimum(j, len(fomc_pos) - 1)] - nxt_i, np.nan).astype(float)
    return f[FEATURES].replace([np.inf, -np.inf], np.nan)
