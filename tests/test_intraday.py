import numpy as np
import pandas as pd
import pytest

import intraday as I


def synth(n=60, seed=0):
    rng = np.random.default_rng(seed)
    C = 100 * np.exp(np.cumsum(rng.normal(0, 0.0007, (n, 390)), axis=1) + np.cumsum(rng.normal(0, 0.01, n))[:, None])
    O = np.c_[C[:, :1] * np.exp(rng.normal(0, 0.003, (n, 1))), C[:, :-1]]
    H, L = np.maximum(O, C) * 1.0003, np.minimum(O, C) * 0.9997
    V = rng.integers(1000, 5000, (n, 390)).astype(float)
    last = np.full(n, 389)
    last[10] = 209  # a half day
    return pd.bdate_range("2020-01-02", periods=n), O, H, L, C, V, last


def perturbed(d0, K, seed=1):
    dates, O, H, L, C, V, last = synth()
    rng = np.random.default_rng(seed)
    f = np.exp(rng.normal(0, 0.02, (len(dates), 390)))
    mask = np.zeros_like(O, bool)
    mask[d0, K:] = True
    mask[d0 + 1:, :] = True
    return dates, *(np.where(mask, x * f, x) if i < 4 else x for i, x in enumerate((O, H, L, C))), V, last


@pytest.mark.parametrize("st", [s for s in I.STRATEGIES], ids=[s["id"] for s in I.STRATEGIES])
def test_entries_do_not_peek(st):
    d0, K = 30, 200
    A = I.Days.from_arrays(*synth())
    B = I.Days.from_arrays(*perturbed(d0, K))
    for p in st["grid"]:
        ta, tb = st["fn"](A, **p), st["fn"](B, **p)
        def early(t):
            keep = (t["day"] < d0) | ((t["day"] == d0) & (t["entry_m"] <= K))
            if st["id"] == "id_overnight":  # entered at the previous close, exits on day `day`
                keep = t["day"] <= d0
            return sorted(zip(t["day"][keep], t["entry_m"][keep], t["dir"][keep]))
        assert early(ta) == early(tb), f"{st['id']} {p} decides with future prices"


def test_trade_returns_use_next_bar_open_and_costs():
    A = I.Days.from_arrays(*synth())
    t = I.intraday_only(A)
    d = 5
    i = list(t["day"]).index(d)
    assert t["ret"][i] == pytest.approx(A.close[d] / A.O[d, 0] - 1 - 2 * I.COST)
    t = I.intraday_momentum(A, sig_min=30, hold=30)
    i = list(t["day"]).index(d)
    assert t["entry_m"][i] == A.last[d] + 1 - 30 and t["exit_m"][i] == A.last[d] + 1
