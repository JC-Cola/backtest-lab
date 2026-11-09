import numpy as np
import pandas as pd
import pytest

import news13 as N


def ts(s):
    return pd.Timestamp(s, tz="UTC")


def test_news_window_et_incl_dst():
    # 2026-03-08 is the US DST start. Mon 2026-03-09: open 09:30 EDT = 13:30 UTC; Fri 03-06 close 16:00 EST = 21:00 UTC.
    dates = pd.DatetimeIndex(["2026-03-05", "2026-03-06", "2026-03-09", "2026-03-10"])
    arts = [ts("2026-03-06 20:59:59"),   # Fri 15:59:59 EST: before the Friday close -> Friday's window, not Monday's
            ts("2026-03-06 21:00:00"),   # exactly 16:00 EST: NOT in the window that starts after it (exclusive)
            ts("2026-03-06 21:00:01"),   # after the close -> Monday window
            ts("2026-03-09 13:29:59"),   # 09:29:59 EDT -> Monday window
            ts("2026-03-09 13:30:00"),   # exactly 09:30 EDT: not strictly before the open -> excluded
            ts("2026-03-09 14:29:59")]   # would be 09:29:59 under EST, but DST makes it 10:29:59 EDT -> excluded
    n = N.news_counts_open(dates, pd.DatetimeIndex(arts))
    # Monday window: the two before the open. Monday-session articles (13:30 and 14:29:59 UTC = 09:30 / 10:29 EDT) belong to no window.
    assert list(n) == [0, 0, 2, 0]


def test_news_window_standard_time_and_fall_back():
    # 2026-11-01 DST ends. Mon 11-02 open 09:30 EST = 14:30 UTC; Fri 10-30 close 16:00 EDT = 20:00 UTC.
    dates = pd.DatetimeIndex(["2026-10-30", "2026-11-02"])
    n = N.news_counts_open(dates, pd.DatetimeIndex([ts("2026-10-30 20:00:01"), ts("2026-11-02 14:29:59"), ts("2026-11-02 14:30:00"),
                                                    ts("2026-10-30 19:59:59")]))
    assert n[1] == 2


def test_vol_uses_only_past_and_gap_uses_prev_close():
    rng = np.random.default_rng(3)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.01, 120))
    v = N.vol_series(c)
    t = 100
    assert v[t] == pytest.approx(np.std(c[t - 60:t] / c[t - 61:t - 1] - 1, ddof=1))
    c2 = c.copy(); c2[t:] *= 1.5                      # day t's close and later do not change vol_t
    assert N.vol_series(c2)[t] == pytest.approx(v[t])
    assert np.isnan(v[:60]).all()
    gd = N.gap_down(np.array([97.0, 99.0, 97.9]), np.array([100.0, 100.0, 100.0]), np.array([0.02, 0.02, 0.01]), 1)
    assert list(gd) == [True, False, True]            # -3% vs -2% sigma; -1% vs -2% sigma; -2.1% vs -1%
    assert not N.gap_down(np.array([90.0]), np.array([100.0]), np.array([np.nan]), 1)[0]


def bars(o, c):
    """(1, NB) arrays from short lists, rest repeats the last price."""
    O = np.full((1, N.NB), np.nan); C = np.full((1, N.NB), np.nan)
    O[0, :len(o)], C[0, :len(c)] = o, c
    return O, C


def test_entry_at_first_bar_open_and_exit_times():
    o = [100 + i for i in range(45)]
    c = [100.5 + i for i in range(45)]
    O, C = bars(o, c)
    # entry 09:30 = open of bar 0 (100); m=1 exits at close of bar 0 (100.5), m=5 at close of bar 4 (104.5)
    assert N.gross_returns(O, C, 0, 1, 1)[0] == pytest.approx(100.5 / 100 - 1)
    assert N.gross_returns(O, C, 0, 5, 1)[0] == pytest.approx(104.5 / 100 - 1)
    # entry 09:31 = open of bar 1 (101); m=2 exits at close of bar 2 (102.5)
    assert N.gross_returns(O, C, 1, 2, 1)[0] == pytest.approx(102.5 / 101 - 1)
    assert N.gross_returns(O, C, 0, 30, 1)[0] == pytest.approx(129.5 / 100 - 1)


