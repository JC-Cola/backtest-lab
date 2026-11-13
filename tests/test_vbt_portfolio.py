"""Target-weight vectorbt helper (src/vbt_portfolio.py) on synthetic weights."""
import json

import numpy as np
import pandas as pd
import pytest

import vbt_portfolio as P


def book(n=120, seed=0, cols="ABCD"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    px = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (n, len(cols))), 0)), index=idx, columns=list(cols))
    return idx, px, rng


@pytest.mark.parametrize("lag", [0, 1])
def test_long_only_matches_numpy_reference(lag):
    idx, px, rng = book()
    w = pd.DataFrame(rng.dirichlet(np.ones(4), len(idx)) * 0.9, index=idx, columns=px.columns)
    _, r = P.run_weights(w, px, lag=lag)
    ref = P.reference_returns(w, px, lag=lag)
    assert P.compare(r, ref)["max_abs_daily_diff"] < 1e-12


def test_long_short_matches_reference():
    idx, px, rng = book(seed=1)
    w = pd.DataFrame(rng.choice([-0.2, 0.0, 0.2], (len(idx), 4)), index=idx, columns=px.columns)
    _, r = P.run_weights(w, px, lag=0)
    assert P.compare(r, P.reference_returns(w, px, lag=0))["max_abs_daily_diff"] < 1e-12


def test_known_single_asset_return_and_cost():
    idx = pd.bdate_range("2020-01-01", periods=3)
    px = pd.DataFrame({"A": [100.0, 110.0, 99.0]}, index=idx)
    w = pd.DataFrame({"A": [0.5, 0.5, 0.0]}, index=idx)
    _, r = P.run_weights(w, px, fee=1e-4, lag=0, rebalance="on_change")
    # day0: buy 50% (fee 0.5e-4 of equity); day1: +10% on half = +5%; day2: -10% on the 55% held, sell it (fee)
    e0 = 1 - 0.5e-4
    e1 = e0 * (1 + 0.5 * 0.10)
    assert r.iloc[0] == pytest.approx(-0.5e-4, abs=1e-9)
    assert r.iloc[1] == pytest.approx(e1 / e0 - 1, abs=2e-4)
    assert r.iloc[2] < 0


def test_lag_shifts_execution():
    idx, px, rng = book(n=30)
    w = pd.DataFrame(0.0, index=idx, columns=px.columns)
    w.iloc[5, 0] = 1.0
    _, r0 = P.run_weights(w, px, lag=0)
    _, r1 = P.run_weights(w, px, lag=1)
    assert r0.iloc[:5].abs().sum() == 0 and abs(r0.iloc[5]) > 0   # fee paid on the date the target is filled
    assert r1.iloc[:6].abs().sum() == 0 and abs(r1.iloc[6]) > 0


def test_gross_over_one_refused():
    idx, px, _ = book(n=10)
    w = pd.DataFrame(0.6, index=idx, columns=px.columns)
    with pytest.raises(ValueError):
        P.run_weights(w, px)


def test_compare_reports_diff():
    idx = pd.bdate_range("2020-01-01", periods=3)
    a = pd.Series([0.01, 0.0, -0.01], idx)
    b = a + [0.0, 0.001, 0.0]
    c = P.compare(a, b)
    assert c["max_abs_daily_diff"] == pytest.approx(0.001) and c["worst_day"] == str(idx[1].date())


def test_crosscheck_qlib_missing_and_no_weights(tmp_path):
    assert P.crosscheck_qlib(tmp_path / "nope.json")["status"] == "missing"
    f = tmp_path / "q.json"
    f.write_text(json.dumps({"sharpe": 1.0}))
    assert P.crosscheck_qlib(f)["status"] == "no_weights_in_file"


def test_crosscheck_qlib_with_weights(tmp_path):
    idx, px, rng = book(n=40)
    w = pd.DataFrame(rng.dirichlet(np.ones(4), len(idx)) * 0.9, index=idx, columns=px.columns)
    ref = P.reference_returns(w, px, lag=1)
    f = tmp_path / "q.json"
    f.write_text(json.dumps({"weights": {str(d.date()): row.to_dict() for d, row in w.iterrows()},
                             "daily_returns": {str(d.date()): float(v) for d, v in ref.items()}}))
    out = P.crosscheck_qlib(f, prices=px)
    assert out["status"] == "ok" and out["vs_system"]["max_abs_daily_diff"] < 1e-10
