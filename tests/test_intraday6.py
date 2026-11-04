import numpy as np
import pytest

import intraday as I
import intraday6 as S6
from test_intraday import perturbed, synth


def flat(n=20):
    dates = I.pd.bdate_range("2020-01-02", periods=n)
    a = np.full((n, 390), 100.0)
    return dates, a.copy(), a.copy(), a.copy(), a.copy(), np.full((n, 390), 1000.0), np.full(n, 389)


def one(d=15, e=10, x=300, s=1.0):
    return {"day": np.array([d]), "entry_m": np.array([e]), "exit_m": np.array([x]), "dir": np.array([s]), "ret": np.array([0.123])}


def run(D, s=1.0, R=2, f=0.01, **kw):
    return S6.overlay(D, one(s=s, **kw), np.full(D.n, f), R)


def mk(**edits):
    dates, O, H, L, C, V, last = flat()
    for arr, (m, v) in edits.items():
        {"O": O, "H": H, "L": L, "C": C}[arr][15, m] = v
    return I.Days.from_arrays(dates, O, H, L, C, V, last)


def test_target_hit_fills_at_target():
    t = run(mk(H=(50, 102.5)))
    assert t["exit_m"][0] == 50 and t["ret"][0] == pytest.approx(0.02 - 2 * I.COST)


def test_short_target_and_stop():
    assert run(mk(L=(50, 97.5)), s=-1.0)["ret"][0] == pytest.approx(0.02 - 2 * I.COST)
    assert run(mk(H=(50, 101.5)), s=-1.0)["ret"][0] == pytest.approx(-0.01 - 2 * I.COST)


def test_stop_fills_at_stop_and_both_in_one_bar_takes_stop():
    assert run(mk(L=(40, 98.5)))["ret"][0] == pytest.approx(-0.01 - 2 * I.COST)
    t = run(mk(L=(40, 98.0), H=(40, 103.0)))
    assert t["exit_m"][0] == 40 and t["ret"][0] == pytest.approx(-0.01 - 2 * I.COST)


def test_gapped_open_fills_at_the_open():
    t = run(mk(O=(30, 104.0), H=(30, 104.0)))
    assert t["exit_m"][0] == 30 and t["ret"][0] == pytest.approx(0.04 - 2 * I.COST)
    t = run(mk(O=(30, 97.0), L=(30, 97.0)))
    assert t["ret"][0] == pytest.approx(-0.03 - 2 * I.COST)


def test_never_exits_before_entry_and_entry_bar_open_is_the_fill():
    t = run(mk(L=(10, 98.0)))  # touches the stop on the entry bar itself
    assert t["exit_m"][0] == 11 and t["ret"][0] == pytest.approx(-0.01 - 2 * I.COST)
    D = mk(O=(10, 104.0), H=(10, 104.0))  # the entry bar's open is the entry price, not a gap exit
    assert run(D)["exit_m"][0] > 10


def test_extremes():
    D = mk(H=(50, 102.5), L=(60, 90.0))
    assert run(D, R=1000)["ret"][0] == pytest.approx(-0.01 - 2 * I.COST)  # target unreachable: the stop
    t = run(D, f=1e-9)  # a tiny stop and target: out at the first bar that moves at all
    assert t["exit_m"][0] == 50 and t["ret"][0] == pytest.approx(2e-9 - 2 * I.COST)
    D2 = mk()
    t = run(D2, R=1000)
    assert t["exit_m"][0] == 300 and t["ret"][0] == 0.123  # nothing touched: base exit kept


def test_nan_atr_keeps_base_exit():
    D = mk(H=(50, 102.5))
    t = S6.overlay(D, one(), np.full(D.n, np.nan), 2)
    assert t["exit_m"][0] == 300 and t["ret"][0] == 0.123


def test_atr_frac_uses_only_previous_sessions():
    A = I.Days.from_arrays(*synth())
    a = S6.atr_frac(A)
    assert np.isnan(a[:14]).all() and np.isfinite(a[14:]).all()
    H, L = S6._day_hl(A)
    assert a[20] == pytest.approx(((H - L) / A.close)[6:20].mean())


def test_study6_no_peek_and_valid_trades():
    d0, K = 30, 200
    A, B = I.Days.from_arrays(*synth()), I.Days.from_arrays(*perturbed(d0, K))
    for p in S6.STRATEGIES[0]["grid"]:
        ta, tb = S6.noise_sl_tp(A, **p), S6.noise_sl_tp(B, **p)
        assert len(ta["ret"]) > 0
        assert (ta["exit_m"] > ta["entry_m"]).all() and (ta["exit_m"] <= A.last[ta["day"]] + 1).all()
        early = lambda t: sorted(zip(*(t[k][(t["day"] < d0) | ((t["day"] == d0) & (t["entry_m"] <= K))] for k in ("day", "entry_m", "dir"))))
        assert early(ta) == early(tb)
