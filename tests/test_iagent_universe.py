"""Research universe, scanner feature families (no lookahead), lockbox names, multiple-testing count across names."""
import json
import random
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

import intraday as I
from iagent import bars5
from iagent import features as F
from iagent import platform as P
from iagent import shots as H
from iagent import spec as S
from iagent import universe as U
from iagent.loop import run
from iagent.memory import IMemory
from test_intraday import perturbed, synth

NEW = F.SCANNER_FEATURES


# ---------------------------------------------------------------- universe selection
def liq(n=60):
    rng = random.Random(1)
    out = {f"S{i:02d}": {"price": 50.0, "median_dollar_volume": 1e9 - i * 1e6, "sessions": 250} for i in range(n)}
    out["PENNY"] = {"price": 3.0, "median_dollar_volume": 5e10, "sessions": 250}      # most traded but under $5
    out["SPY"] = {"price": 600.0, "median_dollar_volume": 9e10, "sessions": 250}
    return out


def test_select_top_n_core_floor_and_lockbox_share():
    u = U.select(liq(), date(2026, 11, 23))
    assert u["search"][:2] == ["SPY", "QQQ"] and "PENNY" not in u["search"] + u["lockbox"]
    names = [r["symbol"] for r in u["ranked"]]
    assert names == [f"S{i:02d}" for i in range(40)]                       # top 40 by median dollar volume
    assert len(u["lockbox"]) == 8 and set(u["lockbox"]) <= set(names)      # 20% of 40
    assert not set(u["lockbox"]) & set(u["search"])                         # never searched
    assert set(u["search"]) == {"SPY", "QQQ"} | (set(names) - set(u["lockbox"]))
    assert u == U.select(liq(), date(2026, 11, 23))                         # deterministic
    assert U.select(liq(), date(2026, 12, 23))["lockbox"] != u["lockbox"]   # seeded by the month


def test_lockbox_sticky_and_never_a_searched_name():
    u1 = U.select(liq(), date(2026, 11, 23))
    u2 = U.select(liq(), date(2026, 12, 23), prev=u1, ever_search=u1["search"])
    assert set(u1["lockbox"]) <= set(u2["lockbox"])                         # a lockbox name stays lockbox
    assert not set(u2["lockbox"]) & set(u1["search"])                       # a searched name never becomes one
    # a lockbox name that left the top 40 and came back is still lockbox, never searched
    shrunk = {k: v for k, v in liq().items() if k not in u1["lockbox"]}
    u3 = U.select(shrunk, date(2027, 1, 5), prev=u1)
    u4 = U.select(liq(), date(2027, 2, 5), prev=u3)
    assert set(u1["lockbox"]) <= set(u4["lockbox"]) and not set(u1["lockbox"]) & set(u4["search"])


def test_ensure_refreshes_monthly_and_never_raises(tmp_path):
    calls = []
    def fetch(syms):
        calls.append(len(syms))
        return liq()
    cand = [f"S{i:02d}" for i in range(60)]
    u = U.ensure(tmp_path, date(2026, 11, 23), cand, fetch)
    assert (tmp_path / "universe.json").exists() and json.loads((tmp_path / "universe.json").read_text())["search"] == u["search"]
    assert U.ensure(tmp_path, date(2026, 12, 10), cand, fetch)["refreshed"] == "2026-11-23" and len(calls) == 1   # < 30 days: no fetch
    assert U.ensure(tmp_path, date(2026, 12, 30), cand, fetch)["refreshed"] == "2026-12-30" and len(calls) == 2
    boom = lambda syms: (_ for _ in ()).throw(RuntimeError("network"))
    assert U.ensure(tmp_path, date(2027, 3, 1), cand, boom)["refreshed"] == "2026-12-30"       # keeps the old file
    assert U.ensure(tmp_path / "new", date(2027, 3, 1), cand, boom)["search"] == ["SPY", "QQQ"]  # nothing known: core only


def test_fetch_liquidity_uses_the_scanner_readers(monkeypatch):
    scan = U.scanner()
    def get(path):
        if "spark" in path:
            syms = path.split("symbols=")[1].split("&")[0].split(",")
            res = [{"symbol": s, "response": [{"meta": {"symbol": s, "regularMarketPrice": 3.0 if s == "CHEAP" else 50.0,
                                                         "regularMarketVolume": 1_000_000}, "timestamp": [1], "indicators": {"quote": [{"close": [1.0]}]}}]}
                   for s in syms]
            return json.dumps({"spark": {"result": res, "error": None}})
        sym = path.split("chart/")[1].split("?")[0]
        n = 260
        return json.dumps({"chart": {"result": [{"meta": {"symbol": sym}, "timestamp": list(range(n)),
                                                 "indicators": {"quote": [{"close": [100.0] * n, "volume": [1000 + i for i in range(n)]}]}}]}})
    monkeypatch.setattr(scan, "yahoo_get", get)
    out = U.fetch_liquidity(["AAA", "BBB", "CHEAP"], scan)
    assert set(out) == {"AAA", "BBB"}                                       # price floor from the scanner's spark stage
    assert out["AAA"]["sessions"] == 250 and out["AAA"]["median_dollar_volume"] == pytest.approx(100.0 * np.median([1000 + i for i in range(10, 260)]))


