import random

import numpy as np
import pytest

import intraday as I
from iagent import features as F
from iagent import platform as P
from iagent import spec as S
from iagent.loop import run
from test_intraday import perturbed, synth


@pytest.fixture(scope="module")
def mk():
    """Synthetic 'SPY' whose search window is the whole (60-day, 2020) sample."""
    return P.Market("SPY", I.Days.from_arrays(*synth(60)))


def test_features_do_not_peek():
    d0, K = 30, 200
    A = F.build(I.Days.from_arrays(*synth()))
    B = F.build(I.Days.from_arrays(*perturbed(d0, K)))
    assert A.shape == (60, len(F.DECISIONS), len(F.FEATURES))
    assert np.isfinite(A).any() and np.isnan(A).any()
    same = lambda a, b: np.array_equal(a, b, equal_nan=True)
    assert same(A[:d0], B[:d0])
    for i, m in enumerate(F.DECISIONS):
        if m <= K:
            assert same(A[d0, i], B[d0, i]), f"decision {m} on day d0 uses future bars"
        else:
            assert not same(A[d0, i], B[d0, i])   # the test has teeth: later decisions do see the change


def test_half_day_is_nan_after_close():
    D = I.Days.from_arrays(*synth())
    cube = F.build(D)                              # day 10 ends at bar 209
    for i, m in enumerate(F.DECISIONS):
        assert np.isnan(cube[10, i]).all() == (m > 209)


def test_operators_validate_and_hash_stable():
    rng = random.Random(3)
    w = {"gap": 3.0}
    for _ in range(300):
        a, b = S.random_spec(rng, w, "QQQ"), S.random_spec(rng, None, "QQQ")
        for s in (a, S.mutate(a, rng, w), S.crossover(a, b, rng), S.mutate(S.crossover(a, b, rng), rng)):
            S.validate(s)
            c = S.canonical(s)
            S.validate(c)
            assert S.canonical(c) == c and S.spec_hash(s) == S.spec_hash(c)
            assert S.describe(c)
    with pytest.raises(S.SpecError):
        S.validate({**a, "time": 45})
    with pytest.raises(S.SpecError):
        S.validate({**a, "exit": {"type": "hold", "n": 7}})
    assert S.describe({"market": "QQQ", "time": 60, "side": "short", "stop_bp": 50, "exit": {"type": "hold", "n": 30},
                       "entry": [{"feature": "vwap_dist", "op": ">", "q": 90}, {"feature": "ret_30", "op": ">", "q": 70}]}) \
        == "QQQ at 10:30: short when vwap_dist > p90 and ret_30 > p70; hold 30 min; stop 50 bp"


def _spec(side="long", stop=None, ex=None, q=50, op=">"):
    return {"market": "SPY", "time": 120, "side": side, "entry": [{"feature": "gap", "op": op, "q": q}],
            "exit": ex or {"type": "hold", "n": 30}, "stop_bp": stop}


@pytest.mark.parametrize("side", ["long", "short"])
def test_hold_trade_pricing(mk, side):
    D = mk.D
    t = S.trades(_spec(side), D, mk.cube, mk.th)
    assert len(t["ret"]) > 5
    dr = 1.0 if side == "long" else -1.0
    for d, m, j, r in zip(t["day"], t["entry_m"], t["exit_m"], t["ret"]):
        assert j == min(m + 30, D.last[d] + 1)
        assert r == pytest.approx(dr * (D.P[d, j] / D.P[d, m] - 1) - 2 * I.COST)
    tc = S.trades(_spec(side, ex={"type": "close"}), D, mk.cube, mk.th)
    assert (tc["exit_m"] == D.last[tc["day"]] + 1).all()
    # restricting to a window gives the same trades as filtering the full run
    sl = slice(20, 45)
    tw, tf = S.trades(_spec(side), D, mk.cube, mk.th, sl), t
    keep = (tf["day"] >= 20) & (tf["day"] < 45)
    assert list(tw["day"]) == list(tf["day"][keep]) and np.allclose(tw["ret"], tf["ret"][keep])


