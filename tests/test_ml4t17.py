"""Study 17 (ML4T_PLAN_17.md): purged CV, meta-label construction, IC / quantile / decay maths, rating scoring."""
import math

import numpy as np
import pandas as pd
import pytest

import intraday as I
import ml4t_cv as CV
import ml4t_ic as IC
import ml4t_meta as M
import ml4t17 as R


# ---------------------------------------------------------------- purged CV
def test_purged_blocks_partition_and_no_leak():
    pos = np.repeat(np.arange(200), 3)               # 3 rows per session
    sp = CV.purged_block_splits(pos, n_blocks=5, purge=5)
    assert len(sp) == 5
    tests = np.concatenate([te for _, te in sp])
    assert sorted(tests) == list(range(len(pos)))    # every row tested exactly once
    for tr, te in sp:
        lo, hi = pos[te].min(), pos[te].max()
        assert set(tr).isdisjoint(te)
        assert not np.any((pos[tr] >= lo - 5) & (pos[tr] <= hi + 5))   # nothing inside the purge / embargo band
        assert np.all((pos[tr] < lo - 5) | (pos[tr] > hi + 5))
        # the band is exactly what was removed: rows outside the band and outside the block are all trained on
        assert len(tr) == np.sum((pos < lo - 5) | (pos > hi + 5))


def test_oof_predict_every_row_once_and_uses_only_allowed_train():
    pos = np.repeat(np.arange(100), 2)
    X = pos.reshape(-1, 1).astype(float)
    y = (pos % 2).astype(int)
    seen = []

    def fp(Xtr, ytr, Xte):
        seen.append((Xtr.min(), Xtr.max(), Xte.min(), Xte.max()))
        return np.full(len(Xte), Xte.mean())
    out = CV.oof_predict(X, y, pos, fp, n_blocks=4, purge=3)
    assert not np.isnan(out).any()
    for tr_lo, tr_hi, te_lo, te_hi in seen:
        assert not (te_lo - 3 <= tr_hi <= te_hi + 3 and te_lo - 3 <= tr_lo <= te_hi + 3)


# ---------------------------------------------------------------- meta-labels
def synth_days(n=40, seed=0, perturb_from=None):
    rng = np.random.default_rng(seed)
    price = 100 * np.exp(np.cumsum(rng.normal(0, 3e-4, (n, 390)), axis=1) + np.cumsum(rng.normal(0, 2e-3, n))[:, None])
    O = price.copy()
    C = price * (1 + rng.normal(0, 1e-4, price.shape))
    H = np.maximum(O, C) * 1.0002
    L = np.minimum(O, C) * 0.9998
    V = rng.integers(100, 1000, (n, 390)).astype(float)
    return O, H, L, C, V


def make_D(arrs, n=40):
    O, H, L, C, V = arrs
    dates = pd.bdate_range("2021-01-04", periods=n)
    return I.Days.from_arrays(dates, O, H, L, C, V, np.full(n, 389))


def one_trade(d, em, xm, di):
    return {"day": np.array([d]), "entry_m": np.array([em]), "exit_m": np.array([xm]), "dir": np.array([float(di)]), "ret": np.array([0.0])}


def vix_series():
    return pd.Series(np.linspace(15, 25, 80), index=pd.bdate_range("2020-12-01", periods=80))


def test_features_use_only_pre_entry_data_and_label_only_post_entry():
    n, d, em, xm, di = 40, 30, 150, 210, 1.0
    A = synth_days(n)
    D1 = make_D(A, n)
    t = one_trade(d, em, xm, di)
    f1 = M.trade_table(D1, vix_series(), lookback=14, trades=t).iloc[0]
    # perturb EVERYTHING after the decision bar (bars >= em on day d, and all later days)
    O, H, L, C, V = [a.copy() for a in A]
    for a in (O, H, L, C, V):
        a[d, em:] *= 1.07
        a[d + 1:] *= 0.9
    D2 = make_D((O, H, L, C, V), n)
    f2 = M.trade_table(D2, vix_series(), lookback=14, trades=t).iloc[0]
    for k in M.FEATURES:
        assert f1[k] == pytest.approx(f2[k]), k            # features unchanged by the future
    # perturb only bars BEFORE the entry fill (bar < em of day d, earlier days): the label (entry price onward) is unchanged
    O, H, L, C, V = [a.copy() for a in A]
    O[d, :em - 1] *= 1.05                                   # the open of bar em is the entry fill; earlier bars are irrelevant to it
    D3 = make_D((O, H, L, C, V), n)
    # the entry fill uses P[d, em] = O[d, em]; the exit P[d, xm] = O[d, xm]; both untouched
    f3 = M.trade_table(D3, vix_series(), lookback=14, trades=t).iloc[0]
    assert (f1["y"], f1["y_close"]) == (f3["y"], f3["y_close"])
    # and the label does react to post-entry data
    O, H, L, C, V = [a.copy() for a in A]
    O[d, xm] *= 0.5
    f4 = M.trade_table(make_D((O, H, L, C, V), n), vix_series(), lookback=14, trades=t).iloc[0]
    assert f4["y"] == 0 and f1["y"] in (0, 1)


def test_label_definition_long_and_short():
    n = 40
    O, H, L, C, V = synth_days(n)
    O[30, 100] = 100.0
    O[30, 160] = 101.0
    D = make_D((O, H, L, C, V), n)
    y, _ = M.trade_label(D, 30, 100, 160, 1.0)
    assert y == 1                                            # +1% long, costs 2 bp
    y, _ = M.trade_label(D, 30, 100, 160, -1.0)
    assert y == 0
    O[30, 160] = 100.01
    y, _ = M.trade_label(make_D((O, H, L, C, V), n), 30, 100, 160, 1.0)
    assert y == 0                                            # +1 bp does not cover 2 bp of round-trip costs


