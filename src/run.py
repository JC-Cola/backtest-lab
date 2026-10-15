"""Orchestrates the backtest lab and writes results/results.json."""
from __future__ import annotations

import json
import math
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import data
from engine import count_trades, extract_trades, returns_np
from stats import (bh_qvalues, coin_flip, deflated_sharpe, metrics, select_best,
                   sharpe_np, walk_forward)
from strategies import STRATEGIES

OUT = Path(__file__).resolve().parent.parent / "results" / "results.json"
N_SIMS, SEED, MIN_IS_TRADES = 2000, 20240601, 10

_ETF = dict(is_start="2005-01-01", is_end="2016-12-31", oos_start="2017-01-01",
            ann=252, cost_bps=3, wf_window=1260)
_BTC = dict(is_start="2014-09-17", is_end="2020-12-31", oos_start="2021-01-01",
            ann=365, cost_bps=10, wf_window=1825)
CONFIG = {t: dict(_ETF) for t in ["SPY", "QQQ", "IWM", "DIA", "GLD", "TLT"]}
CONFIG["BTC-USD"] = dict(_BTC)


def clean(x):
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if not math.isfinite(x) else round(float(x), 4)
    return x


def weekly_equity(dates, r):
    eq = pd.Series(np.cumprod(1 + r), index=dates)
    per = pd.Series(dates.to_period("W-FRI"), index=dates)
    wk = eq[~per.duplicated(keep="last")]
    out_d = [d.strftime("%Y-%m-%d") for d in wk.index]
    out_v = list(wk.values)
    if wk.index[0] != dates[0]:
        out_d.insert(0, dates[0].strftime("%Y-%m-%d"))
        out_v.insert(0, 1.0)
    return out_d, out_v


def run_asset(ticker, cfg, df):
    idx = df.index
    lo_i = idx.searchsorted(pd.Timestamp(cfg["is_start"]), "left")
    hi_i = idx.searchsorted(pd.Timestamp(cfg["is_end"]), "right")
    lo_o = idx.searchsorted(pd.Timestamp(cfg["oos_start"]), "left")
    O, C = df.Open.values, df.Close.values
    ann, cost = cfg["ann"], cfg["cost_bps"]
    I, X = slice(lo_i, hi_i), slice(lo_o, len(df))
    od, oo, oc = idx[X], O[X], C[X]
    rows = []
    bh = None
    for st in STRATEGIES:
        t0 = time.time()
        pos = [np.clip(np.nan_to_num(st["fn"](df, **p).values.astype(float)), -1, 1) for p in st["grid"]]
        n = len(pos)
        is_r = [returns_np(O[I], C[I], s[I], cost) for s in pos]
        is_sh = [sharpe_np(r, ann) for r in is_r]
        is_nt = [count_trades(s[I]) for s in pos]
        b = select_best(is_sh, is_nt, MIN_IS_TRADES)
        s_is, s_oos = pos[b][I], pos[b][X]
        is_tr = extract_trades(O[I], C[I], s_is, cost)
        is_m = metrics(is_r[b], s_is, is_tr, ann)
        trial = [r.mean() / r.std(ddof=1) for r in is_r if r.std(ddof=1) > 1e-12]
        dsr = deflated_sharpe(is_r[b], trial) if st["id"] != "buy_hold" else None
        # single OOS run of the chosen params
        r_oos = returns_np(oo, oc, s_oos, cost)
        tr_oos = extract_trades(oo, oc, s_oos, cost)
        oos_m = metrics(r_oos, s_oos, tr_oos, ann)
        sh2 = sharpe_np(returns_np(oo, oc, s_oos, 2 * cost), ann)
        grid_med = float(np.median([sharpe_np(returns_np(oo, oc, s[X], cost), ann) for s in pos]))
        wf = walk_forward(O, C, idx, pos, cfg["wf_window"], ann, cost)
        cf = None
        if st["id"] != "buy_hold" and oos_m["exposure"] <= 0.95:
            cf = coin_flip(oo, tr_oos, cost, N_SIMS, SEED + zlib.crc32(f"{st['id']}{ticker}".encode()) % 100000)
        dd, eqs = weekly_equity(od, r_oos)
        row = {"strategy": st["id"], "asset": ticker, "params": st["grid"][b], "n_trials": n,
               "is": is_m, "oos": oos_m, "oos_cost2x_sharpe": sh2, "oos_grid_median_sharpe": grid_med,
               "dsr_prob": dsr, "walk_forward": wf, "coin_flip": cf,
               "_eq": eqs, "_dates": dd}
        if st["id"] == "buy_hold":
            bh = {"sharpe": oos_m["sharpe"], "cagr": oos_m["cagr"], "max_dd": oos_m["max_dd"], "eq": eqs}
        rows.append(row)
        print(f"  {ticker:8s} {st['id']:20s} {n:3d} combos  oosSh={oos_m['sharpe']:6.2f}  "
              f"p={'-' if cf is None else format(cf['p_value'], '.3f')}  ({time.time() - t0:.1f}s)", flush=True)
    for r in rows:
        r["buy_hold_oos"] = {k: bh[k] for k in ("sharpe", "cagr", "max_dd")}
        r["equity"] = {"dates": r.pop("_dates"), "strategy": r.pop("_eq"), "buy_hold": bh["eq"]}
    return rows, {"ticker": ticker, "is_start": str(idx[lo_i].date()), "is_end": str(idx[hi_i - 1].date()),
                  "oos_start": str(idx[lo_o].date()), "oos_end": str(idx[-1].date()), "bars": len(df)}


