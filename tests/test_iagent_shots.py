"""Budgeted held-out protocol of the intraday agent: similarity, monthly budget, robustness ranking, BH across shots,
feature-weight learning."""
import copy
import random
from datetime import datetime, timezone

import pytest

import candidates
from iagent import shots as H
from iagent import spec as S
from iagent.memory import IMemory

BASE = {"market": "SPY", "time": 120, "side": "long", "stop_bp": 50, "exit": {"type": "hold", "n": 30},
        "entry": [{"feature": "gap", "op": ">", "q": 50}, {"feature": "vwap_dist", "op": "<", "q": 10}]}
NOW = datetime(2026, 12, 10, 12, tzinfo=timezone.utc)


def spec(**kw):
    s = copy.deepcopy(BASE)
    s.update(kw)
    return s


def rec(s, folds=(1.5, 2.0, 1.2), f2=(1.0, 1.4, 0.8), trades=300, **kw):
    r = {"hash": S.spec_hash(s), "spec": s, "proposer": "evolution", "status": "scored", "reward": 0.0, "dsr": 0.0,
         "consistent": True, "fold_sharpes": list(folds), "trades": trades, "daily_sr": 0.05, "T": 1250,
         "skew": 0.0, "kurt": 3.0}
    if f2 is not None:
        r["fold_sharpes_2x"] = list(f2)
    return r | kw


def mem_with(tmp_path, recs):
    m = IMemory(tmp_path)
    for r in recs:
        m.add(r)
    return m


# ---------------------------------------------------------------- similarity
def test_similarity_rule():
    a = spec()
    assert H.similar(a, copy.deepcopy(a))
    b = spec(); b["entry"][0]["q"] = 55                       # +5 percentile tweak -> duplicate
    assert H.similar(a, b) and H.similar(b, a)
    b["entry"][0]["q"] = 56                                    # +6 -> distinct
    assert not H.similar(a, b)
    c = spec(); c["entry"].reverse(); c["entry"][1]["q"] = 46  # order-insensitive, within 5
    assert H.similar(a, c)
    for kw in ({"time": 150}, {"side": "short"}, {"market": "QQQ"}):
        assert not H.similar(a, spec(**kw)), kw
    # same idea, only the stop / target / exit hold differ -> near-duplicate (do not spend two shots on it)
    for kw in ({"stop_bp": 100}, {"stop_bp": None}, {"exit": {"type": "close"}}, {"exit": {"type": "hold", "n": 120}},
               {"target_r": 2}, {"stop_bp": None, "exit": {"type": "close"}, "target_r": 3}):
        assert H.similar(a, spec(**kw)) and H.similar(spec(**kw), a), kw
    g = spec(stop_bp=None); g["entry"][0]["q"] = 56            # exit variant AND a threshold beyond 5 -> still distinct
    assert not H.similar(a, g)
    d = spec(); d["entry"][0]["op"] = "<"                      # different operator
    assert not H.similar(a, d)
    e = spec(); e["entry"][0]["feature"] = "ret_open"
    assert not H.similar(a, e)
    f = spec(); f["entry"] = f["entry"][:1]                    # different number of conditions
    assert not H.similar(a, f)


# ---------------------------------------------------------------- robustness ranking
def test_robustness_gates_and_order():
    s = spec()
    assert H.robustness(rec(s)) == pytest.approx(1.0)                      # min(median 1x = 1.5, median 2x = 1.0)
    assert H.robustness(rec(s, folds=(2.8, 0.0, 2.0))) == 0                # a fold not positive
    assert H.robustness(rec(s, f2=(1.0, -0.1, 0.8))) == 0                  # negative at 2x costs
    assert H.robustness(rec(s, trades=149)) == 0                           # too few trades
    assert H.robustness(rec(s, f2=None)) == pytest.approx(1.5)             # provisional (no 2x yet)
    assert H.robustness(rec(s, folds=(1, 1))) == 0


