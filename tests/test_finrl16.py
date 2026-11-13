"""Study 16 (FINRL_PLAN_16.md): analysis maths (pure) and, when finrl is installed (.venv-finrl), the FinRL env wiring."""
import numpy as np
import pandas as pd
import pytest

import importlib.util

import finrl16 as F
import finrl_data as FD


def _need_finrl():
    """finrl is only installed in .venv-finrl; importorskip would import its broken __init__, so look the spec up."""
    import sys
    if "finrl" not in sys.modules and importlib.util.find_spec("finrl") is None:
        pytest.skip("finrl not installed (run in .venv-finrl)")


def test_sharpe_dd_total():
    r = np.array([0.01, -0.02, 0.03, 0.0])
    assert F.sharpe(r) == pytest.approx(r.mean() / r.std(ddof=1) * np.sqrt(252))
    assert F.total(r) == pytest.approx(1.01 * 0.98 * 1.03 - 1)
    assert F.max_dd(r) == pytest.approx(0.98 - 1)
    assert F.sharpe(np.zeros(5)) == 0.0


def test_run_returns_drops_first_date():
    d, r = F.run_returns({"dates": ["a", "b", "c"], "value": [100.0, 110.0, 99.0]})
    assert d == ["b", "c"] and r == pytest.approx([0.1, -0.1])


def test_permutation_null_equal_weights_is_uninformative_and_concentration_is_detected():
    rng = np.random.default_rng(0)
    T, N = 300, 10
    R = rng.normal(0, 0.01, (T, N))
    W = np.hstack([np.zeros((T, 1)), np.full((T, N), 1 / N)])
    r = (W[:, 1:] * R).sum(1)
    p, _ = F.permutation_null(r, W, R, n_sims=100)
    assert p == pytest.approx(1.0)                         # permuting equal weights changes nothing
    N = 60
    R2 = rng.normal(0, 0.01, (T, N))
    R2[:, 0] += 0.003                                      # stock 0 is genuinely better (1 of 60 names: p ~ 1/60)
    W2 = np.hstack([np.zeros((T, 1)), np.zeros((T, N))])
    W2[:, 1] = 1.0                                         # the agent holds only stock 0
    r2 = (W2[:, 1:] * R2).sum(1)
    p2, null = F.permutation_null(r2, W2, R2, n_sims=400)
    assert p2 < 0.05 and np.median(null) < F.sharpe(r2)


def test_permutation_null_keeps_cost_and_exposure():
    rng = np.random.default_rng(1)
    T, N = 50, 4
    R = rng.normal(0, 0.01, (T, N))
    W = np.hstack([np.full((T, 1), 0.5), np.full((T, N), 0.125)])
    r = (W[:, 1:] * R).sum(1) - 0.001                      # a cost-like drag inside the net series
    _, null = F.permutation_null(r, W, R, n_sims=20)
    assert np.allclose(null, F.sharpe(r))                  # equal weights: simulated net == actual net, drag preserved


def test_lag1_constant_weights_and_cost():
    T, N = 6, 2
    R = np.full((T, N), 0.01)
    W = np.hstack([np.zeros((T, 1)), np.full((T, N), 0.5)])
    r = F.lag1_returns(W, R, 0.001)
    assert r[0] == pytest.approx(0.0)                      # day 0: still cash
    assert r[1] == pytest.approx(0.01 - 0.001 * 1.0)       # first day invested: pays the cost on 100% weight change
    assert r[2] == pytest.approx(0.01)


def test_turnover():
    W = np.array([[0, .5, .5], [0, .5, .5], [0, 1.0, 0.0], [0, 1.0, 0.0]])
    assert F.turnover(W) == pytest.approx(((0 + 1.0 + 0) / 3) / 2)


def test_benchmarks_equal_weight_buy_and_hold():
    dates = ["2022-01-03", "2022-01-04", "2022-01-05"]
    rows = []
    for t, p in {"AXP": [100, 110, 121], "AMGN": [100, 100, 100], "SPY": [100, 101, 102], "DIA": [100, 100, 101]}.items():
        for d, c in zip(dates, p):
            rows.append({"date": d, "tic": t, "open": c, "high": c, "low": c, "close": c, "volume": 1})
    out = F.benchmark_returns(dates, pd.DataFrame(rows))
    assert out["SPY"] == pytest.approx([0.01, 1 / 101])
    # two equal-dollar positions: value = mean of (1.0, 1.1, 1.21) and (1,1,1) -> 1, 1.05, 1.105 (other tickers absent in the toy frame)
    assert out["EW28"] == pytest.approx([0.05, 1.105 / 1.05 - 1])


