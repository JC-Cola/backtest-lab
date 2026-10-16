import json
import random

import numpy as np
import pandas as pd
import pytest

from agent import features as F
from agent import spec as S
from agent.memory import Memory
from data import CTX_TICKERS


def synth(n=1500, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2012-01-02", periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    o = c * np.exp(rng.normal(0, 0.004, n))
    df = pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.005, "Low": np.minimum(o, c) * 0.995, "Close": c}, index=idx)
    ctx = pd.DataFrame({t: 20 * np.exp(np.cumsum(rng.normal(0, 0.03, n))) for t in CTX_TICKERS}, index=idx)
    return df, ctx


def test_features_are_point_in_time():
    df, ctx = synth()
    full = F.build(df, ctx)
    K = 1000
    cut = F.build(df.iloc[: K + 1], ctx.iloc[: K + 1])
    pd.testing.assert_frame_equal(full.iloc[: K + 1], cut, check_freq=False)
    df2, ctx2 = df.copy(), ctx.copy()
    df2.iloc[K + 1:] *= 1.7
    ctx2.iloc[K + 1:] *= 0.3
    pd.testing.assert_frame_equal(full.iloc[: K + 1], F.build(df2, ctx2).iloc[: K + 1], check_freq=False)


def test_random_specs_validate_and_compile_without_lookahead():
    df, ctx = synth()
    tab = F.build(df, ctx)
    th = S.thresholds(tab, slice(0, 1000))
    rng = random.Random(0)
    K = 1100
    tab_cut = F.build(df.iloc[: K + 1], ctx.iloc[: K + 1])
    for _ in range(60):
        s = S.canonical(S.random_spec(rng))
        S.validate(s)
        p = S.positions(s, tab, th)
        assert set(np.unique(p)) <= {0.0, 1.0}
        np.testing.assert_array_equal(p[: K + 1], S.positions(s, tab_cut, th))
        assert S.spec_hash(s) == S.spec_hash(json.loads(json.dumps(s)))
        m = S.mutate(s, rng)
        S.validate(m)


@pytest.mark.parametrize("bad", [
    {"market": "SPY", "entry": [], "exit": {"type": "hold", "n": 3}},
    {"market": "XXX", "entry": [{"feature": "ibs", "op": "<", "q": 10}], "exit": {"type": "hold", "n": 3}},
    {"market": "SPY", "entry": [{"feature": "tomorrow_close", "op": "<", "q": 10}], "exit": {"type": "hold", "n": 3}},
    {"market": "SPY", "entry": [{"feature": "ibs", "op": "<", "q": 150}], "exit": {"type": "hold", "n": 3}},
])
def test_invalid_specs_rejected(bad):
    with pytest.raises(S.SpecError):
        S.validate(bad)


def test_memory_persists_and_dedupes(tmp_path):
    m = Memory(tmp_path)
    s = S.canonical(S.random_spec(random.Random(1), market="SPY"))
    m.add({"hash": S.spec_hash(s), "spec": s, "status": "scored", "reward": 0.4, "daily_sr": 0.01,
           "fold_sharpes": [0.1, 0.2, 0.3]})
    m2 = Memory(tmp_path)  # a new session reads the journal first
    assert len(m2.trials) == 1 and S.spec_hash(s) in m2.seen
    assert m2.trial_sharpes("SPY") == [0.01]
    from agent.proposer import EvolutionProposer
    props = EvolutionProposer(m2).propose(20, ["SPY"])
    assert all(S.spec_hash(p) not in m2.seen for p in props)
