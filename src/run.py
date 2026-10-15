"""Orchestrates the backtest lab and writes results/results.json."""
from __future__ import annotations

import argparse
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
from strategies import STRATEGIES, positions

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "results.json"
HUNT_LOG = ROOT / "results" / "hunt_log.json"
N_SIMS, SEED, MIN_IS_TRADES = 2000, 20240601, 10

_ETF = dict(is_start="2005-01-01", is_end="2016-12-31", oos_start="2017-01-01",
            ann=252, cost_bps=3, wf_window=1260)
_BTC = dict(is_start="2014-09-17", is_end="2020-12-31", oos_start="2021-01-01",
            ann=365, cost_bps=10, wf_window=1825)
CONFIG = {t: dict(_ETF) for t in ["SPY", "QQQ", "IWM", "DIA", "GLD", "TLT"]}
CONFIG["BTC-USD"] = dict(_BTC)
# Lockbox: markets never used while searching. Only consulted for PASS/WEAK rows.
LOCKBOX = {t: dict(_ETF) for t in ["EFA", "EEM", "XLF", "XLE", "XLK"]}
LOCKBOX_CRYPTO = {"ETH-USD": dict(_BTC, is_start="2017-11-09")}


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


def evaluate(st, ticker, cfg, df, ctx):
    """Full procedure for one strategy on one market: tune on in-sample, one OOS run of the
    chosen params, cost stress, walk-forward, coin-flip. Returns the result row."""
    idx = df.index
    lo_i = idx.searchsorted(pd.Timestamp(cfg["is_start"]), "left")
    hi_i = idx.searchsorted(pd.Timestamp(cfg["is_end"]), "right")
    lo_o = idx.searchsorted(pd.Timestamp(cfg["oos_start"]), "left")
    O, C = df.Open.values, df.Close.values
    ann, cost = cfg["ann"], cfg["cost_bps"]
    I, X = slice(lo_i, hi_i), slice(lo_o, len(df))
    od, oo, oc = idx[X], O[X], C[X]
    pos = [np.clip(np.nan_to_num(positions(st, df, ctx, p).values.astype(float)), -1, 1) for p in st["grid"]]
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
    return {"strategy": st["id"], "asset": ticker, "params": st["grid"][b], "n_trials": n,
            "is": is_m, "oos": oos_m, "oos_cost2x_sharpe": sh2, "oos_grid_median_sharpe": grid_med,
            "dsr_prob": dsr, "walk_forward": wf, "coin_flip": cf, "_eq": eqs, "_dates": dd}


def window_info(ticker, cfg, df):
    idx = df.index
    lo_i = idx.searchsorted(pd.Timestamp(cfg["is_start"]), "left")
    hi_i = idx.searchsorted(pd.Timestamp(cfg["is_end"]), "right")
    lo_o = idx.searchsorted(pd.Timestamp(cfg["oos_start"]), "left")
    return {"ticker": ticker, "is_start": str(idx[lo_i].date()), "is_end": str(idx[hi_i - 1].date()),
            "oos_start": str(idx[lo_o].date()), "oos_end": str(idx[-1].date()), "bars": len(df)}


def run_asset(ticker, cfg, df, ctx, strategies):
    rows, bh = [], None
    for st in strategies:
        t0 = time.time()
        row = evaluate(st, ticker, cfg, df, ctx)
        if st["id"] == "buy_hold":
            m = row["oos"]
            bh = {"sharpe": m["sharpe"], "cagr": m["cagr"], "max_dd": m["max_dd"], "eq": row["_eq"]}
        rows.append(row)
        cf = row["coin_flip"]
        print(f"  {ticker:8s} {st['id']:20s} {row['n_trials']:3d} combos  oosSh={row['oos']['sharpe']:6.2f}  "
              f"p={'-' if cf is None else format(cf['p_value'], '.3f')}  ({time.time() - t0:.1f}s)", flush=True)
    for r in rows:
        r["buy_hold_oos"] = {k: bh[k] for k in ("sharpe", "cagr", "max_dd")}
        r["equity"] = {"dates": r.pop("_dates"), "strategy": r.pop("_eq"), "buy_hold": bh["eq"]}
    return rows, window_info(ticker, cfg, df)


