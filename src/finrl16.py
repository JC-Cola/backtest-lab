"""Study 16 analysis (FINRL_PLAN_16.md): turns the per-job outputs of src/finrl_env.py (data/finrl16/run_<ALGO>_<SEED>.json) into
results/finrl16.json. Needs only numpy/pandas (+ stats.py), so it runs in .venv as well as .venv-finrl.

    .venv/bin/python src/finrl16.py
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import finrl_data
from stats import bh_qvalues, deflated_sharpe

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "data" / "finrl16"
OUT = ROOT / "results" / "finrl16.json"
ALGOS = ("A2C", "PPO", "SAC")
SEEDS = (0, 1, 2, 3, 4)
COST = {"1x": 0.001, "2x": 0.002}
N_SIMS = 2000
SEED = 20261113


# ------------------------------------------------------------------ metrics
def sharpe(r) -> float:
    r = np.asarray(r, float)
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return float(r.mean() / sd * math.sqrt(252)) if sd > 1e-12 else 0.0


def max_dd(r) -> float:
    eq = np.cumprod(1 + np.asarray(r, float))
    return float((eq / np.maximum.accumulate(np.r_[1.0, eq])[1:] - 1).min())


def total(r) -> float:
    return float(np.prod(1 + np.asarray(r, float)) - 1)


def stats_of(r) -> dict:
    return {"sharpe": sharpe(r), "total_return": total(r), "max_drawdown": max_dd(r), "days": int(len(r))}


def run_returns(run) -> tuple[list, np.ndarray]:
    """(dates of each return, daily account-value returns) of one rollout; the first date is the all-cash start."""
    v = np.asarray(run["value"], float)
    return run["dates"][1:], v[1:] / v[:-1] - 1


# ------------------------------------------------------------------ benchmarks
def benchmark_returns(dates, df=None):
    """Buy-and-hold returns on `dates[1:]` for SPY, DIA and the equal-weight (initial equal dollars, no rebalance, no costs)
    portfolio of the 28 stocks, all bought at the close of dates[0]."""
    df = finrl_data.load() if df is None else df
    stocks, bench = finrl_data.split_universe(df)
    px = stocks.pivot(index="date", columns="tic", values="close").loc[dates]
    out = {}
    for b in finrl_data.BENCH:
        out[b] = (bench[b].loc[dates].pct_change().iloc[1:]).values
    ew = (px / px.iloc[0]).mean(axis=1)
    out["EW28"] = ew.pct_change().iloc[1:].values
    return out


# ------------------------------------------------------------------ coin-flip: random names, same exposure / weights / cost
def permutation_null(r, W, R, n_sims=N_SIMS, seed=SEED):
    """r: the run's net daily returns (T). W: (T, N+1) weights held over each day (cash first). R: (T, N) stock returns that day.
    Each simulation relabels which stock gets which weight with one fixed random permutation of the N names for the whole
    path, so exposure, concentration, turnover and hence cost are unchanged; net_sim = net + gross_sim - gross_actual.
    Returns the p-value of the actual Sharpe and the null Sharpes."""
    r, W, R = np.asarray(r, float), np.asarray(W, float), np.asarray(R, float)
    Ws = W[:, 1:]
    gross = (Ws * R).sum(1)
    rng = np.random.default_rng(seed)
    vals = np.empty(n_sims)
    for i in range(n_sims):
        pi = rng.permutation(R.shape[1])
        vals[i] = sharpe(r + (Ws * R[:, pi]).sum(1) - gross)
    act = sharpe(r)
    return (1 + int((vals >= act - 1e-12).sum())) / (n_sims + 1), vals


# ------------------------------------------------------------------ lag-1 stress: the same weights, executed one day late
def lag1_returns(W, R, cost):
    """Hold yesterday's end-of-day weights (cash on day 0) and pay `cost` per unit of weight changed (both sides)."""
    Ws = np.asarray(W, float)[:, 1:]
    lag = np.vstack([np.zeros((1, Ws.shape[1])), Ws[:-1]])
    prev = np.vstack([np.zeros((1, Ws.shape[1])), lag[:-1]])
    turn = np.abs(lag - prev).sum(1)
    return (lag * np.asarray(R, float)).sum(1) - cost * turn


