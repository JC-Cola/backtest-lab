import numpy as np
import pytest

import intraday as I
import intraday3 as S3
import intraday4 as S4
import intraday5 as S5
from test_intraday import perturbed, synth


@pytest.mark.parametrize("st", S3.STRATEGIES + S4.STRATEGIES + S5.STRATEGIES, ids=[s["id"] for s in S3.STRATEGIES + S4.STRATEGIES + S5.STRATEGIES])
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


@pytest.mark.parametrize("st", S3.STRATEGIES + S4.STRATEGIES + S5.STRATEGIES, ids=[s["id"] for s in S3.STRATEGIES + S4.STRATEGIES + S5.STRATEGIES])
def test_trades_are_intraday_and_priced(st):
    A = I.Days.from_arrays(*synth())
    for p in st["grid"]:
        t = st["fn"](A, **p)
        if st["id"] == "id_volume_spike_fade":  # synthetic volume has no spikes; see test_volume_spike_fade
            continue
        assert len(t["ret"]) > 0, f"{st['id']} {p} never trades"
        assert (t["exit_m"] > t["entry_m"]).all()
        assert (t["exit_m"] <= A.last[t["day"]] + 1).all()
        i = 0
        d, e, x, s = t["day"][i], t["entry_m"][i], t["exit_m"][i], t["dir"][i]
        if st["id"] not in ("id_prev_day_breakout", "id_gap_go", "id_pivot_revert", "id_lunch_breakout"):  # stops fill at the level
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
