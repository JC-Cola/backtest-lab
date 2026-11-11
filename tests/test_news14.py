import numpy as np
import pandas as pd
import pytest

import news14 as M


def day(o=None, h=None, l=None, c=None):
    """(1, 390) arrays; default a flat 100 session with the given bar overrides {j: value}."""
    arrs = []
    for d in (o, h, l, c):
        a = np.full((1, 390), 100.0)
        for j, v in (d or {}).items():
            a[0, j] = v
        arrs.append(a)
    return arrs


def test_entry_open_and_close_exits():
    O, H, L, C = day(o={0: 100, 1: 101}, c={385: 95, 389: 90})
    dc = np.array([92.0])
    assert M.gross_returns(O, H, L, C, dc, 0, "X55", -1)[0] == pytest.approx(95 / 100 - 1)      # exit = close of bar 385 (15:55)
    assert M.gross_returns(O, H, L, C, dc, 1, "X55", -1)[0] == pytest.approx(95 / 101 - 1)      # 09:31 entry = open of bar 1
    assert M.gross_returns(O, H, L, C, dc, 0, "XC", -1)[0] == pytest.approx(92 / 100 - 1)       # official close, not the 15:59 bar
    C2 = C.copy(); C2[0, 389] = 50
    assert M.gross_returns(O, H, L, C2, dc, 0, "X55", -1)[0] == pytest.approx(95 / 100 - 1)     # later bars never used


def test_bar_index_is_1555():
    t = pd.Timestamp("2026-11-05 15:55")
    assert (t.hour * 60 + t.minute) - 570 == M.LAST_BAR == 385


def test_missing_prices_skip():
    O, H, L, C = day(c={385: np.nan})
    dc = np.array([90.0])
    assert np.isnan(M.gross_returns(O, H, L, C, dc, 0, "X55", -1)[0])
    assert np.isnan(M.gross_returns(O, H, L, C, dc, 0, "S1", -1)[0])             # no stop and no 15:55 bar -> skipped
    assert M.gross_returns(O, H, L, C, dc, 0, "XC", -1)[0] == pytest.approx(-0.1)
    O2, H2, L2, C2 = day(o={0: np.nan})
    assert np.isnan(M.gross_returns(O2, H2, L2, C2, dc, 0, "X55", -1)[0])        # missing entry bar


def test_short_stop_hits_level_and_gap_through():
    # short from 100, 1% stop = 101. Bar 10 high 101.5 -> stopped at 101 (bar opened below the level)
    O, H, L, C = day(h={10: 101.5}, c={385: 90})
    assert M.gross_returns(O, H, L, C, np.array([90.0]), 0, "S1", -1)[0] == pytest.approx(101 / 100 - 1)
    # bar 20 opens at 103 (gap through the level): fill at the open, worse than the stop level
    O, H, L, C = day(o={20: 103}, h={20: 104}, c={385: 90})
    assert M.gross_returns(O, H, L, C, np.array([90.0]), 0, "S1", -1)[0] == pytest.approx(103 / 100 - 1)
    # 2% stop (102) not touched by a 101.5 high -> exits at 15:55 close
    O, H, L, C = day(h={10: 101.5}, c={385: 90})
    assert M.gross_returns(O, H, L, C, np.array([90.0]), 0, "S2", -1)[0] == pytest.approx(0.9 - 1)
    # the earliest touching bar wins even if a later bar is higher
    O, H, L, C = day(h={5: 101.2, 50: 110}, c={385: 90})
    assert M.gross_returns(O, H, L, C, np.array([90.0]), 0, "S1", -1)[0] == pytest.approx(0.01)


def test_stop_checked_from_entry_bar_not_before():
    # 09:31 entry (open of bar 1 = 100): a high at bar 0 is before entry and must not stop; bar 1's high counts
    O, H, L, C = day(h={0: 105, 1: 100.5}, c={385: 98})
    assert M.gross_returns(O, H, L, C, np.array([98.0]), 1, "S1", -1)[0] == pytest.approx(0.98 - 1)
    O, H, L, C = day(h={0: 105, 1: 101.2}, c={385: 98})
    assert M.gross_returns(O, H, L, C, np.array([98.0]), 1, "S1", -1)[0] == pytest.approx(0.01)
    # entry bar itself can stop (09:30 entry, high of bar 0 touches)
    O, H, L, C = day(h={0: 101.5}, c={385: 98})
    assert M.gross_returns(O, H, L, C, np.array([98.0]), 0, "S1", -1)[0] == pytest.approx(0.01)
    # a spike after 15:55 does not stop the trade (bar 386+)
    O, H, L, C = day(h={388: 120}, c={385: 98})
    assert M.gross_returns(O, H, L, C, np.array([98.0]), 0, "S1", -1)[0] == pytest.approx(0.98 - 1)


