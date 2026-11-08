import numpy as np
import pandas as pd
import pytest

import intraday as I
import intraday11 as S
from test_intraday import synth


def D0():
    return I.Days.from_arrays(*synth())


def test_grid_is_14_and_excludes_plain():
    assert len(S.GRID) == 14 and S.PLAIN not in S.GRID


def test_realized_vol_uses_only_prior_days():
    D = D0()
    base = S.realized_vol(D)
    d0 = 40
    dates, O, H, L, C, V, last = synth()
    C2, O2, H2, L2 = (x.copy() for x in (C, O, H, L))
    for x in (C2, O2, H2, L2):
        x[d0:] *= 1.5
    D2 = I.Days.from_arrays(dates, O2, H2, L2, C2, V, last)
    new = S.realized_vol(D2)
    assert np.allclose(base[:d0 + 1], new[:d0 + 1], equal_nan=True)   # day d0 itself is not used by vol[d0]
    assert not np.isclose(base[d0 + 2], new[d0 + 2])


def test_realized_vol_value():
    D = D0()
    ret = D.close[1:] / D.close[:-1] - 1
    d = 30
    assert S.realized_vol(D)[d] == pytest.approx(ret[d - 15:d - 1].std(ddof=1))  # ret[k-1] is day k's return; days d-14..d-1


def test_size_target_and_cap():
    vol = np.array([0.005, 0.01, 0.02, 0.04, np.nan])
    v = m = np.ones(5)
    w = S.day_weights(vol, v, m, 0.01, 1.0, "none")
    assert w[:4] == pytest.approx([1.0, 1.0, 0.5, 0.25]) and w[4] == 0
    w = S.day_weights(vol, v, m, 0.01, 2.0, "none")
    assert w[:4] == pytest.approx([2.0, 1.0, 0.5, 0.25])
    assert S.day_weights(vol, v, m, None, None, "none") == pytest.approx(np.ones(5))


def test_filter_modes():
    vol = np.full(4, 0.01)
    v = np.array([20, 20, 20, np.nan]); m = np.array([15, 20, 25, 15])
    assert list(S.day_weights(vol, v, m, None, None, "high")) == [1, 0, 0, 0]
    assert list(S.day_weights(vol, v, m, None, None, "low")) == [0, 1, 1, 0]
    assert list(S.day_weights(vol, v, m, None, None, "none")) == [1, 1, 1, 1]


def test_vix_state_uses_only_earlier_dates():
    idx = pd.bdate_range("2020-01-01", periods=60)
    vix = pd.Series(np.arange(60, dtype=float) + 10, index=idx)
    dates = pd.DatetimeIndex([idx[30]])
    v, m = S.vix_state(dates, vix)
    assert v[0] == vix.iloc[29] and m[0] == pytest.approx(vix.iloc[10:30].mean())
    vix2 = vix.copy(); vix2.iloc[30:] = 999.0                       # same day and later changed: no effect
    v2, m2 = S.vix_state(dates, vix2)
    assert (v2[0], m2[0]) == (v[0], m[0])
    vix3 = vix.copy(); vix3.iloc[29] = 500.0                        # the previous day changed: effect
    assert S.vix_state(dates, vix3)[0][0] == 500.0
    assert np.isnan(S.vix_state(pd.DatetimeIndex([idx[5]]), vix)[0][0])


def test_variant_scales_trades_and_costs():
    D = D0()
    t = {"day": np.array([20, 21]), "entry_m": np.array([10, 10]), "exit_m": np.array([100, 100]), "dir": np.array([1.0, -1.0]),
         "ret": np.array([0.01, -0.004])}
    w = np.zeros(D.n); w[20], w[21] = 2.0, 0.5
    sl = slice(0, D.n)
    r1 = S.daily_w(D, t, w, sl, 1)
    assert r1[20] == pytest.approx(0.02) and r1[21] == pytest.approx(-0.002)
    r2 = S.daily_w(D, t, w, sl, 2)
    assert r2[20] == pytest.approx((0.01 - 2 * I.COST) * 2.0) and r2[21] == pytest.approx((-0.004 - 2 * I.COST) * 0.5)
    w[21] = 0
    assert S.daily_w(D, t, w, sl, 1)[21] == 0


def test_unit_weights_equal_plain_and_coin_matches_unweighted():
    D = D0()
    t = I.noise_breakout(D, lookback=14, check=60)
    sl = slice(0, D.n)
    assert np.allclose(S.daily_w(D, t, np.ones(D.n), sl), I.daily_returns(D, t, sl))
    assert np.allclose(S.coin_vals_w(D, t, np.ones(D.n), 5), I.coin_vals(D, t, 5))


def test_bootstrap_detects_and_rejects():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 0.01, 800)
    idx = S.block_index(800, np.random.default_rng(1), b=2000)
    assert S.paired_p(base + 0.002, base, idx)["p_mean"] < 0.01
    assert S.paired_p(base * 1.0, base, idx)["p_mean"] > 0.4