def load_market(ticker, cfg, ctx_raw):
    df = data.load(ticker).loc[cfg["is_start"]:]
    return df, data.align_ctx(ctx_raw, df.index)


def fisher_p(ps) -> float:
    """Fisher's combined p: X = -2 sum ln p ~ chi2(2k); closed-form survival for even dof."""
    x = -2.0 * float(np.sum(np.log(np.clip(ps, 1e-300, 1))))
    k = len(ps)
    term, tot = 1.0, 1.0
    for i in range(1, k):
        term *= (x / 2) / i
        tot += term
    return float(min(1.0, math.exp(-x / 2) * tot))


def lockbox(row, st, ctx_raw, cache):
    """Re-run the exact same procedure (fresh tuning, OOS, coin-flip) on markets never searched.
    Always-invested / no-trade lockbox runs count as p = 1."""
    crypto = row["asset"] == "BTC-USD"
    markets = LOCKBOX_CRYPTO if crypto else LOCKBOX
    out = []
    for t, cfg in markets.items():
        if t not in cache:
            cache[t] = load_market(t, cfg, ctx_raw)
        df, ctx = cache[t]
        r = evaluate(st, t, cfg, df, ctx)
        p = r["coin_flip"]["p_value"] if r["coin_flip"] else 1.0
        out.append({"ticker": t, "p_value": p, "oos_sharpe": r["oos"]["sharpe"], "params": r["params"],
                    "oos_cagr": r["oos"]["cagr"], "n_trades": r["oos"]["n_trades"]})
    ps = [a["p_value"] for a in out]
    med = float(np.median([a["oos_sharpe"] for a in out]))
    if crypto:
        fp, ok = ps[0], ps[0] < 0.05
        rule = "ETH-USD coin-flip p < 0.05"
    else:
        fp = fisher_p(ps)
        ok = fp < 0.05 and med > 0
        rule = "Fisher combined p < 0.05 and median OOS Sharpe > 0"
    return {"assets": out, "fisher_p": fp, "median_oos_sharpe": med, "confirmed": bool(ok), "rule": rule}


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


def hunt_summary(results, strategies, through):
    tested = [r for r in results if r["strategy"] != "buy_hold"]
    batch_of = {s["id"]: s["batch"] for s in strategies}
    from collections import Counter

    def row(r):
        lb = r.get("lockbox")
        return {"strategy": r["strategy"], "asset": r["asset"], "batch": batch_of[r["strategy"]],
                "verdict": r["verdict"], "p": r["coin_flip"]["p_value"], "q": r["coin_flip"]["q_value"],
                "oos_sharpe": r["oos"]["sharpe"], "bh_sharpe": r["buy_hold_oos"]["sharpe"],
                "lockbox_confirmed": None if lb is None else lb["confirmed"],
                "lockbox_p": None if lb is None else lb["fisher_p"]}

    confirmed = [r for r in tested if r["verdict"] == "PASS" and (r.get("lockbox") or {}).get("confirmed")]
    return {
        "through_batch": through,
        "strategies_in_pool": sum(1 for s in strategies if s["id"] != "buy_hold"),
        "new_strategies": [s["id"] for s in strategies if s["batch"] == through],
        "tests_in_bh_pool": sum(1 for r in tested if r["coin_flip"]),
        "verdicts": dict(Counter(r["verdict"] for r in tested)),
        "pass_weak": [row(r) for r in tested if r["verdict"] in ("PASS", "WEAK")],
        "confirmed_pass": [f"{r['strategy']} on {r['asset']}" for r in confirmed],
    }


