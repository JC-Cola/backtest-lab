import numpy as np
import pandas as pd
import pytest

from stats import coin_flip
from strategies import STRATEGIES

K = 1000


def synth(seed, n=1500):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-01", periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    o = c * np.exp(rng.normal(0, 0.004, n))
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.01, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.01, n))
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=idx)


@pytest.mark.parametrize("st", STRATEGIES, ids=[s["id"] for s in STRATEGIES])
def test_no_lookahead(st):
    df = synth(1)
    df2 = df.copy()
    rng = np.random.default_rng(99)
    m = len(df) - K - 1
    c = df.Close.values[K + 1:] * np.exp(np.cumsum(rng.normal(0, 0.05, m)))  # wild future
    o = c * np.exp(rng.normal(0, 0.02, m))
    df2.iloc[K + 1:, df.columns.get_loc("Close")] = c
    df2.iloc[K + 1:, df.columns.get_loc("Open")] = o
    df2.iloc[K + 1:, df.columns.get_loc("High")] = np.maximum(o, c) * 1.03
    df2.iloc[K + 1:, df.columns.get_loc("Low")] = np.minimum(o, c) * 0.97
    for params in st["grid"]:
        a = st["fn"](df, **params).iloc[: K + 1].fillna(0).values
        b = st["fn"](df2, **params).iloc[: K + 1].fillna(0).values
        np.testing.assert_array_equal(a, b, err_msg=f"{st['id']} {params} peeks at the future")
        assert np.abs(st["fn"](df, **params)).max() <= 1.0 + 1e-12


def test_coinflip_pvalue_for_random_strategy_not_small():
    df = synth(7, 2000)
    o = df.Open.values
    rng = np.random.default_rng(5)
    ps = []
    for i in range(60):
        k = 25
        h = rng.integers(2, 15, k)
        free = len(o) - 1 - h.sum()
        cuts = np.sort(rng.integers(0, free + 1, k))
        ent = cuts + np.cumsum(h) - h
        d = rng.choice([1.0, -1.0], k)
        tr = {"entry": ent, "exit": ent + h, "dir": d}
        ps.append(coin_flip(o, tr, 3, n_sims=500, seed=i)["p_value"])
    ps = np.array(ps)
    assert 0.3 < ps.mean() < 0.7
    assert (ps < 0.05).mean() < 0.15