def test_vix_prior_close_is_strictly_before():
    v = pd.Series([10.0, 20.0, 30.0], index=pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06"]))
    out = M.vix_prior_close(v, pd.to_datetime(["2021-01-05", "2021-01-06", "2021-01-04"]))
    assert out[0] == 10 and out[1] == 20 and np.isnan(out[2])


def test_veto_coin_flip_no_veto_is_p1():
    n = 30
    D = make_D(synth_days(n), n)
    tab = pd.DataFrame({"day": np.arange(n), "ret": np.random.default_rng(0).normal(0, 1e-3, n)})
    p, _ = R.veto_coin_flip(D, tab, np.ones(n, bool), slice(0, n), n_sims=50)
    assert p == 1.0


def test_daily_from_mask_costs():
    n = 10
    D = make_D(synth_days(n), n)
    tab = pd.DataFrame({"day": [1, 1, 3], "ret": [0.01, 0.02, -0.01]})
    r1 = M.daily_from_mask(D, tab, np.array([True, False, True]), slice(0, n), 1)
    r2 = M.daily_from_mask(D, tab, np.array([True, False, True]), slice(0, n), 2)
    assert r1[1] == pytest.approx(0.01) and r1[3] == pytest.approx(-0.01) and r1[2] == 0
    assert r2[1] == pytest.approx(0.01 - 2e-4)


# ---------------------------------------------------------------- IC maths
def panel(T=60, N=40, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=T)
    return idx, [f"S{i}" for i in range(N)], rng


def test_ic_perfect_random_and_negative():
    idx, cols, rng = panel()
    fwd = pd.DataFrame(rng.normal(0, 0.01, (len(idx), len(cols))), index=idx, columns=cols)
    assert IC.daily_ic(fwd * 3 + 0.001, fwd).mean() == pytest.approx(1.0)
    assert IC.daily_ic(-fwd, fwd).mean() == pytest.approx(-1.0)
    noise = pd.DataFrame(rng.normal(size=fwd.shape), index=idx, columns=cols)
    assert abs(IC.daily_ic(noise, fwd).mean()) < 0.06


def test_quantiles_turnover_and_decay():
    idx, cols, rng = panel(T=120)
    ret1 = pd.DataFrame(rng.normal(0, 0.01, (len(idx), len(cols))), index=idx, columns=cols)
    close = 100 * (1 + ret1).cumprod()
    fwd1 = close.pct_change().shift(-1)
    scores = fwd1.fillna(0.0)                                 # a perfect 1-day-ahead signal
    q = IC.quantile_returns(scores, fwd1, 5)
    v = [q["mean_fwd_by_quantile"][i] for i in range(1, 6)]
    assert v == sorted(v) and q["top_minus_bottom"] > 0 and q["monotonic_share"] == 1.0
    static = pd.DataFrame([np.arange(len(cols))] * len(idx), index=idx, columns=cols).astype(float)
    assert IC.top_quantile_turnover(static, 5) == pytest.approx(0.0)
    rnd = pd.DataFrame(rng.normal(size=static.shape), index=idx, columns=cols)
    assert 0.65 < IC.top_quantile_turnover(rnd, 5) < 0.95      # random lists replace ~80% of the top quintile
    dec = IC.ic_decay(scores, close, lags=range(1, 4))
    assert dec[1] > 0.99 and abs(dec[2]) < 0.1 and abs(dec[3]) < 0.1
    rep = IC.factor_report(scores, close)
    assert rep["ic"]["mean_ic"] > 0.99 and rep["ic"]["days"] > 100


def test_required_n_and_scores():
    assert IC.required_n(0.1) == pytest.approx(((1.6449 + 0.8416) / math.atanh(0.1)) ** 2 + 3, rel=1e-3)
    assert IC.required_n(0.0) == float("inf")
    assert IC.rating_score("Overweight") == 1 and IC.rating_score("sell") == -2 and IC.rating_score(" Hold ") == 0
    assert IC.binom_p_one_sided(5, 5) == pytest.approx(1 / 32)


def test_forward_returns_next_open_and_unrealised():
    idx = pd.bdate_range("2026-01-05", periods=6)
    px = pd.DataFrame({"Open": [10, 11, 12, 13, 14, 15.0], "Close": [10.5, 11.5, 12.5, 13.5, 14.5, 15.5]}, index=idx)
    d = idx[1]
    assert IC.forward_returns(px, "X", d, 1) == pytest.approx(12.5 / 12 - 1)           # next open (12) -> close of day+1 (12.5)
    assert IC.forward_returns(px, "X", d, 3) == pytest.approx(14.5 / 12 - 1)
    assert IC.forward_returns(px, "X", d, 1, "close_close") == pytest.approx(12.5 / 11.5 - 1)
    assert math.isnan(IC.forward_returns(px, "X", idx[5], 1))


def test_rating_ic_table_runs_and_flags_contaminated():
    idx = pd.bdate_range("2026-01-05", periods=60)
    rng = np.random.default_rng(1)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.01, 60))
    px = pd.DataFrame({"Open": c, "Close": c * 1.001}, index=idx)
    dec = [{"date": str(idx[i].date()), "symbol": "A", "rating": r, "clean": i > 20}
           for i, r in zip(range(5, 40, 3), ["Buy", "Sell", "Hold", "Overweight", "Underweight"] * 3)]
    out = IC.rating_ic_table(dec, lambda s: px, n_perm=200)
    assert out["all"]["next_open"]["1"]["n"] == len(dec)
    assert out["clean"]["next_open"]["1"]["n"] + out["possibly_contaminated"]["next_open"]["1"]["n"] == len(dec)
    assert "spearman_ic" in out["all"]["next_open"]["5"]