def write_log(entry):
    log = json.loads(HUNT_LOG.read_text()) if HUNT_LOG.exists() else {"batches": []}
    log["batches"] = [b for b in log["batches"] if b["through_batch"] != entry["through_batch"]] + [entry]
    log["batches"].sort(key=lambda b: b["through_batch"])
    HUNT_LOG.write_text(json.dumps(clean(log), indent=1, allow_nan=False))
    return log


def main(through=None):
    t0 = time.time()
    through = max(s["batch"] for s in STRATEGIES) if through is None else through
    strategies = [s for s in STRATEGIES if s["batch"] <= through]
    ctx_raw = data.load_context()
    results, assets = [], []
    for ticker, cfg in CONFIG.items():
        print(f"== {ticker}: loading data", flush=True)
        df, ctx = load_market(ticker, cfg, ctx_raw)
        rows, a = run_asset(ticker, cfg, df, ctx, strategies)
        results += rows
        assets.append(a)
    assign_verdicts(results)
    by_id = {s["id"]: s for s in strategies}
    cache = {}
    for r in results:
        if r["verdict"] in ("PASS", "WEAK"):
            r["lockbox"] = lockbox(r, by_id[r["strategy"]], ctx_raw, cache)
            lb = r["lockbox"]
            print(f"  lockbox {r['strategy']:20s} {r['asset']:8s} {r['verdict']:4s} combined p={lb['fisher_p']:.3f} "
                  f"medSh={lb['median_oos_sharpe']:.2f} confirmed={lb['confirmed']}", flush=True)
        else:
            r["lockbox"] = None
    entry = hunt_summary(results, strategies, through)
    entry["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log = write_log(entry)
    done = bool(entry["confirmed_pass"])
    hunt = {"through_batch": through, "batches": [b["through_batch"] for b in log["batches"]],
            "stop_rule": "stop at the first PASS (BH q < 0.10 over every test ever run) confirmed on lockbox markets",
            "status": "confirmed PASS found" if done else "no confirmed PASS",
            "confirmed_pass": entry["confirmed_pass"],
            "strategies_in_pool": entry["strategies_in_pool"], "tests_in_bh_pool": entry["tests_in_bh_pool"],
            "lockbox_markets": list(LOCKBOX), "lockbox_crypto": list(LOCKBOX_CRYPTO), "log": "results/hunt_log.json"}
    out = {"generated": entry["generated"],
           "config": {"assets": CONFIG, "lockbox": LOCKBOX | LOCKBOX_CRYPTO, "n_sims": N_SIMS, "seed": SEED,
                      "min_is_trades": MIN_IS_TRADES,
                      "cost_note": "one-way bps per position change; OOS also re-run at 2x",
                      "execution": "signal on close t, trade at open t+1"},
           "hunt": hunt,
           "strategies": [{k: s[k] for k in ("id", "name", "family", "source", "description", "batch")}
                          | {"grid_size": len(s["grid"]), "uses_context": s["ctx"]} for s in strategies],
           "assets": assets, "results": results}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(clean(out), allow_nan=False))
    print(f"\n{'strategy':20s} {'asset':8s} {'OOS Sh':>7s} {'B&H Sh':>7s} {'p':>6s} {'q':>6s}  verdict")
    for r in results:
        cf = r["coin_flip"]
        if not cf or cf["p_value"] >= 0.05:
            continue
        print(f"{r['strategy']:20s} {r['asset']:8s} {r['oos']['sharpe']:7.2f} "
              f"{r['buy_hold_oos']['sharpe']:7.2f} {cf['p_value']:6.3f} {cf['q_value']:6.3f}  {r['verdict']}")
    print("\nverdicts:", entry["verdicts"], "| BH pool:", entry["tests_in_bh_pool"],
          "| confirmed:", entry["confirmed_pass"] or "none")
    print(f"wrote {OUT} in {time.time() - t0:.0f}s")
    return entry


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=None, help="include strategies up to this hunt batch")
    main(ap.parse_args().batch)