def turnover(W) -> float:
    """Mean daily one-sided traded fraction of the book (sum |dw| / 2)."""
    Ws = np.asarray(W, float)[:, 1:]
    return float(np.abs(np.diff(Ws, axis=0)).sum(1).mean() / 2)


# ------------------------------------------------------------------ assembling
def load_runs(runs_dir: Path = RUNS):
    out = {}
    for a in ALGOS:
        for s in SEEDS:
            p = runs_dir / f"run_{a}_{s}.json"
            if p.exists():
                out[(a, s)] = json.loads(p.read_text())
    return out


def select_algo(runs):
    """Highest median-over-seeds validation Sharpe at 1x (needs all 5 seeds of an algorithm)."""
    med = {}
    for a in ALGOS:
        v = [sharpe(run_returns(runs[(a, s)]["valid"]["1x"])[1]) for s in SEEDS if (a, s) in runs]
        if len(v) == len(SEEDS):
            med[a] = float(np.median(v))
    return (max(med, key=med.get) if med else None), med


def run_row(run, bench_sharpe, df):
    row = {"algo": run["algo"], "seed": run["seed"], "steps": run["steps"]}
    row["valid_sharpe_1x"] = sharpe(run_returns(run["valid"]["1x"])[1])
    for k in ("1x", "2x"):
        t = run["test"][k]
        d, r = run_returns(t)
        row[k] = stats_of(r) | {"trades": t["trades"], "total_cost_usd": t["cost"],
                                "daily_turnover": turnover(t["weights"]), "mean_cash_weight": float(np.mean(np.asarray(t["weights"])[:, 0]))}
    return row


