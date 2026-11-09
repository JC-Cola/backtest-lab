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


# ------------------------------------------------------------------ take-profit (target_r), wider grammar
OLD_A = {"market": "SPY", "time": 120, "side": "long", "stop_bp": 50, "exit": {"type": "hold", "n": 30},
         "entry": [{"feature": "gap", "op": ">", "q": 50}, {"feature": "vwap_dist", "op": "<", "q": 10}]}
OLD_B = {"market": "QQQ", "time": 330, "side": "short", "exit": {"type": "close"},
         "entry": [{"feature": "ret_30", "op": ">", "q": 70.4}]}


def test_old_spec_hashes_unchanged():
    # hashes computed with the code before target_r existed
    assert S.spec_hash(OLD_A) == "dba3f672b312" and S.spec_hash(OLD_B) == "52ce9dd84f11"
    assert S.spec_hash({**OLD_A, "target_r": None}) == "dba3f672b312"
    assert S.spec_hash({**OLD_A, "target_r": 2}) != S.spec_hash(OLD_A)
    assert S.spec_hash({**OLD_A, "target_r": 2}) == S.spec_hash({**OLD_A, "target_r": 2.0})
    assert "target_r" not in S.canonical({**OLD_A, "target_r": None})


def test_journal_hashes_still_valid():
    import json
    from pathlib import Path
    j = Path(__file__).resolve().parent.parent / "agent_memory" / "intraday" / "journal.jsonl"
    if not j.exists():
        pytest.skip("no journal")
    for line in j.read_text().splitlines():
        r = json.loads(line)
        if r.get("spec") is None:  # an invalid proposal (e.g. from Claude) is journaled as rejected with its raw text only
            assert r.get("status") == "rejected" and "raw" in r
            continue
        S.validate(r["spec"])
        assert S.spec_hash(r["spec"]) == r["hash"]


def test_target_validation():
    S.validate({**OLD_A, "target_r": 3})
    S.validate({**OLD_A, "target_r": None})
    with pytest.raises(S.SpecError):
        S.validate({**OLD_B, "target_r": 2})                  # no stop
    with pytest.raises(S.SpecError):
        S.validate({**OLD_B, "stop_bp": None, "target_r": 2})
    with pytest.raises(S.SpecError):
        S.validate({**OLD_A, "target_r": 5})
    with pytest.raises(S.SpecError):
        S.validate({**OLD_A, "stop_bp": 75})
    for n in (5, 240):
        S.validate({**OLD_A, "exit": {"type": "hold", "n": n}})
    assert S.describe({**OLD_A, "target_r": 3}).endswith("; stop 50 bp, target 3R")
    assert S.describe({**OLD_A, "target_r": 1.5}).endswith("stop 50 bp, target 1.5R")


def test_operators_make_targets():
    rng = random.Random(11)
    specs = [S.random_spec(rng) for _ in range(400)]
    specs += [S.mutate(s, rng) for s in specs[:200]] + [S.crossover(specs[i], specs[i + 1], rng) for i in range(0, 200, 2)]
    for s in specs:
        S.validate(s)
        assert s.get("target_r") is None or s["stop_bp"]
    withstop = [s for s in specs[:400] if s["stop_bp"]]
    frac = sum(1 for s in withstop if s.get("target_r")) / len(withstop)
    assert 0.25 < frac < 0.55
    assert {s["time"] for s in specs} >= {150, 210, 270} and {s["stop_bp"] for s in specs} >= {10, 200}


def _flat_market(side, mods):
    n = 60
    a = np.full((n, 390), 100.0)
    arr = {"O": a.copy(), "H": a.copy(), "L": a.copy(), "C": a.copy()}
    for (k, d, m), v in mods.items():
        arr[k][d, m] = v
    D = I.Days.from_arrays(I.pd.bdate_range("2020-01-02", periods=n), arr["O"], arr["H"], arr["L"], arr["C"],
                           np.full((n, 390), 1000.0), np.full(n, 389))
    cube = np.full((n, len(F.DECISIONS), len(F.FEATURES)), np.nan)
    cube[:, :, F.FEATURES.index("gap")] = np.arange(n)[:, None]
    s = {**_spec(side, 50, {"type": "close"}), "target_r": 2, "entry": [{"feature": "gap", "op": ">", "q": 1}]}
    t = S.trades(s, D, cube, S.thresholds(cube, slice(0, n)))
    return {d: (x, r) for d, x, r in zip(t["day"], t["exit_m"], t["ret"])}


