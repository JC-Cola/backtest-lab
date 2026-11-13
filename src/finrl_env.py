"""Study 16 (FINRL_PLAN_16.md) training side: FinRL's own StockTradingEnv + FeatureEngineer + stable-baselines3 agents.
Runs in .venv-finrl (finrl 0.3.7, gymnasium, SB3, torch). The analysis side (src/finrl16.py) needs only numpy/pandas.

    .venv-finrl/bin/python src/finrl_env.py features          # build the FinRL feature frame
    .venv-finrl/bin/python src/finrl_env.py run [--jobs 6] [--algos A2C,PPO,SAC] [--seeds 0,1,2,3,4]

`finrl/__init__.py` imports the Alpaca paper-trading module (needs `alpaca_trade_api`), so the package is registered as a stub
that points at the installed directory; the shipped files (env_stocktrading.py, preprocessors.py, config.py) run unmodified.
Each job writes data/finrl16/run_<ALGO>_<SEED>.json (validation + test at 1x/2x costs, daily returns and weights).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "data" / "finrl16"
FEATURES = ROOT / "data" / "finrl16_features.csv"

TRAIN = ("2010-01-01", "2019-12-31")
VALID = ("2020-01-01", "2021-12-31")
TEST_START = "2022-01-01"
COSTS = {"1x": 0.001, "2x": 0.002}
STEPS = {"A2C": 50_000, "PPO": 200_000, "SAC": 70_000}
HMAX, INITIAL, REWARD_SCALING, TURB_Q = 100, 1_000_000, 1e-4, 0.90


def load_finrl():
    """Register `finrl` without running its __init__ (see module docstring). Returns the finrl.config module."""
    if "finrl" not in sys.modules or not hasattr(sys.modules["finrl"], "__path__"):
        spec = importlib.util.find_spec("finrl")
        if spec is None:
            raise RuntimeError("finrl is not installed (use .venv-finrl)")
        stub = types.ModuleType("finrl")
        stub.__path__ = list(spec.submodule_search_locations)
        sys.modules["finrl"] = stub
    import finrl.config as cfg
    return cfg


def build_features(path: Path = FEATURES) -> pd.DataFrame:
    """FinRL FeatureEngineer on the cached Dow frame: default INDICATORS + turbulence index; saved to data/finrl16_features.csv."""
    import finrl_data
    cfg = load_finrl()
    from finrl.meta.preprocessor.preprocessors import FeatureEngineer
    stocks, _ = finrl_data.split_universe(finrl_data.load())
    fe = FeatureEngineer(use_technical_indicator=True, tech_indicator_list=cfg.INDICATORS, use_vix=False,
                         use_turbulence=True, user_defined_feature=False)
    df = fe.preprocess_data(stocks)
    df = df.sort_values(["date", "tic"]).reset_index(drop=True)
    df.to_csv(path, index=False)
    return df


def load_features() -> pd.DataFrame:
    return pd.read_csv(FEATURES) if FEATURES.exists() else build_features()


def data_split(df, start, end):
    """FinRL's data_split (start <= date < end) with the day index the env needs. `end` is exclusive, as in FinRL."""
    d = df[(df["date"] >= start) & (df["date"] < end)].sort_values(["date", "tic"]).reset_index(drop=True)
    d.index = d["date"].factorize()[0]
    return d


def span(df, a, b):
    """Rows with a <= date <= b (inclusive end), env-ready."""
    d = df[(df["date"] >= a) & (df["date"] <= b)].sort_values(["date", "tic"]).reset_index(drop=True)
    d.index = d["date"].factorize()[0]
    return d


def make_env(df, cost, turbulence_threshold=None):
    cfg = load_finrl()
    from finrl.meta.env_stock_trading.env_stocktrading import StockTradingEnv
    n = df["tic"].nunique()
    ind = list(cfg.INDICATORS)
    # speed only: the env calls df.tic.unique() on every step (8 ms on 70k rows); a categorical column makes it 3x faster and
    # leaves every number unchanged (tests/test_finrl16.py compares the two on the same actions)
    df = df.assign(tic=df["tic"].astype("category"))
    return StockTradingEnv(df=df, stock_dim=n, hmax=HMAX, initial_amount=INITIAL, num_stock_shares=[0] * n,
                           buy_cost_pct=[cost] * n, sell_cost_pct=[cost] * n, reward_scaling=REWARD_SCALING,
                           state_space=1 + 2 * n + len(ind) * n, action_space=n, tech_indicator_list=ind,
                           turbulence_threshold=turbulence_threshold, print_verbosity=10 ** 9)


