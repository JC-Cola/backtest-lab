import numpy as np
import pytest

import intraday as I
import intraday3 as S3
import intraday4 as S4
import intraday5 as S5
import intraday7 as S7
from test_intraday import perturbed, synth

ALL = S3.STRATEGIES + S4.STRATEGIES + S5.STRATEGIES + S7.STRATEGIES


@pytest.mark.parametrize("st", ALL, ids=[s["id"] for s in ALL])
def test_entries_do_not_peek(st):
    d0, K = 30, 200
    A = I.Days.from_arrays(*synth())
    B = I.Days.from_arrays(*perturbed(d0, K))
    for p in st["grid"]:
        ta, tb = st["fn"](A, **p), st["fn"](B, **p)
        def early(t):
            keep = (t["day"] < d0) | ((t["day"] == d0) & (t["entry_m"] <= K))
            return sorted(zip(t["day"][keep], t["entry_m"][keep], t["dir"][keep]))
        assert early(ta) == early(tb), f"{st['id']} {p} decides with future prices"


@pytest.mark.parametrize("st", ALL, ids=[s["id"] for s in ALL])
def test_trades_are_intraday_and_priced(st):
    A = I.Days.from_arrays(*synth())
    for p in st["grid"]:
        t = st["fn"](A, **p)
        if st["id"] == "id_dts_orb_setup":  # setups are rare on synthetic prices; see test_dts_setup_*
            continue
        if st["id"] == "id_volume_spike_fade":  # synthetic volume has no spikes; see test_volume_spike_fade
            continue
        assert len(t["ret"]) > 0, f"{st['id']} {p} never trades"
        assert (t["exit_m"] > t["entry_m"]).all()
        assert (t["exit_m"] <= A.last[t["day"]] + 1).all()
        i = 0
        d, e, x, s = t["day"][i], t["entry_m"][i], t["exit_m"][i], t["dir"][i]
        if st["id"] not in ("id_prev_day_breakout", "id_gap_go", "id_pivot_revert", "id_lunch_breakout", "id_dts_orb", "id_dts_orb_setup"):  # stops fill at the level
            assert t["ret"][i] == pytest.approx(s * (A.P[d, x] / A.P[d, e] - 1) - 2 * I.COST)


def test_fomc_only_trades_on_fomc_days():
    A = I.Days.from_arrays(*synth())
    t = S3.fomc_drift(A, entry=30)
    assert {str(A.dates[d].date()) for d in t["day"]} <= S3._FOMC
    assert (t["entry_m"] == 30).all() and (t["exit_m"] == 270).all()


def test_late_momentum_sign():
    A = I.Days.from_arrays(*synth())
    t = S3.late_momentum(A, ref="open", sig_end=360)
    for d, s in zip(t["day"], t["dir"]):
        assert s == np.sign(A.C[d, 359] / A.O[d, 0] - 1)


def test_volume_spike_fade():
    dates, O, H, L, C, V, last = synth()
    d, m = 7, 100
    V = V.copy()
    V[d, m] = 50 * V[d, m - 30:m].mean()
    C = C.copy()
    C[d, m] = O[d, m] * 1.004  # a sharp up-move on the spike bar
    H = np.maximum(H, C)
    A = I.Days.from_arrays(dates, O, H, L, C, V, last)
    t = S4.volume_spike_fade(A, k=10.0, hold=15)
    i = list(zip(t["day"], t["entry_m"])).index((d, m + 1))
    assert t["dir"][i] == -1.0 and t["exit_m"][i] == m + 16
    assert t["ret"][i] == pytest.approx(-(A.P[d, m + 16] / A.P[d, m + 1] - 1) - 2 * I.COST)


def test_time_of_day_last30_ends_at_close():
    A = I.Days.from_arrays(*synth())
    t = S4.time_of_day(A, window="last30")
    assert (t["exit_m"] == A.last[t["day"]] + 1).all() and (t["exit_m"] - t["entry_m"] == 30).all()


def _orb_day(first_up_bar=3, last=389, bump=None):
    """Synthetic day 5: flat range 99.8-100.2, then every minute from bar `first_up_bar` on trades at 100.5."""
    dates, O, H, L, C, V, lastv = synth()
    d, m0 = 5, first_up_bar * 5
    lastv = lastv.copy()
    lastv[d] = last
    O, H, L, C = (a.copy() for a in (O, H, L, C))
    O[d], C[d], H[d], L[d] = 100.0, 100.0, 100.2, 99.8
    O[d, m0:], C[d, m0:], H[d, m0:], L[d, m0:] = 100.5, 100.5, 100.5, 100.5
    if bump:
        H[d, bump] = 101.6
    return d, I.Days.from_arrays(dates, O, H, L, C, V, lastv)


def _one(A, d, **kw):
    t = S7.id_dts_orb(A, **kw)
    i = list(t["day"]).index(d)
    return t["entry_m"][i], t["exit_m"][i], t["ret"][i]


def test_dts_orb_entry_is_first_five_minute_close_above_range():
    d, A = _orb_day(first_up_bar=5)  # bars 3 and 4 stay inside the range; bar 5 (minutes 25-29) closes above
    e, x, _ = _one(A, d)
    assert e == 30 and A.P[d, e] == 100.5
    d, A = _orb_day(first_up_bar=72)  # first close above the range would fill after 15:30: no trade
    assert d not in set(S7.id_dts_orb(A)["day"])


def test_dts_orb_flat_at_1555_or_session_close():
    d, A = _orb_day()
    e, x, r = _one(A, d, R=2)
    assert (e, x) == (20, 385) and r == pytest.approx(A.P[d, 385] / A.P[d, 20] - 1 - 2 * I.COST)
    d, A = _orb_day(last=209)
    assert _one(A, d, R=2)[1] == 210


def test_dts_orb_two_r_target_return():
    d, A = _orb_day(bump=40)  # entry 100.5, midpoint stop 100.0, 2R target 101.5
    e, x, r = _one(A, d, stop="mid", R=2)
    assert (e, x) == (20, 40) and r == pytest.approx(101.5 / 100.5 - 1 - 2 * I.COST)


def test_dts_setup_filters_use_only_earlier_days():
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    j = 150
    for setup in S7.SETUPS:
        other = close.copy()
        other[j:] *= np.exp(rng.normal(0, 0.1, 300 - j))
        a, b = S7.setup_flags(close, setup), S7.setup_flags(other, setup)
        assert (a[:j + 1] == b[:j + 1]).all(), setup  # day j's flag sees closes of days < j only
        assert a.any(), setup


def test_dts_setup_rules():
    z = [100.0]  # a trailing day to read the flag on (flags look at closes of earlier days only)
    assert S7.setup_flags(np.r_[np.full(25, 100.0), 101.0, z], "breakout_20d")[26]
    assert not S7.setup_flags(np.r_[np.full(25, 100.0), 101.0, 100.0, z], "breakout_20d")[27]
    f = S7.setup_flags(np.r_[np.full(20, 100.0), 90.0, 105.0, z], "reclaim_ma20")
    assert f[22] and not f[21]
    d = np.r_[np.linspace(100, 60, 30), 61.0, z]  # long slide (RSI < 30), then an up close
    assert S7.setup_flags(d, "oversold_bounce")[31]
    g = np.r_[np.full(6, 100.0), np.linspace(100, 120, 10)[1:], np.full(5, 120.0), z]
    assert S7.setup_flags(g, "bull_flag")[len(g) - 1]
