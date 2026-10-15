"""Batch 1: calendar strategies. Calendar facts only (dates, NYSE holiday rules, published FOMC
schedule); no price data at all. All long/flat. See calendar_util for the session convention."""
from __future__ import annotations

import numpy as np
import pandas as pd

import calendar_util as cu


def _pos(df, hold_fn):
    ext = cu.extended_index(df.index)
    return pd.Series(cu.hold_to_position(hold_fn(ext), len(df)), index=df.index)


def pre_fomc(df, a=-1, b=0):
    """Pre-FOMC drift (Lucca & Moench 2015): hold sessions a..b relative to the scheduled FOMC
    statement day (0 = statement day, held open to next open)."""
    return _pos(df, lambda ext: cu.window_mask(len(ext), cu.event_positions(ext, cu.fomc_dates()), a, b))


def pre_holiday(df, k=1, after=0):
    """Pre-holiday effect (Ariel 1990): hold the last k sessions before an NYSE holiday, plus
    `after` sessions from the first session after it."""
    return _pos(df, lambda ext: cu.window_mask(len(ext), cu.post_holiday_sessions(ext), -k, after - 1))


def opex_week(df, which="opex"):
    """Monthly options-expiration week: hold Mon-Fri of the week containing the third Friday
    ('opex'), the week after it ('after'), or only the two sessions into opex Friday ('late')."""
    def hold(ext):
        tf = cu.third_fridays(ext[0] - pd.Timedelta(days=40), ext[-1] + pd.Timedelta(days=40))
        wk = ext.to_period("W-FRI")
        if which == "late":
            ev = cu.event_positions(ext, tf)
            return cu.window_mask(len(ext), ev, -1, 0)
        target = tf.to_period("W-FRI")
        if which == "after":
            target = target + 1
        return np.asarray(wk.isin(target))
    return _pos(df, hold)


def mid_month(df, d1=8, d2=15):
    """Mid-month window: hold sessions whose calendar day-of-month is in [d1, d2]."""
    return _pos(df, lambda ext: (ext.day >= d1) & (ext.day <= d2))


def halloween(df, start=11, end=4):
    """'Sell in May' / Halloween indicator (Bouman & Jacobsen 2002): hold from month `start`
    through month `end` (wrapping the year), flat otherwise."""
    return _pos(df, lambda ext: (ext.month >= start) | (ext.month <= end))


def santa_rally(df, n_dec=5, n_jan=2):
    """Santa Claus rally (Hirsch, Stock Trader's Almanac): last n_dec sessions of December plus
    the first n_jan sessions of January."""
    def hold(ext):
        s, e = cu.month_ranks(ext)
        return ((ext.month == 12) & (e < n_dec)) | ((ext.month == 1) & (s < n_jan))
    return _pos(df, hold)


def weekday(df, day=0, mode="only"):
    """Day-of-week effect: hold only sessions starting on weekday `day` ('only'), or every
    session except those ('skip'). 0 = Monday."""
    return _pos(df, lambda ext: (ext.dayofweek == day) if mode == "only" else (ext.dayofweek != day))


def quarter_end(df, n=5, side="end"):
    """Quarter-end window dressing / quarter-start inflows: hold the last n sessions of
    Mar/Jun/Sep/Dec ('end') or the first n sessions of Jan/Apr/Jul/Oct ('start')."""
    def hold(ext):
        s, e = cu.month_ranks(ext)
        if side == "end":
            return (ext.month % 3 == 0) & (e < n)
        return (ext.month % 3 == 1) & (s < n)
    return _pos(df, hold)


def calendar_composite(df, parts=("tom", "holiday", "fomc"), halloween_gate=False):
    """Union of fixed-parameter calendar effects: turn of month (last 1 + first 3 sessions),
    pre-holiday (1 session), pre-FOMC (day before + statement day). Optionally only Nov-Apr."""
    def hold(ext):
        n = len(ext)
        h = np.zeros(n, bool)
        s, e = cu.month_ranks(ext)
        if "tom" in parts:
            h |= (e < 1) | (s < 3)
        if "holiday" in parts:
            h |= cu.window_mask(n, cu.post_holiday_sessions(ext), -1, -1)
        if "fomc" in parts:
            h |= cu.window_mask(n, cu.event_positions(ext, cu.fomc_dates()), -1, 0)
        if halloween_gate:
            h &= (ext.month >= 11) | (ext.month <= 4)
        return h
    return _pos(df, hold)
