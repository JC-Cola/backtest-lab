"""Study 15 (QLIB_PLAN_15.md): qlib Alpha158 + LightGBM + TopkDropoutStrategy on today's S&P 500 / Nasdaq-100 names.

    python qlib15.py [--end YYYY-MM-DD] [--no-w]        # writes results/qlib15.json and results/qlib15_returns.csv

Model P: one fit on 2010-2019 (valid 2020-2021), applied to 2022 -> end. Model W: refit every quarter on the trailing 8 years.
Backtests use qlib's own backtester (deal at the next open, positions marked at the close) at 0.05% and 0.10% per side.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import qlib_data as D
import qlib_model as M

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "qlib15.json"
OUT_CSV = ROOT / "results" / "qlib15_returns.csv"
TRAIN, VALID, TEST_START = ("2010-01-01", "2019-12-31"), ("2020-01-01", "2021-12-31"), "2022-01-01"
COSTS = {"0.05%": 0.0005, "0.10%": 0.001}
TOPK, N_DROP, ACCOUNT = 50, 5, 1_000_000
COIN_TRIALS = 1000


# ---------------------------------------------------------------- statistics (pure)

def stats(r: pd.Series) -> dict:
    r = r.dropna()
    n = len(r)
    if n < 2:
        return {"days": n}
    eq = (1 + r).cumprod()
    sd = r.std(ddof=1)
    return {"days": n, "total_return": round(float(eq.iloc[-1] - 1), 4),
            "annual_return": round(float(eq.iloc[-1] ** (252.0 / n) - 1), 4),
            "sharpe": round(float(r.mean() / sd * np.sqrt(252)), 3) if sd > 0 else None,
            "max_drawdown": round(float((eq / eq.cummax() - 1).min()), 4),
            "vol": round(float(sd * np.sqrt(252)), 4)}


def by_year(r: pd.Series, extra: dict | None = None) -> dict:
    out = {}
    for y, g in r.groupby(r.index.year):
        out[str(y)] = stats(g)
        if extra:
            out[str(y)].update({k: round(float(v[v.index.year == y].mean()), 4) for k, v in extra.items() if (v.index.year == y).any()})
    return out


def ic_summary(ic: pd.DataFrame) -> dict:
    if ic.empty:
        return {}
    return {"ic": round(float(ic["ic"].mean()), 4), "rank_ic": round(float(ic["rank_ic"].mean()), 4),
            "icir": round(float(ic["ic"].mean() / ic["ic"].std(ddof=1) * np.sqrt(252)), 2),
            "rank_icir": round(float(ic["rank_ic"].mean() / ic["rank_ic"].std(ddof=1) * np.sqrt(252)), 2), "days": int(len(ic))}


# ---------------------------------------------------------------- qlib backtest

def run_backtest(pred: pd.Series, start, end, cost: float):
    """qlib's backtester + TopkDropoutStrategy. Returns (daily frame: net, gross, cost, turnover; positions dict)."""
    from qlib.backtest import backtest as qbacktest
    from qlib.backtest.executor import SimulatorExecutor
    from qlib.contrib.strategy import TopkDropoutStrategy
    strat = TopkDropoutStrategy(signal=pred, topk=TOPK, n_drop=N_DROP, method_sell="bottom", method_buy="top", hold_thresh=1)
    ex = SimulatorExecutor(time_per_step="day", generate_portfolio_metrics=True)
    pm, _ = qbacktest(start_time=start, end_time=end, strategy=strat, executor=ex, account=ACCOUNT, benchmark="SPY",
                      exchange_kwargs={"freq": "day", "limit_threshold": None, "deal_price": "open", "open_cost": cost,
                                       "close_cost": cost, "min_cost": 0, "trade_unit": 1})
    rep, pos = pm["1day"]
    rep = rep.copy()
    rep.index = pd.to_datetime(rep.index).normalize()
    df = pd.DataFrame({"gross": rep["return"], "cost": rep["cost"], "turnover": rep["turnover"]})
    df["net"] = df["gross"] - df["cost"]
    return df, pos


def replaced_per_day(positions: dict) -> float:
    """Mean number of names the strategy swaps per day (sells), from qlib's daily position snapshots."""
    days = sorted(positions)
    prev, n = None, []
    for d in days:
        cur = set(positions[d].get_stock_list())
        if prev is not None:
            n.append(len(prev - cur))
        prev = cur
    return float(np.mean(n)) if n else 0.0