def rollout(model, df, cost, thr):
    """Deterministic run of a trained policy over df. Returns dates, account value, post-trade weights [cash, stocks...] per day
    (the weights held from that close to the next), the stocks' day-over-day returns, tics."""
    env = make_env(df, cost, thr)
    n = env.stock_dim
    obs, _ = env.reset()
    tics = list(df.loc[0, "tic"])
    px = df.pivot(index="date", columns="tic", values="close")[tics]
    W, done = [], False
    while not done:
        price = np.array(env.state[1:1 + n], float)
        a, _ = model.predict(np.array(obs, np.float32), deterministic=True)
        obs, _, done, _, _ = env.step(a)
        if done:
            break
        cash = float(env.state[0])
        sh = np.array(env.state[1 + n:1 + 2 * n], float)
        v = cash + float((sh * price).sum())
        W.append(np.r_[cash, sh * price] / v)
    dates = [str(d) for d in env.date_memory]
    return {"dates": dates, "value": [float(x) for x in env.asset_memory], "weights": np.array(W).round(6).tolist(),
            "tics": tics, "stock_ret": px.pct_change().iloc[1:].values.round(8).tolist(),
            "trades": int(env.trades), "cost": float(env.cost)}


def make_model(algo, env, seed):
    cfg = load_finrl()
    from stable_baselines3 import A2C, PPO, SAC
    from stable_baselines3.common.vec_env import DummyVecEnv
    params = {"A2C": cfg.A2C_PARAMS, "PPO": cfg.PPO_PARAMS, "SAC": cfg.SAC_PARAMS}[algo]
    cls = {"A2C": A2C, "PPO": PPO, "SAC": SAC}[algo]
    return cls("MlpPolicy", DummyVecEnv([lambda: env]), seed=seed, verbose=0, device="cpu", **params)


def run_job(algo, seed, steps=None, force=False):
    out = RUNS / f"run_{algo}_{seed}.json"
    if out.exists() and not force:
        return str(out)
    import torch
    torch.set_num_threads(1)
    RUNS.mkdir(parents=True, exist_ok=True)
    df = load_features()
    tr, va = span(df, *TRAIN), span(df, *VALID)
    thr = float(np.quantile(tr.drop_duplicates("date")["turbulence"].values, TURB_Q))
    model = make_model(algo, make_env(tr, COSTS["1x"], None), seed)
    model.learn(total_timesteps=steps or STEPS[algo])
    res = {"algo": algo, "seed": seed, "steps": steps or STEPS[algo], "turbulence_threshold": thr,
           "valid": {"1x": rollout(model, va, COSTS["1x"], thr)}}
    last = df["date"].max()
    te = span(df, TEST_START, last)
    res["test"] = {k: rollout(model, te, c, thr) for k, c in COSTS.items()}
    out.write_text(json.dumps(res))
    return str(out)


def _job(a):
    return run_job(*a)


if __name__ == "__main__":
    import argparse
    import multiprocessing as mp
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["features", "run"])
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--algos", default="A2C,PPO,SAC")
    ap.add_argument("--seeds", default="0,1,2,3,4")
    ap.add_argument("--steps", type=int, default=None, help="override steps (smoke test only)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.cmd == "features":
        d = build_features()
        print(d.shape, d["date"].min(), d["date"].max())
    else:
        build = load_features()
        todo = [(al, int(s), a.steps, a.force) for al in a.algos.split(",") for s in a.seeds.split(",")]
        with mp.get_context("spawn").Pool(a.jobs) as p:
            for r in p.imap_unordered(_job, todo):
                print("done", r, flush=True)
