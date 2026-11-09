import numpy as np
import pandas as pd
import pytest

import news12 as N


def prices(n=120, seed=1, shock_at=None, mult=1.0):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    if shock_at is not None:
        c[shock_at:] *= mult
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.002, n))
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame({"Open": o, "Close": c}, index=idx)


def test_vol_excludes_day_t_and_uses_only_past():
    df = prices()
    ret, vol, sign = N.shock_arrays(df.Close.values, 2)
    # vol[t] = std (ddof=1) of the 60 returns ending day t-1
    t = 100
    assert vol[t] == pytest.approx(np.std(ret[t - 60:t], ddof=1))
    # changing day t's close (and everything after) leaves vol[t] and every earlier vol / sign unchanged
    c2 = df.Close.values.copy()
    c2[t:] *= 1.3
    ret2, vol2, sign2 = N.shock_arrays(c2, 2)
    assert np.allclose(vol[:t + 1], vol2[:t + 1], equal_nan=True)
    assert (sign[:t] == sign2[:t]).all()
    assert sign2[t] == 1 and abs(ret2[t]) >= 2 * vol2[t]


def test_shock_threshold_and_sign():
    c = np.r_[100.0 * np.ones(70), 100.0]
    c = c * (1 + np.r_[np.zeros(70), 0.0])
    rng = np.random.default_rng(0)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.01, 80))
    c[70] = c[69] * 0.85
    _, vol, s2 = N.shock_arrays(c, 2)
    _, _, s3 = N.shock_arrays(c, 3)
    assert s2[70] == -1 and s3[70] == -1
    assert np.all(s2[:61] == 0)          # not enough history: never a shock


def test_news_window_boundaries():
    # Fri 2020-01-03, Mon 2020-01-06, Tue 2020-01-07 (US/Eastern, no DST edge)
    dates = pd.DatetimeIndex(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])
    et = "US/Eastern"
    ts = pd.DatetimeIndex([
        "2020-01-03 16:00:00",     # exactly Fri close -> Fri (inclusive end)
        "2020-01-03 16:00:01",     # just after Fri close -> belongs to Monday
        "2020-01-04 10:00:00",     # Saturday -> Monday
        "2020-01-06 16:00:00",     # exactly Mon close -> Monday
        "2020-01-06 16:00:01",     # -> Tuesday
        "2020-01-02 09:00:00",     # Thu morning -> Thu
    ]).tz_localize(et)
    n, e = N.news_counts(dates, ts, ["x"] * 6)
    assert list(n) == [1, 1, 3, 1]
    assert list(e) == [0, 0, 0, 0]
    # after the last close: dropped, not wrapped
    n2, _ = N.news_counts(dates, pd.DatetimeIndex(["2020-01-08 10:00:00"]).tz_localize(et), ["x"])
    assert n2.sum() == 0


def test_news_window_utc_input_and_summer():
    dates = pd.DatetimeIndex(["2020-07-01", "2020-07-02"])     # EDT: 16:00 ET = 20:00 UTC
    ts = pd.DatetimeIndex(["2020-07-01 20:00:00", "2020-07-01 20:00:01"]).tz_localize("UTC")
    n, _ = N.news_counts(dates, ts)
    assert list(n) == [1, 1]


def test_earnings_flag():
    dates = pd.DatetimeIndex(["2020-01-02", "2020-01-03"])
    ts = pd.DatetimeIndex(["2020-01-02 10:00", "2020-01-03 10:00", "2020-01-03 11:00"]).tz_localize("US/Eastern")
    n, e = N.news_counts(dates, ts, ["Nvidia Q3 earnings beat", "Analyst raises price target", "Stock rises"])
    assert list(n) == [1, 2] and list(e) == [1, 0]


def stock_with(sig_days, opens, closes=None):
    df = pd.DataFrame({"Open": opens, "Close": opens if closes is None else closes},
                      index=pd.bdate_range("2022-01-03", periods=len(opens)))
    st = N.Stock("T", px=df, news=pd.DataFrame(columns=["id", "created_at", "headline", "summary", "symbols", "source", "tags", "channels"]))
    sig = np.zeros(len(opens), int)
    for d, s in sig_days.items():
        sig[d] = s
    return st, sig


def test_fill_at_next_open():
    o = np.arange(100.0, 130.0)
    st, sig = stock_with({5: 1}, o)
    tr = N.take_trades(st, sig, N.TYPES["c"], 3, N.TEST)
    assert list(tr["ent"]) == [6] and list(tr["ex"]) == [9]            # signal day 5 -> enter open of day 6, exit open day 9
    assert N.net_returns(st, tr, 0.0)[0] == pytest.approx(o[9] / o[6] - 1)


def test_short_pnl_and_borrow():
    o = np.r_[np.full(10, 100.0), np.full(20, 90.0)]
    st, sig = stock_with({5: -1}, o)
    tr = N.take_trades(st, sig, N.TYPES["a"], 5, N.TEST)                # short after bad news
    assert list(tr["dir"]) == [-1]
    # entry day 6 (open 100), exit day 11 (open 90)... place a drop between: o[6..9]=100, o[10:]=90
    g = o[tr["ex"][0]] / o[tr["ent"][0]] - 1
    exp = -g - 2 * 0.0005 - 0.005 * 5 / 252
    assert N.net_returns(st, tr, 0.0005)[0] == pytest.approx(exp)
    long_tr = N.take_trades(st, sig, N.TYPES["b"], 5, N.TEST)           # reversal buys: no borrow, mirror gross
    assert N.net_returns(st, long_tr, 0.0)[0] == pytest.approx(g)


def test_no_overlap_and_window_and_end_of_data():
    o = np.linspace(100, 130, 30)
    st, sig = stock_with({3: 1, 5: 1, 8: 1, 27: 1}, o)
    tr = N.take_trades(st, sig, N.TYPES["c"], 3, N.TEST)
    # day3: ent 4 exit 7; day5 ent 6 < 7 skipped; day8: ent 9 ok exit 12; day27: exit 31 beyond data -> dropped
    assert list(tr["t"]) == [3, 8]
    # entry exactly on the previous exit day is allowed
    st2, sig2 = stock_with({3: 1, 6: 1}, o)
    tr2 = N.take_trades(st2, sig2, N.TYPES["c"], 3, N.TEST)
    assert list(tr2["t"]) == [3, 6]


def test_daily_series_matches_trade_return_h1():
    o = np.array([100, 101, 103, 102, 105, 104, 106, 108, 107, 109.0] * 3)
    st, sig = stock_with({2: -1}, o)
    g = N.Group([st], N.TEST)
    res = g.run("a", "all", 2, 1)  # sig comes from news; inject directly instead
    st._sig[(2, "all")] = sig
    res = g.run("a", "all", 2, 1)
    assert len(res["trades"][0]["t"]) == 1
    day = list(g.cal).index(st.dates[3])                                 # entry day 3 (open) -> exit open day 4
    exp = -(o[4] / o[3] - 1) - 0.005 / 252 - 2 * 0.0005
    assert res["r1"][day] == pytest.approx(exp)
    assert np.count_nonzero(res["r1"]) == 1