def test_top_ranks_by_robustness_not_deflated_reward(tmp_path):
    lo, hi = spec(time=60), spec(time=90)
    m = mem_with(tmp_path, [rec(lo, folds=(0.4, 0.5, 0.6), f2=(0.3, 0.4, 0.5), reward=0.9),
                            rec(hi, folds=(2.0, 2.5, 1.8), f2=(1.5, 2.0, 1.4), reward=0.0),
                            rec(spec(time=120), folds=(3.0, -1.0, 3.0), f2=None)])
    assert [t["spec"]["time"] for t in m.top(3)] == [90, 60, 120]


def test_next_shot_picks_best_untested_nonduplicate_and_fills_2x(tmp_path):
    a, dup, other, weak2x = spec(time=60), spec(time=60), spec(time=90), spec(time=150)
    dup["entry"][0]["q"] = 53                                              # near-duplicate of a
    m = mem_with(tmp_path, [rec(a, folds=(2.0, 2.2, 2.4), f2=(1.5, 1.6, 1.7)),
                            rec(dup, folds=(2.0, 2.2, 2.3), f2=(1.5, 1.6, 1.6)),
                            rec(other, folds=(1.0, 1.2, 1.1), f2=(0.8, 0.9, 0.7)),
                            rec(weak2x, folds=(3.0, 3.0, 3.0), f2=None)])  # best at 1x but dies at 2x costs
    calls = []
    def ev(s):
        calls.append(s["time"])
        return {"fold_sharpes": [3.0, 3.0, 3.0], "fold_sharpes_2x": [0.5, -0.2, 0.4], "trades": 400}
    first = H.next_shot(m, ev)
    assert first["spec"]["time"] == 60 and calls == [150]
    assert m.trials[3]["fold_sharpes_2x"] == [0.5, -0.2, 0.4]
    assert "fold_sharpes_2x" in (tmp_path / "journal.jsonl").read_text()    # written back to the journal
    m.update(first["n"], holdout={"p_value": 0.5, "sharpe": 0, "sharpe_2x_cost": 0})
    assert H.next_shot(m, ev)["spec"]["time"] == 90                       # dup of the tested one is skipped
    assert [t["spec"]["time"] for t in H.plan(mem_with(tmp_path / "x", [rec(a), rec(dup), rec(other)]), ev, 3)] == [60, 90]


# ---------------------------------------------------------------- monthly budget
def test_monthly_budget(tmp_path):
    specs = [spec(time=t) for t in (30, 60, 90, 120, 150)]
    m = mem_with(tmp_path, [rec(s, folds=(1 + i, 1 + i, 1 + i), f2=(1 + i, 1 + i, 1 + i)) for i, s in enumerate(specs)])
    spent_on = []
    def ho(mk, s, h):
        spent_on.append(s["time"])
        return {"p_value": 0.2, "sharpe": 0.5, "sharpe_2x_cost": 0.3, "trades": 90, "window": ["2021-01-04", "2026-11-01"]}
    ev = lambda s: pytest.fail("all records already have 2x folds")
    got = H.spend(m, lambda s: None, ev, ho, now=NOW)
    assert len(got) == H.K_PER_MONTH == 3 and spent_on == [150, 120, 90]  # best robustness first
    assert H.shots_this_month(m, NOW) == 3
    assert H.spend(m, lambda s: None, ev, ho, now=NOW) == [] and len(spent_on) == 3   # budget exhausted: no more shots
    assert all(m.trials[n - 1]["status"] == "promoted" and m.trials[n - 1]["holdout"]["ts"].startswith("2026-12") for n in got)
    nxt = datetime(2027, 1, 2, tzinfo=timezone.utc)                        # new calendar month: budget refills
    assert H.shots_this_month(m, nxt) == 0
    assert len(H.spend(m, lambda s: None, ev, ho, now=nxt)) == 2           # only 2 untested ideas are left
    assert len(IMemory(tmp_path).holdout_tests()) == 5                     # persisted


