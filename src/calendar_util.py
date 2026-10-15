"""Trading-calendar helpers for calendar strategies (calendar facts only, never prices).

Timing convention (same as the engine): a position decided on the close of bar t is held from
the OPEN of session t+1 to the OPEN of session t+2. So "hold session j" means s[j-1] = 1.
`hold_to_position` does that shift. It works on an extended calendar (the data index plus future
sessions taken from the NYSE holiday rules, or every day for crypto), so the last bar's
position depends only on the calendar, never on whether a later price row exists.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.tseries.holiday import (AbstractHolidayCalendar, GoodFriday, Holiday, USLaborDay,
                                    USMartinLutherKingJr, USMemorialDay, USPresidentsDay,
                                    USThanksgivingDay, nearest_workday, sunday_to_monday)

FOMC_CSV = Path(__file__).resolve().parent.parent / "data" / "fomc_dates.csv"
EXTEND = 70  # future sessions appended (enough to finish the next month and quarter)


class NYSEHolidays(AbstractHolidayCalendar):
    """Regular scheduled NYSE full-day closures (rules published years in advance).
    Unscheduled closures (9/11, Hurricane Sandy, state funerals) are deliberately left out:
    they were not known ahead of time. The data index already reflects them."""
    rules = [
        Holiday("NewYear", month=1, day=1, observance=sunday_to_monday),
        Holiday("MLK", month=1, day=1, offset=USMartinLutherKingJr.offset, start_date="1998-01-01"),
        USPresidentsDay, GoodFriday, USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, observance=nearest_workday, start_date="2022-01-01"),
        Holiday("July4", month=7, day=4, observance=nearest_workday),
        USLaborDay, USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


@lru_cache(maxsize=4)
def nyse_holidays(start="1990-01-01", end="2030-12-31") -> pd.DatetimeIndex:
    return NYSEHolidays().holidays(start, end)


def is_crypto(idx: pd.DatetimeIndex) -> bool:
    return (idx.dayofweek >= 5).mean() > 0.1


def extended_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Data index + EXTEND future sessions from the calendar."""
    last = idx[-1]
    if is_crypto(idx):
        fut = pd.date_range(last + pd.Timedelta(days=1), periods=EXTEND, freq="D")
    else:
        b = pd.bdate_range(last + pd.Timedelta(days=1), periods=EXTEND * 2)
        fut = b[~b.isin(nyse_holidays())][:EXTEND]
    return idx.append(fut)


def hold_to_position(hold_ext, n: int) -> np.ndarray:
    """hold_ext[j] = want to be long during session j (open j -> open j+1) on the extended
    calendar. Returns s[t] = hold_ext[t+1] for the n data bars."""
    h = np.asarray(hold_ext, float)
    return h[1 : n + 1]


def month_ranks(ext: pd.DatetimeIndex):
    """0-based (rank from start, rank from end) of each session within its month."""
    key = pd.Series(ext.year * 100 + ext.month)
    return key.groupby(key).cumcount().values, key.groupby(key).cumcount(ascending=False).values


def event_positions(ext: pd.DatetimeIndex, dates) -> np.ndarray:
    """Position in ext of the first session on or after each event date (inside ext's range)."""
    d = pd.DatetimeIndex(pd.to_datetime(dates)).sort_values()
    d = d[(d >= ext[0]) & (d <= ext[-1])]
    return np.unique(ext.searchsorted(d, "left"))


def window_mask(n_ext: int, events: np.ndarray, a: int, b: int) -> np.ndarray:
    """Boolean mask of sessions e+a .. e+b (inclusive) around each event position e."""
    m = np.zeros(n_ext, bool)
    for e in events:
        lo, hi = max(0, e + a), min(n_ext - 1, e + b)
        if lo <= hi:
            m[lo : hi + 1] = True
    return m


@lru_cache(maxsize=1)
def fomc_dates() -> pd.DatetimeIndex:
    """Scheduled FOMC statement dates (published a year+ ahead) from data/fomc_dates.csv."""
    return pd.DatetimeIndex(pd.to_datetime(pd.read_csv(FOMC_CSV)["date"]))


def post_holiday_sessions(ext: pd.DatetimeIndex) -> np.ndarray:
    """Positions of the first session on or after each NYSE holiday."""
    return event_positions(ext, nyse_holidays())


def third_fridays(start, end) -> pd.DatetimeIndex:
    return pd.date_range(start, end, freq="WOM-3FRI")