# ---------------------------------------------------------------- benchmarks

def load_closes(symbols, cal) -> tuple:
    o, c = {}, {}
    for s in symbols:
        f = D.CSV_DIR / ("%s.csv" % s)
        if f.exists():
            d = pd.read_csv(f, index_col=0, parse_dates=True)
            o[s], c[s] = d["open"], d["close"]
    return pd.DataFrame(o).reindex(cal), pd.DataFrame(c).reindex(cal)


def coin_flip(O: np.ndarray, C: np.ndarray, m: float, trials=COIN_TRIALS, topk=TOPK, cost=0.0005, seed=15) -> np.ndarray:
    """Random top-k portfolios swapping m random names a day. O, C: (T days, N symbols) open/close. Returns (trials, T-1) daily net
    returns for days 1..T-1 (same accounting as qlib: bought at the open and marked at the close, sold at the open)."""
    rng = np.random.default_rng(seed)
    T, N = C.shape
    ret = np.zeros((trials, T - 1))
    held = np.argsort(rng.random((trials, N)) * np.isfinite(C[0]) - (~np.isfinite(C[0])), axis=1)[:, -topk:]   # random start
    rows = np.arange(trials)[:, None]
    mmax = int(np.ceil(m))
    for t in range(1, T):
        cprev, o, c = C[t - 1], O[t], C[t]
        ok = np.isfinite(o) & np.isfinite(c)
        k = np.full(trials, int(np.floor(m))) + (rng.random(trials) < (m - np.floor(m)))
        # which held positions to sell: m random ones
        rank = np.argsort(rng.random((trials, topk)), axis=1)
        sell_slot = rank[:, :mmax]
        sell_on = np.arange(mmax)[None, :] < k[:, None]
        sold = np.take_along_axis(held, sell_slot, axis=1)
        # candidates to buy: valid names not held
        key = rng.random((trials, N)) * ok
        key[rows, held] = -1.0
        buys = np.argsort(key, axis=1)[:, -mmax:]
        # day return: continuing names close/close, sold names prev close -> open, bought names open -> close
        with np.errstate(invalid="ignore", divide="ignore"):
            cc = np.where(np.isfinite(c / cprev), c / cprev - 1, 0.0)
            so = np.where(np.isfinite(o / cprev), o / cprev - 1, 0.0)
            oc = np.where(np.isfinite(c / o), c / o - 1, 0.0)
        r_cont = cc[held].sum(axis=1)
        sold_cc = np.take_along_axis(cc[held], sell_slot, axis=1)
        r_cont = r_cont - (sold_cc * sell_on).sum(axis=1)
        r_sold = (so[sold] * sell_on).sum(axis=1)
        r_buy = (oc[buys] * sell_on).sum(axis=1)
        ret[:, t - 1] = (r_cont + r_sold + r_buy) / topk - cost * 2.0 * k / topk
        new = held.copy()
        for j in range(mmax):
            sel = sell_on[:, j]
            new[np.arange(trials)[sel], sell_slot[sel, j]] = buys[sel, j]
        held = new
    return ret


def sharpe_of(r: np.ndarray) -> np.ndarray:
    return r.mean(axis=-1) / r.std(axis=-1, ddof=1) * np.sqrt(252)


# ---------------------------------------------------------------- the study

def walk_forward(frame: pd.DataFrame, end: pd.Timestamp) -> tuple[pd.Series, list]:
    """Quarterly refit on the trailing 8 years (valid = the last 6 months, 2-day gap), applied to the next quarter."""
    preds, fits = [], []
    q = pd.Timestamp(TEST_START)
    while q <= end:
        nxt = min(q + pd.DateOffset(months=3), end + pd.Timedelta(days=1))
        vs = q - pd.DateOffset(months=6)
        tr = (max(pd.Timestamp(TRAIN[0]), q - pd.DateOffset(years=8)), vs - pd.Timedelta(days=3))
        va = (vs, q - pd.Timedelta(days=4))
        t0 = time.time()
        b = M.fit(frame, tr, va)
        part = M.between(frame, q, nxt - pd.Timedelta(days=1))
        if len(part):
            preds.append(b.predict(part))
        fits.append({"from": q.strftime("%Y-%m-%d"), "best_iteration": b.meta["best_iteration"], "rows": b.meta["rows"],
                     "seconds": round(time.time() - t0, 1)})
        print("  W fit %s: %d rounds, %.0fs" % (q.date(), b.meta["best_iteration"], time.time() - t0), flush=True)
        q = nxt
    return pd.concat(preds), fits