def test_select_algo_needs_five_seeds_and_takes_best_median_validation():
    def run(sh):
        v = [100.0]
        for i in range(60):
            v.append(v[-1] * (1 + 0.001 * sh + (0.01 if i % 2 else -0.01)))
        return {"valid": {"1x": {"dates": [str(i) for i in range(len(v))], "value": v}}}
    runs = {("A2C", s): run(1.0) for s in F.SEEDS} | {("PPO", s): run(3.0) for s in F.SEEDS} | {("SAC", 0): run(9.0)}
    sel, med = F.select_algo(runs)
    assert sel == "PPO" and "SAC" not in med


def test_periods_do_not_overlap_and_universe():
    import finrl_env
    assert finrl_env.TRAIN[1] < finrl_env.VALID[0] and finrl_env.VALID[1] < finrl_env.TEST_START
    assert len(FD.TICKERS) == 28 and "WBA" not in FD.TICKERS and "DOW" not in FD.TICKERS
    df = pd.DataFrame({"date": ["2022-01-03", "2022-01-04", "2022-01-05", "2022-01-06"], "tic": "A", "x": 1})
    s = finrl_env.span(df, "2022-01-04", "2022-01-05")
    assert list(s["date"]) == ["2022-01-04", "2022-01-05"] and list(s.index) == [0, 1]   # inclusive end, day index from 0


def test_last_complete_session_excludes_today():
    df = pd.DataFrame({"date": ["2026-11-11", "2026-11-12", "2026-11-13"]})
    assert FD.last_complete_session(df, pd.Timestamp("2026-11-13").date()) == pd.Timestamp("2026-11-12")


# ---------------------------------------------------------------- FinRL env (needs .venv-finrl)
def toy_frame(T=12, N=3, seed=0):
    rng = np.random.default_rng(seed)
    _need_finrl()
    import finrl_env
    cfg = finrl_env.load_finrl()
    dates = [str(d.date()) for d in pd.bdate_range("2022-01-03", periods=T)]
    rows = []
    for n in range(N):
        p = 50 * np.cumprod(1 + rng.normal(0, 0.01, T))
        for i, d in enumerate(dates):
            r = {"date": d, "tic": f"T{n}", "open": p[i], "high": p[i], "low": p[i], "close": p[i], "volume": 1.0, "turbulence": 5.0}
            r.update({k: 0.0 for k in cfg.INDICATORS})
            rows.append(r)
    df = pd.DataFrame(rows).sort_values(["date", "tic"]).reset_index(drop=True)
    df.index = df["date"].factorize()[0]
    return df


def test_env_buy_cost_is_cost_pct_of_traded_value_and_categorical_is_a_noop():
    _need_finrl()
    import finrl_env as fe
    df = toy_frame()
    env = fe.make_env(df, 0.001)
    env.reset()
    px = np.array(env.state[1:4])
    env.step(np.array([1.0, 1.0, 1.0]))                    # buy hmax = 100 shares of each (cash is ample)
    assert env.cost == pytest.approx(0.001 * 100 * px.sum(), rel=1e-6)
    assert list(env.state[4:7]) == [100, 100, 100]
    # same actions with a plain (non-categorical) tic column give identical accounts
    from finrl.meta.env_stock_trading.env_stocktrading import StockTradingEnv  # noqa: F401
    a, b = fe.make_env(df, 0.001), fe.make_env(df, 0.001)
    b2 = fe.make_env(df.assign(tic=df["tic"].astype(str)), 0.001)
    a.reset(); b2.reset()
    rng = np.random.default_rng(3)
    for _ in range(8):
        act = rng.uniform(-1, 1, 3)
        a.step(act); b2.step(act)
    assert a.asset_memory == pytest.approx(b2.asset_memory)


def test_env_is_long_only_and_turbulence_sells_everything():
    _need_finrl()
    import finrl_env as fe
    df = toy_frame()
    env = fe.make_env(df, 0.001, turbulence_threshold=1.0)   # toy turbulence is 5 > 1 on every day
    env.reset()
    env.step(np.array([1.0, 1.0, 1.0]))                      # FinRL quirk: turbulence is 0 on the first step (updated after it)
    assert list(env.state[4:7]) == [100, 100, 100]
    env.step(np.array([1.0, 1.0, 1.0]))                      # now 5 >= 1: the env overrides the action and sells everything
    assert list(env.state[4:7]) == [0, 0, 0]
    env2 = fe.make_env(df, 0.001)
    env2.reset()
    env2.step(np.array([-1.0, -1.0, -1.0]))                  # selling with no shares does nothing (no shorting)
    assert list(env2.state[4:7]) == [0, 0, 0] and env2.state[0] == pytest.approx(1_000_000)