# ---------------------------------------------------------------- BH across ALL shots + noise band
def test_bh_across_all_shots_and_band(tmp_path, monkeypatch):
    monkeypatch.setattr(IMemory, "prior_window_pvalues", lambda self: [0.5] * 20)   # earlier tests on the same years
    mk = lambda i, p, sh, sh2: rec(spec(time=30 * (i + 1)), holdout={"p_value": p, "sharpe": sh, "sharpe_2x_cost": sh2,
                                                                          "lockbox": {"ok": True}})
    m = mem_with(tmp_path, [mk(0, 0.001, 1.0, 0.8), mk(1, 0.001, 1.0, 0.2), mk(2, 0.04, 1.0, 0.9), mk(3, 0.001, 1.0, -0.1)])
    m.band = 0.5
    v = {x["n"]: x for x in m.holdout_verdicts()}
    assert v[1]["pass"] and v[1]["stat_pass"] and v[1]["beats_band"]
    assert v[2]["stat_pass"] and not v[2]["beats_band"] and not v[2]["pass"]     # significant but under the noise band
    assert not v[3]["stat_pass"] and not v[3]["pass"]                            # p=0.04 does not survive BH over 24 tests
    assert not v[4]["stat_pass"]                                                  # negative at 2x costs
    assert all("dsr_now" in x for x in v.values())                                # reported, not gating
    # the more shots ever spent, the harder it gets: add 30 more null shots, the first still passes only if q < 0.10
    m2 = mem_with(tmp_path / "b", [mk(0, 0.001, 1.0, 0.8)] + [mk(i % 11, 0.6, 0.1, 0.05) for i in range(30)])
    m2.band = 0.5
    assert m2.holdout_verdicts()[0]["q"] > v[1]["q"]                              # q grows with the number of shots ever spent
    m.band = None
    monkeypatch.setattr(candidates, "benchmarks", lambda *a, **k: {"intraday": None, "long-term": None})
    assert not any(x["pass"] for x in m.holdout_verdicts())                       # no band known -> fail closed


def test_candidates_still_need_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(IMemory, "prior_window_pvalues", lambda self: [])
    h = {"p_value": 0.001, "sharpe": 1.0, "sharpe_2x_cost": 0.8, "lockbox": {"ok": True}, "trades": 100, "window": ["2021-01-04", "2026-11-01"]}
    m = mem_with(tmp_path / "i", [rec(spec(time=60), holdout=h), rec(spec(time=90), holdout=h | {"sharpe_2x_cost": 0.2})])
    m.band = 0.5
    from agent.memory import Memory
    monkeypatch.setattr(Memory, "prior_window_pvalues", lambda self: [])
    doc = candidates.update(Memory(tmp_path / "d"), m, path=tmp_path / "c.json", results_dir=tmp_path, live=[])
    st = {c["description"]: c["status"] for c in doc["candidates"]}
    assert doc["benchmarks"]["intraday"] is None                       # results_dir is empty: no benchmark -> nothing is "better"
    assert sorted(st.values()) == ["awaiting approval", candidates.NOT_BETTER]   # 0.8 beats the band 0.5, 0.2 does not; never "approved" by itself
    doc = candidates.update(Memory(tmp_path / "d"), m, path=tmp_path / "c.json", results_dir=tmp_path, live=[])
    assert all(c["status"] != "approved" for c in doc["candidates"])


# ---------------------------------------------------------------- feature weights learn
def test_feature_weights_move_away_from_one(tmp_path):
    rng = random.Random(0)
    recs = []
    for i in range(60):
        good = i % 2 == 0
        s = spec(time=30 * (1 + i % 12), entry=[{"feature": "gap" if good else "rv_5d", "op": ">", "q": 5 + (i % 18) * 5}])
        base = 1.8 if good else 0.2
        recs.append(rec(s, folds=tuple(base + rng.uniform(-0.2, 0.2) for _ in range(3)), f2=None, reward=0.0))   # reward all 0, like now
    w = IMemory(tmp_path)
    for r in recs:
        w.add(r)
    fw = w.feature_weights()
    assert fw["gap"] > 1.5 and fw["rv_5d"] < 0.7 and 0.2 <= fw["rv_5d"] and fw["gap"] <= 5.0
    assert fw["gap"] / fw["rv_5d"] > 3
