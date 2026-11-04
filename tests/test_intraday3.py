import numpy as np
import pytest

import intraday as I
import intraday3 as S3
from test_intraday import perturbed, synth


@pytest.mark.parametrize("st", S3.STRATEGIES, ids=[s["id"] for s in S3.STRATEGIES])
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


@pytest.mark.parametrize("st", S3.STRATEGIES, ids=[s["id"] for s in S3.STRATEGIES])
def test_trades_are_intraday_and_priced(st):
    A = I.Days.from_arrays(*synth())
    for p in st["grid"]:
        t = st["fn"](A, **p)
        assert len(t["ret"]) > 0, f"{st['id']} {p} never trades"
        assert (t["exit_m"] > t["entry_m"]).all()
        assert (t["exit_m"] <= A.last[t["day"]] + 1).all()
        i = 0
        d, e, x, s = t["day"][i], t["entry_m"][i], t["exit_m"][i], t["dir"][i]
        if st["id"] not in ("id_prev_day_breakout",):  # its stop exits fill at the level, not P
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