def test_target_pricing_synthetic_days():
    c = 2 * I.COST
    long = _flat_market("long", {("H", 5, 170): 101.2,                     # target (+100 bp) hit mid-day
                                   ("H", 6, 170): 101.5, ("L", 6, 170): 99.4,  # stop and target in one bar: the stop
                                   ("O", 7, 180): 101.5, ("H", 7, 180): 101.5,  # opens through the target
                                   ("O", 8, 180): 99.0, ("L", 8, 180): 99.0,    # opens through the stop
                                   ("L", 9, 162): 99.4})                       # stop touched
    assert long[5] == (170, pytest.approx(0.01 - c))
    assert long[6] == (170, pytest.approx(-0.005 - c))
    assert long[7] == (180, pytest.approx(0.015 - c))
    assert long[8] == (180, pytest.approx(-0.01 - c))
    assert long[9] == (162, pytest.approx(-0.005 - c))
    assert long[10] == (390, pytest.approx(-c))                              # untouched: held to the close
    short = _flat_market("short", {("L", 5, 170): 98.8, ("L", 6, 170): 98.0, ("H", 6, 170): 100.6,
                                     ("O", 7, 180): 98.5, ("L", 7, 180): 98.5, ("O", 8, 180): 101.0, ("H", 8, 180): 101.0})
    assert short[5] == (170, pytest.approx(0.01 - c))
    assert short[6] == (170, pytest.approx(-0.005 - c))
    assert short[7] == (180, pytest.approx(0.015 - c))
    assert short[8] == (180, pytest.approx(-0.01 - c))


@pytest.mark.parametrize("side", ["long", "short"])
def test_target_exits_on_synthetic_history(mk, side):
    D, dr = mk.D, 1.0 if side == "long" else -1.0
    sp = {**_spec(side, 25, {"type": "close"}), "target_r": 2}
    t, ts = S.trades(sp, D, mk.cube, mk.th), S.trades(_spec(side, 25, {"type": "close"}), D, mk.cube, mk.th)
    assert list(t["day"]) == list(ts["day"]) and (t["exit_m"] > t["entry_m"]).all()
    n_tgt = 0
    for d, m, j, r in zip(t["day"], t["entry_m"], t["exit_m"], t["ret"]):
        p_in, out = D.P[d, m], D.P[d, m] * (1 + dr * (r + 2 * I.COST))
        stop, tgt = p_in * (1 - dr * 25 / 1e4), p_in * (1 + dr * 2 * 25 / 1e4)
        assert any(np.isclose(out, x) for x in (stop, tgt, D.O[d, j], D.P[d, D.last[d] + 1]))
        n_tgt += np.isclose(out, tgt)
        # before the exit bar nothing touched either level
        bars = np.arange(m, min(j, D.last[d] + 1))
        lo, hi = (D.L[d, bars], D.H[d, bars]) if dr > 0 else (-D.H[d, bars], -D.L[d, bars])
        assert not (lo <= (stop if dr > 0 else -stop)).any() and not (hi >= (tgt if dr > 0 else -tgt)).any()
    assert n_tgt > 0


def test_new_grid_and_cube_shape(mk):
    assert F.DECISIONS == list(range(30, 361, 30)) and len(F.DECISIONS) == 12
    assert mk.cube.shape == (60, 12, len(F.FEATURES)) and set(mk.th) == {(m, f) for m in F.DECISIONS for f in F.FEATURES}
    for m in (150, 210, 270):
        sp = {**_spec(), "time": m, "exit": {"type": "hold", "n": 240}}
        t = S.trades(sp, mk.D, mk.cube, mk.th)
        assert len(t["ret"]) > 0 and (t["entry_m"] == m).all() and (t["exit_m"] <= mk.D.last[t["day"]] + 1).all()