def assign_verdicts(results):
    ps = [(i, r["coin_flip"]["p_value"]) for i, r in enumerate(results) if r["coin_flip"]]
    qs = bh_qvalues([p for _, p in ps])
    for (i, _), q in zip(ps, qs):
        results[i]["coin_flip"]["q_value"] = float(q)
    for r in results:
        cf, bhs = r["coin_flip"], r["buy_hold_oos"]["sharpe"]
        sh, sh2 = r["oos"]["sharpe"], r["oos_cost2x_sharpe"]
        r["beat_buy_hold"] = None if r["strategy"] == "buy_hold" else bool(sh > bhs)
        r["note"] = None
        if r["strategy"] == "buy_hold":
            r["verdict"] = "BENCHMARK"
        elif cf is None:
            r["verdict"] = "N/A (always invested)" if r["oos"]["exposure"] > 0.95 else "N/A (no trades)"
            r["note"] = (f"OOS Sharpe {sh:.2f} vs buy&hold {bhs:.2f}: "
                         f"{'beat' if sh > bhs else 'did not beat'} buy & hold")
        elif cf["p_value"] < 0.05:
            ok = cf["q_value"] < 0.10 and sh > 0 and sh2 > 0
            r["verdict"] = "PASS" if ok else "WEAK"
        else:
            r["verdict"] = "FAIL"


def main():
    t0 = time.time()
    results, assets = [], []
    for ticker, cfg in CONFIG.items():
        print(f"== {ticker}: loading data", flush=True)
        df = data.load(ticker)
        df = df.loc[cfg["is_start"]:]
        rows, a = run_asset(ticker, cfg, df)
        results += rows
        assets.append(a)
    assign_verdicts(results)
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "config": {"assets": CONFIG, "n_sims": N_SIMS, "seed": SEED, "min_is_trades": MIN_IS_TRADES,
                      "cost_note": "one-way bps per position change; OOS also re-run at 2x",
                      "execution": "signal on close t, trade at open t+1"},
           "strategies": [{k: s[k] for k in ("id", "name", "family", "source", "description")}
                          | {"grid_size": len(s["grid"])} for s in STRATEGIES],
           "assets": assets, "results": results}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(clean(out), allow_nan=False))
    print(f"\n{'strategy':20s} {'asset':8s} {'OOS Sh':>7s} {'B&H Sh':>7s} {'p':>6s} {'q':>6s}  verdict")
    for r in results:
        cf = r["coin_flip"]
        p = f"{cf['p_value']:.3f}" if cf else "-"
        q = f"{cf['q_value']:.3f}" if cf else "-"
        print(f"{r['strategy']:20s} {r['asset']:8s} {r['oos']['sharpe']:7.2f} "
              f"{r['buy_hold_oos']['sharpe']:7.2f} {p:>6s} {q:>6s}  {r['verdict']}")
    from collections import Counter
    print("\nverdicts:", dict(Counter(r["verdict"] for r in results)))
    print(f"wrote {OUT} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