def test_run_names_rotate_and_keep_core():
    u = U.select(liq(), date(2026, 11, 23))
    a, b = U.run_names(u, 6, 1), U.run_names(u, 6, 2)
    assert a[:2] == ["SPY", "QQQ"] and len(a) == 8 and set(a) <= set(u["search"]) and a != b
    assert not set(a) & set(u["lockbox"])


# ---------------------------------------------------------------- scanner families: no lookahead
D0, K = 30, 200


@pytest.fixture(scope="module")
def cubes():
    return F.build(I.Days.from_arrays(*synth())), F.build(I.Days.from_arrays(*perturbed(D0, K)))


def _same(a, b):
    return np.array_equal(a, b, equal_nan=True)


@pytest.mark.parametrize("f", ["orb5", "orb15", "orb30", "consol_30", "rvol_30", "gap_fill", "hi20_dist", "z_20d"])
def test_intraday_families_do_not_peek(cubes, f):
    A, B = cubes
    j = F.FEATURES.index(f)
    assert _same(A[:D0, :, j], B[:D0, :, j])
    for i, m in enumerate(F.DECISIONS):
        if m <= K:
            assert _same(A[D0, i, j], B[D0, i, j]), f"{f} at decision {m} uses bars after it"
    assert np.isfinite(A[D0 - 5:, :, j]).any()


@pytest.mark.parametrize("f", ["run_10d", "range_5d", "rsi_14d"])
def test_daily_history_families_use_only_earlier_days(cubes, f):
    A, B = cubes
    j = F.FEATURES.index(f)
    assert _same(A[:D0 + 1, :, j], B[:D0 + 1, :, j])         # day D0 itself (even after the perturbation) is untouched
    assert not _same(A[D0 + 1:, :, j], B[D0 + 1:, :, j])      # the day after sees the changed history
    assert np.isfinite(A[D0, :, j]).any()


def test_family_values_match_their_definitions():
    D = I.Days.from_arrays(*synth())
    cube, d, i = F.build(D), 40, F.DECISIONS.index(90)
    c = D.C[d, 89]
    for name, k in (("orb5", 5), ("orb15", 15), ("orb30", 30)):
        hi, lo = D.H[d, :k].max(), D.L[d, :k].min()
        want = (c - hi) / hi if c > hi else (c - lo) / lo if c < lo else 0.0
        assert cube[d, i, F.FEATURES.index(name)] == pytest.approx(want)
    cl = D.close
    assert cube[d, i, F.FEATURES.index("run_10d")] == pytest.approx(cl[d - 6] / cl[d - 16] - 1)
    assert cube[d, i, F.FEATURES.index("range_5d")] == pytest.approx((cl[d - 5:d].max() - cl[d - 5:d].min()) / cl[d - 1])
    assert cube[d, i, F.FEATURES.index("hi20_dist")] == pytest.approx(c / cl[d - 20:d].max() - 1)
    z = (c - cl[d - 20:d].mean()) / cl[d - 20:d].std()
    assert cube[d, i, F.FEATURES.index("z_20d")] == pytest.approx(z)
    assert cube[d, i, F.FEATURES.index("consol_30")] == pytest.approx((D.H[d, 60:90].max() - D.L[d, 60:90].min()) / c)
    assert cube[d, i, F.FEATURES.index("rsi_14d")] == pytest.approx(U.scanner().rsi(list(cl[:d]), 14))   # the scanner's own RSI


def test_new_features_are_appended_and_old_hashes_hold():
    assert F.FEATURES[:11] == ["gap", "ret_open", "ret_prev_close", "ret_30", "vwap_dist", "range_pos", "noise_z",
                               "vol_ratio", "prev_day_ret", "prev_day_ibs", "rv_5d"] and len(NEW) == 11


# ---------------------------------------------------------------- 5-minute expansion
def test_five_minute_expansion_keeps_boundary_prices():
    idx = pd.date_range("2024-03-04 09:30", periods=78, freq="5min", tz=bars5.NY)
    rng = np.random.default_rng(0)
    c = 100 + np.cumsum(rng.normal(0, 0.1, 78))
    o = np.r_[100, c[:-1]]
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.05, "low": np.minimum(o, c) - 0.05, "close": c, "volume": 1000.0}, index=idx)
    D = I.Days.from_frame(bars5.expand(df))
    assert D.n == 1 and D.last[0] == 389
    assert np.allclose(D.P[0, 0:390:5][:78], o) and D.C[0, 4] == pytest.approx(c[0]) and D.C[0, 389] == pytest.approx(c[-1])
    assert D.V[0].sum() == pytest.approx(78000) and D.H[0, :5].max() == pytest.approx(df.high.iloc[0])