@pytest.mark.parametrize("side", ["long", "short"])
def test_stop_exits(mk, side):
    D, dr = mk.D, 1.0 if side == "long" else -1.0
    t0 = S.trades(_spec(side, ex={"type": "close"}), D, mk.cube, mk.th)
    t = S.trades(_spec(side, 25, ex={"type": "close"}), D, mk.cube, mk.th)
    assert list(t["day"]) == list(t0["day"])
    n_stopped = 0
    for d, m, j, r, j0 in zip(t["day"], t["entry_m"], t["exit_m"], t["ret"], t0["exit_m"]):
        p_in = D.P[d, m]
        stop = p_in * (1 - dr * 25 / 1e4)
        assert j > m
        if j == j0 and np.isclose(r, dr * (D.P[d, j] / p_in - 1) - 2 * I.COST):
            # no stop: no bar crossed the level before the close
            bars = np.arange(m, j)
            assert not ((D.L[d, bars] <= stop) if dr > 0 else (D.H[d, bars] >= stop)).any()
            continue
        n_stopped += 1
        out = p_in * (1 + dr * (r + 2 * I.COST))
        # the exit is either exactly the stop level or a bar open that gapped through it
        assert np.isclose(out, stop) or (np.isclose(out, D.O[d, j]) and j > m and (out <= stop if dr > 0 else out >= stop))
        assert (out <= p_in) if dr > 0 else (out >= p_in)     # only on the stop side
    assert n_stopped > 0


def test_stop_never_uses_entry_bar_open():
    # entry bar opens at the entry price, so a stop can only trigger via its own range, not its open
    D = I.Days.from_arrays(*synth(60))
    mk2 = P.Market("SPY", D)
    t = S.trades(_spec("long", 25, {"type": "close"}), D, mk2.cube, mk2.th)
    assert (t["exit_m"] > t["entry_m"]).all()


def test_score_status_and_keys(mk):
    ok = {"status", "reward", "coverage"}
    good = P.score(mk, _spec(q=10, op=">"), [])
    assert ok <= set(good) and good["status"] in ("rejected", "scored")
    few = P.score(mk, _spec(q=98), [0.0, 0.01])           # 60 days cannot reach 50 trades
    assert few["status"] == "rejected" and "too few trades" in few["reason"] and few["reward"] == 0.0
    spec = {**_spec(), "time": 30, "entry": [{"feature": "noise_z", "op": ">", "q": 1}]}   # warm-up NaNs
    low = P.score(P.Market("SPY", I.Days.from_arrays(*synth(25))), spec, [])
    assert low["status"] == "rejected" and low["reason"].startswith("coverage")
    # a scored result carries what memory.current_reward needs
    for k in ("T", "skew", "kurt", "daily_sr", "fold_sharpes", "trades", "excess_sharpe"):
        assert k in few


def test_scored_and_holdout_on_long_synthetic():
    import pandas as pd
    n = 1700
    dates, O, H, L, C, V, last = synth(n, seed=5)
    dates = pd.bdate_range("2016-01-04", periods=n)        # spans IS and OOS
    m = P.Market("QQQ", I.Days.from_arrays(dates, O, H, L, C, V, last))
    assert m.search.stop < m.holdout.start + 1 and m.holdout.start < n
    res = P.score(m, {**_spec(), "market": "QQQ", "entry": [{"feature": "gap", "op": ">", "q": 20}]}, [0.0, 0.01, -0.01])
    assert res["status"] == "scored" and {"dsr", "consistent", "reward"} <= set(res)
    assert 0 <= res["reward"] <= 1
    ho = P.holdout_test(m, {**_spec(), "market": "QQQ", "entry": [{"feature": "gap", "op": ">", "q": 20}]}, "x")
    assert {"p_value", "n_sims", "sharpe", "sharpe_2x_cost", "buy_hold_sharpe", "trades", "window"} <= set(ho)
    assert ho["n_sims"] == 20000 and I.N_SIMS == 2000 and 0 < ho["p_value"] <= 1 and ho["trades"] > 0


def test_loop_writes_memory_in_tmp(tmp_path):
    from pathlib import Path
    real = Path(__file__).resolve().parent.parent / "agent_memory"
    snap = lambda: sorted((str(p), p.stat().st_mtime_ns) for p in real.rglob("*") if p.is_file())
    before = snap()
    days = {s: I.Days.from_arrays(*synth(60, seed=i)) for i, s in enumerate(S.MARKETS)}
    mem = run(rounds=2, per_round=5, seed=1, quiet=True, mem_root=tmp_path, days=days)
    assert len(mem.trials) == 10
    assert (tmp_path / "journal.jsonl").read_text().count("\n") == 10
    assert "intraday" in (tmp_path / "lessons.md").read_text().lower()
    assert snap() == before