def test_long_stop_uses_low():
    O, H, L, C = day(l={30: 98.5}, h={30: 150}, c={385: 110})          # a high spike does not stop a long
    assert M.gross_returns(O, H, L, C, np.array([110.0]), 0, "S1", 1)[0] == pytest.approx(99 / 100 - 1)
    O, H, L, C = day(o={30: 97}, l={30: 96}, c={385: 110})              # gap down through 99: fill at the open 97
    assert M.gross_returns(O, H, L, C, np.array([110.0]), 0, "S2", 1)[0] == pytest.approx(97 / 100 - 1)
    assert M.gross_returns(O, H, L, C, np.array([110.0]), 0, "S1", 1)[0] == pytest.approx(97 / 100 - 1)


def test_short_pnl():
    # NVDA 2026-11-05: short 196.07 -> close 181.60
    g = 181.60 / 196.07 - 1
    assert M.net_return(-1, g, 0.0005) == pytest.approx(-g - 0.001)
    assert M.net_return(-1, g, 0.0005) * 100 == pytest.approx(7.38 - 0.1, abs=0.02)
    assert M.net_return(1, g, 0.0005) == pytest.approx(g - 0.001)
    assert M.net_return(-1, 0.02, 0.0005) == pytest.approx(-0.02 - 0.001)     # stopped short: loss plus costs


def test_adverse_excursion():
    O, H, L, C = day(h={7: 103}, l={9: 96})
    assert M.adverse_excursion(O, H, L, 0, -1)[0] == pytest.approx(0.03)
    assert M.adverse_excursion(O, H, L, 0, 1)[0] == pytest.approx(0.04)


def make_stock():
    rng = np.random.default_rng(5)
    n = 90
    dates = pd.bdate_range("2021-01-04", periods=n)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    dayf = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=dates)
    FO = np.zeros((n, 390)); FC = np.zeros((n, 390))
    idx, o, c = [], [], []
    for i, d in enumerate(dates):
        base = close[i - 1] if i else close[0]
        if i == 80:
            base *= 0.95
        j = np.arange(390)
        FO[i] = base * (1 - 0.0001 * j); FC[i] = base * (1 - 0.0001 * (j + 1))
        for jj in range(45):
            idx.append((d + pd.Timedelta(hours=9, minutes=30 + jj)).tz_localize("America/New_York"))
            o.append(FO[i, jj]); c.append(FC[i, jj])
    m = pd.DataFrame({"open": o, "high": o, "low": c, "close": c, "volume": 1.0}, index=pd.DatetimeIndex(idx))
    art = pd.DataFrame({"created_at": pd.to_datetime([dates[80] - pd.Timedelta(hours=3)]).tz_localize("America/New_York").tz_convert("UTC")})
    sig = np.zeros(n, int); sig[40] = -1
    s12 = type("S", (), {"dates": dates, "sig": lambda self, k, sc: sig})()
    st = M.Stock14("X", m, dayf, art, s12, full=(dates, FO, FO, FC, FC))
    return st, dates, close


def test_stock_events_no_lookahead_and_end_to_end():
    st, dates, close = make_stock()
    e = st.e1(1, True)
    assert e[80] and e.sum() == 1                              # gap-down + overnight news only on day 80
    assert st.e1(0, True)[80]                                  # E1b: any negative gap with news (strict)
    assert not st.e1(1, False)[80] and not st.e1(2, True)[79]
    assert st.e2(2)[41] and st.e2(2).sum() == 1                # shock on day 40 -> trade on day 41
    # vol/prev_close use data before the open of day 80: changing day 80's close alters nothing in the event or gap
    assert st.O[80, 0] == pytest.approx(close[79] * 0.95)
    g = st.g(0, "X55", -1)[80]
    assert g == pytest.approx(st.FC[80, 385] / st.FO[80, 0] - 1) and g < 0       # short from the open, exit 15:55
    assert st.g(0, "XC", -1)[80] == pytest.approx(close[80] / st.FO[80, 0] - 1)
    idx, sk = M.N.pick(st, e, ("2021-01-01", "2021-12-31"), 0, "X55", -1)
    assert list(idx) == [80] and sk == 0
    assert len(M.N.pick(st, e, ("2022-01-01", "2100-01-01"), 0, "X55", -1)[0]) == 0
    m, picks, r1 = M._evaluate_set([st], "E1", 1, -1, 0, "X55", ("2021-01-01", "2021-12-31"), False, "t")
    assert m["trades"] == 1 and m["mean_bp"] == pytest.approx(-g * 1e4 - 10)


def test_news_vs_nonews_masks_disjoint():
    st, *_ = make_stock()
    a, b = st.e1(0.5, True), st.e1(0.5, False)
    assert not (a & b).any() and (st.e1(0.5, None) == (a | b)).all()