def main():
    runs = load_runs()
    df = finrl_data.load()
    sel, val_med = select_algo(runs)
    any_run = next(iter(runs.values()))
    dates = any_run["test"]["1x"]["dates"]
    bench = benchmark_returns(dates, df)
    bstats = {k: stats_of(v) for k, v in bench.items()}
    spy_sh = bstats["SPY"]["sharpe"]
    res = {"study": 16, "plan": "FINRL_PLAN_16.md", "generated": datetime.now(timezone.utc).isoformat(),
           "universe": finrl_data.TICKERS, "n_stocks": len(finrl_data.TICKERS), "test_start": dates[0], "test_end": dates[-1],
           "benchmarks": bstats, "validation_median_sharpe_1x": val_med, "selected_algo": sel,
           "finrl": {"version": "0.3.7", "env": "StockTradingEnv", "costs": COST}, "runs": [], "returns": {}}
    # deflated-Sharpe trials: every run's validation daily Sharpe
    trials = np.array([sharpe(run_returns(r["valid"]["1x"])[1]) / math.sqrt(252) for r in runs.values()])
    spread = {}
    for a in ALGOS:
        rows = [run_row(runs[(a, s)], spy_sh, df) for s in SEEDS if (a, s) in runs]
        res["runs"] += rows
        if not rows:
            continue
        spread[a] = {}
        for k in ("1x", "2x"):
            v = np.array([r[k]["sharpe"] for r in rows])
            spread[a][k] = {"min": float(v.min()), "median": float(np.median(v)), "max": float(v.max()), "std": float(v.std(ddof=1)) if len(v) > 1 else 0.0,
                            "n_positive": int((v > 0).sum()), "n_beats_spy": int((v > spy_sh).sum())}
        for s in SEEDS:
            if (a, s) in runs:
                for k in ("1x", "2x"):
                    d, r = run_returns(runs[(a, s)]["test"][k])
                    res["returns"][f"{a}_{s}_{k}"] = {"dates": d, "returns": [round(float(x), 8) for x in r]}
    res["seed_spread"] = spread
    # coin flip, lag-1 and yearly readouts for every run (cheap); gates only for the selected algorithm
    for row in res["runs"]:
        run = runs[(row["algo"], row["seed"])]
        t = run["test"]["2x"]
        d, r = run_returns(t)
        W, R = np.asarray(t["weights"]), np.asarray(t["stock_ret"])
        p, null = permutation_null(r, W, R)
        row["coin_flip_p_2x"] = p
        row["coin_flip_null_median_sharpe"] = float(np.median(null))
        row["lag1"] = {k: stats_of(lag1_returns(W if k == "2x" else np.asarray(run["test"][k]["weights"]), R, COST[k])) for k in ("1x", "2x")}
        row["corr_spy_2x"] = float(np.corrcoef(r, bench["SPY"])[0, 1])
    if sel:
        rows = [r for r in res["runs"] if r["algo"] == sel]
        order = sorted(rows, key=lambda r: r["2x"]["sharpe"])
        med = order[len(order) // 2]
        qs = bh_qvalues([r["coin_flip_p_2x"] for r in rows])
        for r, q in zip(rows, qs):
            r["coin_flip_q_selected_algo"] = float(q)
        d, r_med = run_returns(runs[(sel, med["seed"])]["test"]["2x"])
        yrs = pd.Series(r_med, index=pd.to_datetime(d))
        yearly = {str(y): sharpe(g.values) for y, g in yrs.groupby(yrs.index.year)}
        dsr = deflated_sharpe(r_med, trials)
        cand = bool(len(rows) == len(SEEDS) and med["2x"]["sharpe"] > spy_sh and all(r["2x"]["sharpe"] > 0 for r in rows))
        ew_sh = bstats["EW28"]["sharpe"]
        lag_med = float(np.median([r["lag1"]["2x"]["sharpe"] for r in rows]))
        supported = bool(cand and med["coin_flip_p_2x"] < 0.05 and med["coin_flip_q_selected_algo"] < 0.10 and med["2x"]["sharpe"] > ew_sh)
        res["verdict"] = {"selected_algo": sel, "median_seed": med["seed"], "median_seed_sharpe_2x": med["2x"]["sharpe"],
                          "spy_sharpe": spy_sh, "ew28_sharpe": ew_sh, "all_seeds_positive_2x": all(r["2x"]["sharpe"] > 0 for r in rows),
                          "candidate": cand, "supported": supported, "median_seed_coin_flip_p": med["coin_flip_p_2x"],
                          "median_seed_coin_flip_q": med["coin_flip_q_selected_algo"], "deflated_sharpe_median_seed": dsr,
                          "lag1_median_sharpe_2x": lag_med, "lag1_beats_spy": bool(lag_med > spy_sh),
                          "works_for_us": bool(cand and lag_med > spy_sh), "yearly_sharpe_median_seed_2x": yearly}
    res["bench_returns"] = {"dates": dates[1:], **{k: [round(float(x), 8) for x in v] for k, v in bench.items()}}
    OUT.write_text(json.dumps(res, default=float))
    return res


if __name__ == "__main__":
    r = main()
    print("selected", r["selected_algo"], r["validation_median_sharpe_1x"])
    print("bench", {k: round(v["sharpe"], 2) for k, v in r["benchmarks"].items()})
    for row in r["runs"]:
        print(f"{row['algo']} s{row['seed']} val {row['valid_sharpe_1x']:5.2f} | 1x Sh {row['1x']['sharpe']:5.2f} tot {row['1x']['total_return']*100:6.1f}% dd {row['1x']['max_drawdown']*100:6.1f}% "
              f"| 2x Sh {row['2x']['sharpe']:5.2f} tot {row['2x']['total_return']*100:6.1f}% | p {row['coin_flip_p_2x']:.3f} | lag1 2x {row['lag1']['2x']['sharpe']:5.2f}")
    print(json.dumps(r.get("verdict"), indent=1))