# ---------------------------------------------------------------- lockbox, trial counting across names
def long_market(seed):
    n = 1700
    dates = pd.bdate_range("2016-01-04", periods=n)
    d = synth(n, seed=seed)
    return P.Market("X", I.Days.from_arrays(dates, *d[1:]))


def test_lockbox_names_are_never_searched_and_trials_count_across_names(tmp_path):
    uni = {"search": ["SPY", "QQQ", "AAA"], "lockbox": ["LBX"], "ever_lockbox": ["LBX"], "ranked": []}
    S.register_markets(uni["search"] + uni["lockbox"])
    try:
        days = {s: I.Days.from_arrays(*synth(60, seed=i)) for i, s in enumerate(["SPY", "QQQ", "AAA", "LBX"])}
        rng = random.Random(3)
        specs = [S.random_spec(rng, market=m) for m in ("AAA", "LBX", "AAA", "LBX", "SPY") for _ in range(2)]

        class Fake:
            skipped = []
            def propose(self, k, markets):
                return list(specs)
        mem = run(rounds=1, per_round=10, quiet=True, mem_root=tmp_path, days=days, proposer=Fake(), universe=uni, spend=False)
        assert {t["spec"]["market"] for t in mem.trials} <= {"SPY", "QQQ", "AAA"}
        assert any(t["spec"]["market"] == "AAA" for t in mem.trials) and not any(t["spec"]["market"] == "LBX" for t in mem.trials)
        # the evolution proposer with the default rotation never proposes a lockbox name either
        mem2 = run(rounds=3, per_round=10, quiet=True, mem_root=tmp_path / "b", days=days, universe=uni, spend=False, seed=5)
        assert "LBX" not in {t["spec"]["market"] for t in mem2.trials} and "AAA" in {t["spec"]["market"] for t in mem2.trials}
        # multiple testing: every scored trial on every name deflates every idea
        sc = [t for t in mem2.trials if t.get("daily_sr") is not None]
        assert len(mem2.trial_sharpes("SPY")) == len(sc) and len({t["spec"]["market"] for t in sc}) > 1
    finally:
        S.register_markets([])


def test_lockbox_check_confirms_only_with_enough_positive_unseen_names():
    s = {**{"market": "X", "time": 60, "side": "long", "stop_bp": None, "exit": {"type": "hold", "n": 60}},
         "entry": [{"feature": "gap", "op": ">", "q": 20}]}
    mk = {f"L{i}": long_market(i) for i in range(4)}
    got = P.lockbox_check(lambda n: mk[n], s, list(mk))
    assert got["n_names"] == 4 and got["n_used"] == 4 and isinstance(got["ok"], bool)
    assert all(v["trades"] > 20 for v in got["names"].values())
    bad = P.lockbox_check(lambda n: (_ for _ in ()).throw(OSError("no data")), s, ["L0", "L1", "L2"])
    assert not bad["ok"] and bad["n_used"] == 0                            # unreadable lockbox never confirms
    assert P.lockbox_check(lambda n: None, s, [])["ok"]                     # no lockbox names defined: nothing to confirm
    one = P.lockbox_check(lambda n: mk[n], s, ["L0"])
    assert not one["ok"]                                                    # fewer than 3 names


def test_verdict_needs_lockbox_and_single_stock_uses_spy_buy_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(IMemory, "prior_window_pvalues", lambda self: [])
    ho = lambda **k: {"p_value": 0.001, "sharpe": 1.0, "sharpe_2x_cost": 0.8, "trades": 90, "lockbox": {"ok": True}, **k}
    base = {"market": "AAA", "time": 60, "side": "long", "stop_bp": None, "exit": {"type": "hold", "n": 60},
            "entry": [{"feature": "gap", "op": ">", "q": 50}]}
    S.register_markets(["AAA"])
    try:
        recs = [(base, ho(spy_bh_sharpe=0.5)), (base | {"time": 90}, ho(spy_bh_sharpe=0.9)),
                (base | {"time": 120}, ho(spy_bh_sharpe=0.5, lockbox={"ok": False})), (base | {"time": 150}, ho())]
        m = IMemory(tmp_path)
        for sp, h in recs:
            m.add({"hash": S.spec_hash(sp), "spec": sp, "status": "scored", "reward": 0.0, "holdout": h})
        m.band = 0.0                                                         # the noise band is irrelevant for single stocks
        v = {x["n"]: x for x in m.holdout_verdicts()}
        assert v[1]["pass"] and v[1]["bench"] == 0.5
        assert not v[2]["pass"] and not v[2]["beats_band"]                   # 0.8 does not beat SPY buy-and-hold 0.9
        assert not v[3]["pass"] and not v[3]["lockbox_ok"]                   # fails on the unseen names
        assert not v[4]["pass"]                                              # no SPY benchmark recorded: fail closed
    finally:
        S.register_markets([])