def test_green_bar_exit_and_cap_and_missing():
    o = [100, 99, 98, 97] + [96] * 41
    c = [99.5, 98.5, 97.9, 97.6] + [95.5] * 41        # bars 0-2 red, bar 3 green (close 97.6 > open 97)
    O, C = bars(o, c)
    assert N.gross_returns(O, C, 0, "G", -1)[0] == pytest.approx(97.6 / 100 - 1)       # short covers at the first green close
    assert N.gross_returns(O, C, 0, "G", 1)[0] == pytest.approx(99.5 / 100 - 1)        # long sells at the first red bar (bar 0)
    o2 = [100 - i for i in range(45)]; c2 = [99.5 - i for i in range(45)]               # never green: capped at bar 14 close
    O2, C2 = bars(o2, c2)
    assert N.gross_returns(O2, C2, 0, "G", -1)[0] == pytest.approx(C2[0, 14] / 100 - 1)
    assert N.gross_returns(O2, C2, 1, "G", -1)[0] == pytest.approx(C2[0, 15] / O2[0, 1] - 1)   # 09:31 entry: cap is bar e+14
    Om, Cm = bars(o, c); Om[0, 0] = np.nan
    assert np.isnan(N.gross_returns(Om, Cm, 0, 5, -1)[0])                               # missing entry bar -> skip
    Cx = C.copy(); Cx[0, 4] = np.nan
    assert np.isnan(N.gross_returns(O, Cx, 0, 5, -1)[0])                                # missing exit bar -> skip


def test_short_and_long_pnl_with_costs():
    # price falls 1%: short earns +1% - 10bp round trip; long loses 1% + 10bp
    assert N.net_return(-1, -0.01, 0.0005) == pytest.approx(0.01 - 0.001)
    assert N.net_return(1, -0.01, 0.0005) == pytest.approx(-0.01 - 0.001)
    assert N.net_return(-1, 0.01, 0.002) == pytest.approx(-0.01 - 0.004)


def test_stock_events_and_period_use_open_and_news():
    rng = np.random.default_rng(5)
    n = 90
    dates = pd.bdate_range("2021-01-04", periods=n)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    day = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=dates)
    # minute bars: open of bar 0 = prev close, except day 80 gaps down 5%
    idx, o, c = [], [], []
    for i, d in enumerate(dates):
        base = close[i - 1] if i else close[0]
        if i == 80:
            base *= 0.95
        for j in range(45):
            idx.append((d + pd.Timedelta(hours=9, minutes=30 + j)).tz_localize("America/New_York"))
            o.append(base * (1 - 0.0001 * j)); c.append(base * (1 - 0.0001 * (j + 1)))
    m = pd.DataFrame({"open": o, "high": o, "low": c, "close": c, "volume": 1.0}, index=pd.DatetimeIndex(idx))
    art = pd.DataFrame({"created_at": pd.to_datetime([dates[80] - pd.Timedelta(hours=3)]).tz_localize("America/New_York").tz_convert("UTC")})
    s12 = type("S", (), {"dates": dates, "sig": lambda self, k, sc: np.zeros(n, int)})()
    st = N.Stock13("X", m, day, art, s12)
    e = st.e1(1, True)
    assert e[80] and e.sum() == 1                                 # only the gap day with news (06:30 ET, before the open)
    assert st.e1(1, False).sum() == 0 or not st.e1(1, False)[80]
    assert not st.e1(2, True)[79]
    assert st.O[80, 0] == pytest.approx(close[79] * 0.95)         # entry price = open of the first bar
    idx_, sk = N.pick(st, e, ("2021-01-01", "2021-12-31"), 0, 5, -1)
    assert list(idx_) == [80] and sk == 0
    assert len(N.pick(st, e, ("2022-01-01", "2100-01-01"), 0, 5, -1)[0]) == 0     # outside the period
    # short covers 5 minutes later, price drifts down 5 x 0.01% -> gross -5bp... exit/entry-1 = 5 bars of -1bp
    g = st.g(0, 5, -1)[80]
    assert g == pytest.approx(st.C[80, 4] / st.O[80, 0] - 1) and g < 0