def load_frame(end: str) -> pd.DataFrame:
    cache = D.QDIR / ("frame_%s.pkl" % end)
    if cache.exists():
        return pd.read_pickle(cache)
    t0 = time.time()
    df = M.raw_frame(TRAIN[0], end)
    print("Alpha158 frame %s in %.0fs" % (df.shape, time.time() - t0), flush=True)
    df.to_pickle(cache)
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", help="last day (default: last day in the data)")
    ap.add_argument("--no-w", action="store_true", help="skip the quarterly walk-forward variant")
    a = ap.parse_args(argv)
    D.init_qlib(kernels=8)
    cov = D.coverage()
    end = a.end or cov["last_day"]
    t_all = time.time()
    frame = load_frame(end)
    label = frame["LABEL0"]
    results = {"study": 15, "plan": "QLIB_PLAN_15.md", "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
               "config": {"train": TRAIN, "valid": VALID, "test": [TEST_START, end], "topk": TOPK, "n_drop": N_DROP, "costs_per_side": COSTS,
                          "label": M.LABEL_CFG[0][0], "lgb": M.LGB_PARAMS, "account": ACCOUNT},
               "data": {**cov, "source": "Yahoo Finance daily, split+dividend adjusted; survivorship-biased (today's constituents)",
                        "frame_rows": int(len(frame))}}
    # ---- model P
    t0 = time.time()
    b = M.fit(frame, TRAIN, VALID, meta={"name": "P"})
    pred_all = b.predict(M.between(frame, VALID[0], end))
    preds = {"P": pred_all[M.days_of(pred_all) >= pd.Timestamp(TEST_START)]}
    results["model_P"] = {"best_iteration": b.meta["best_iteration"], "train_rows": b.meta["rows"], "fit_seconds": round(time.time() - t0, 1)}
    ic_valid = M.ic_by_day(pred_all[M.days_of(pred_all) < pd.Timestamp(TEST_START)], label)
    results["ic"] = {"P_valid": ic_summary(ic_valid), "P_test": ic_summary(M.ic_by_day(preds["P"], label))}
    print("P fitted: %s" % results["model_P"], flush=True)
    if not a.no_w:
        wc = D.QDIR / ("predW_%s.pkl" % end)
        if wc.exists():
            preds["W"], fits = pd.read_pickle(wc)
        else:
            preds["W"], fits = walk_forward(frame, pd.Timestamp(end))
            pd.to_pickle((preds["W"], fits), wc)
        results["model_W_fits"] = fits
        results["ic"]["W_test"] = ic_summary(M.ic_by_day(preds["W"], label))
    # ---- backtests
    bt, positions = {}, {}
    cal_all = pd.to_datetime((D.BIN_DIR / "calendars" / "day.txt").read_text().split())
    bt_end = cal_all[cal_all <= pd.Timestamp(end)][-2]            # qlib quirk: the last calendar day cannot be a step end
    for name, pr in preds.items():
        for cname, c in COSTS.items():
            t0 = time.time()
            bt[(name, cname)], pos = run_backtest(pr, TEST_START, bt_end, c)
            if cname == "0.05%":
                positions[name] = pos
            print("backtest %s @ %s/side: %.0fs" % (name, cname, time.time() - t0), flush=True)
    first = bt[("P", "0.05%")]["turnover"].gt(0).idxmax()        # drop the idle cash days before the first signal can trade
    bt = {k: v[v.index >= first] for k, v in bt.items()}
    days = bt[("P", "0.05%")].index
    # ---- benchmarks
    syms = [s.split("\t")[0] for s in (D.BIN_DIR / "instruments" / "sp500.txt").read_text().strip().split("\n")]
    cal = pd.DatetimeIndex(pd.to_datetime((D.BIN_DIR / "calendars" / "day.txt").read_text().split()))
    O, C = load_closes(syms + ["SPY"], cal)
    spy = C["SPY"].pct_change().reindex(days)
    allr = C[syms].pct_change()
    ew = allr.mean(axis=1).reindex(days)
    # ---- tables
    tables = {}
    for (name, cname), df in bt.items():
        t = {"pooled": stats(df["net"]), "by_year": by_year(df["net"], {"turnover_oneway_annual": df["turnover"] / 2 * 252})}
        t["pooled"]["turnover_oneway_annual"] = round(float(df["turnover"].mean() / 2 * 252), 2)
        t["pooled"]["cost_drag_annual"] = round(float(df["cost"].mean() * 252), 4)
        tables["%s@%s" % (name, cname)] = t
    results["tables"] = tables
    results["benchmarks"] = {"SPY": {"pooled": stats(spy), "by_year": by_year(spy)},
                             "equal_weight_universe": {"pooled": stats(ew), "by_year": by_year(ew)}}
    # ---- coin flip (same turnover as P at 0.05%)
    m = replaced_per_day(positions["P"])
    ix = [cal.get_loc(d) for d in days]
    lo = ix[0] - 1                                              # the day before the first test day: its close is the start
    Ot, Ct = O[syms].to_numpy()[lo:ix[-1] + 1], C[syms].to_numpy()[lo:ix[-1] + 1]
    flips = coin_flip(Ot, Ct, m)
    s_flip = sharpe_of(flips)
    s_P = stats(bt[("P", "0.05%")]["net"])["sharpe"]
    results["coin_flip"] = {"names_swapped_per_day": round(m, 2), "trials": COIN_TRIALS, "random_sharpe_mean": round(float(s_flip.mean()), 3),
                            "random_sharpe_p95": round(float(np.percentile(s_flip, 95)), 3), "P_sharpe_0.05%": s_P,
                            "p_value": round(float((np.sum(s_flip >= s_P) + 1) / (COIN_TRIALS + 1)), 4),
                            "random_annual_return_mean": round(float(((1 + flips).prod(axis=1) ** (252 / flips.shape[1]) - 1).mean()), 4)}
    # ---- verdict
    p10 = bt[("P", "0.10%")]["net"]
    spy_pool = stats(spy)
    p_pool = stats(p10)
    yrs = [y for y in sorted(set(days.year)) if (days.year == y).sum() > 240]
    beat = [y for y in yrs if (stats(p10[p10.index.year == y]).get("sharpe") or -9) > (stats(spy[spy.index.year == y]).get("sharpe") or -9)]
    candidate = bool(p_pool["sharpe"] is not None and p_pool["sharpe"] > spy_pool["sharpe"] and p_pool["total_return"] > 0)
    strong = bool(candidate and results["coin_flip"]["p_value"] < 0.05 and len(beat) >= min(3, len(yrs)))
    results["verdict"] = {"rule": "candidate: P pooled test Sharpe at 0.10%/side > SPY's and net return > 0; strong: also coin-flip p < 0.05 at 0.05% and beats SPY's Sharpe in >= 3 full test years at 0.10%",
                          "P_sharpe_at_0.10%": p_pool["sharpe"], "SPY_sharpe": spy_pool["sharpe"], "full_years": yrs, "years_beating_SPY_at_0.10%": beat,
                          "candidate": candidate, "strong_candidate": strong}
    # ---- returns for tear sheets
    rets = pd.DataFrame({"qlib": bt[("P", "0.05%")]["net"], "spy": spy, "equal_weight": ew})
    if "W" in preds:
        rets.insert(1, "qlib_w", bt[("W", "0.05%")]["net"])
    rets.index.name = "date"
    results["daily_returns"] = {d.strftime("%Y-%m-%d"): round(float(v), 6) for d, v in rets["qlib"].dropna().items()}   # P, net at 0.05%
    results["daily_returns_all"] = {c: {d.strftime("%Y-%m-%d"): round(float(v), 6) for d, v in rets[c].dropna().items()} for c in rets.columns}
    # weights[date][symbol]: portfolio weights held at the close of `date` after trading at that day's open; keyed one session EARLIER
    # (the signal date, as vbt_portfolio's lag=1 expects: decided at close t, filled at t+1).
    pdays = sorted(pd.Timestamp(d) for d in positions["P"])
    wts = {}
    for i in range(1, len(pdays)):
        w = positions["P"][pdays[i]].get_stock_weight_dict(only_stock=True)
        if w:
            wts[pdays[i - 1].strftime("%Y-%m-%d")] = {k: round(float(v), 5) for k, v in w.items()}
    results["weights"] = wts
    results["runtime_seconds"] = round(time.time() - t_all, 1)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(results, indent=1) + "\n")
    rets.to_csv(OUT_CSV, float_format="%.6f")
    print(json.dumps({k: results[k] for k in ("verdict", "coin_flip", "ic")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
