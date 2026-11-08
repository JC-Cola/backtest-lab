"""Crypto study 10 (CRYPTO_PLAN_10.md): does skipping weekend-fill entries improve the crypto trend filter?

    python src/crypto10.py        # downloads the untouched coins, writes results/crypto10.json

Positions use crypto9_base.execute(..., weekend=True) unchanged (study 9's exact weekend rule).
"""
from __future__ import annotations

import json
import math
import sys
import zlib
from datetime import datetime, timezone

import numpy as np

import crypto_data as CD
import strategies_trend as tr
from crypto9_base import ANN, COST, STRESS, execute, perf
from engine import extract_trades, returns_np
from run import fisher_p

OUT = CD.DIR.parent.parent / "results" / "crypto10.json"
PLAN = "CRYPTO_PLAN_10.md"
COINS = ["LTC-USD", "BCH-USD", "LINK-USD", "AVAX-USD", "XRP-USD", "ADA-USD", "DOGE-USD", "DOT-USD", "XLM-USD", "ATOM-USD"]
DESCRIPTIVE = ["BTC-USD"]
NS = (150, 50)               # 150 primary
WARMUP, MIN_BARS = 150, 150 + 3 * 365
N_BOOT, BLOCK, SEED = 10000, 20, 20261110
KEYS = ("sharpe", "calmar", "max_dd", "total_return", "trades_per_year")


def positions(df, n, weekend):
    """Long-or-flat SMA(n) trend filter; with `weekend`, entries whose FILL day is Sat/Sun wait (study 9's rule)."""
    s = tr.trend_filter(df, n).values.astype(float)
    wd_fill = (np.asarray(df.index.dayofweek) + 1) % 7
    return execute(s, wd_fill, 0.0, weekend)


def run_version(df, n, weekend, cost):
    """(daily returns on the shared window, positions on the window, perf dict). Returns are computed on the full
    series and then sliced, so skip and no-skip share the identical window."""
    o, c = df.Open.values.astype(float), df.Close.values.astype(float)
    s = positions(df, n, weekend)
    r = returns_np(o, c, s, cost)[WARMUP:]
    tr_ = extract_trades(o, c, s, cost)
    return r, s[WARMUP:], perf(r, s[WARMUP:], tr_)


def stationary_bootstrap_p(d, n_boot=N_BOOT, block=BLOCK, seed=0):
    """One-sided p for mean(d) > 0: Politis-Romano stationary bootstrap (circular, mean block length `block`), the
    resampled means centred on the observed mean (null mean 0). p = (1 + #{mean* - obs >= obs}) / (n_boot + 1)."""
    d = np.asarray(d, float)
    T = len(d)
    obs = d.mean()
    rng = np.random.default_rng(seed)
    cnt, done, chunk = 0, 0, 500
    while done < n_boot:
        b = min(chunk, n_boot - done)
        new = rng.random((b, T)) < 1.0 / block            # True = start a new block
        new[:, 0] = True
        starts = rng.integers(0, T, (b, T))
        idx = np.empty((b, T), dtype=np.int64)
        idx[:, 0] = starts[:, 0]
        # index = previous + 1 (mod T) unless a new block starts; vectorised via the block-start trick
        pos = np.arange(T)
        last_start = np.maximum.accumulate(np.where(new, pos, 0), axis=1)
        base = np.take_along_axis(starts, last_start, axis=1)
        idx = (base + (pos - last_start)) % T
        m = d[idx].mean(axis=1)
        cnt += int(np.sum(m - obs >= obs))
        done += b
    return (1 + cnt) / (n_boot + 1)


def _seed(coin, n, cost):
    return SEED + zlib.crc32(f"{coin}{n}{cost}".encode()) % 100000


def compare(name, df):
    out = {"asset": name, "first": str(df.index[0].date()), "last": str(df.index[-1].date()), "bars": len(df),
           "gap_bars_filled": int((df.Volume == 0).sum()), "window": [str(df.index[WARMUP].date()), str(df.index[-1].date())],
           "years": round((len(df) - WARMUP) / ANN, 2), "n": {}}
    for n in NS:
        res = {}
        for label, cost in (("c20", COST), ("c50", STRESS)):
            r0, s0, p0 = run_version(df, n, False, cost)
            r1, s1, p1 = run_version(df, n, True, cost)
            d = r1 - r0
            res[label] = {"noskip": {k: p0[k] for k in KEYS}, "skip": {k: p1[k] for k in KEYS},
                          "sharpe_diff": p1["sharpe"] - p0["sharpe"], "mean_daily_diff": float(d.mean()),
                          "p_boot": stationary_bootstrap_p(d, seed=_seed(name, n, cost)),
                          "both_better": bool(p1["sharpe"] > p0["sharpe"] and p1["max_dd"] > p0["max_dd"])}
        out["n"][str(n)] = res
    return out


def binom_p(k, n):
    return float(sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n) if n else 1.0


def combine(rows):
    out = {}
    for n in NS:
        for label in ("c20", "c50"):
            xs = [r["n"][str(n)][label] for r in rows]
            k = sum(x["both_better"] for x in xs)
            out[f"n{n}_{label}"] = {"coins": len(xs), "fisher_p": fisher_p([x["p_boot"] for x in xs]) if xs else 1.0,
                                    "median_sharpe_diff": float(np.median([x["sharpe_diff"] for x in xs])) if xs else 0.0,
                                    "n_sharpe_better": sum(x["sharpe_diff"] > 0 for x in xs),
                                    "n_both_better": k, "sign_p": binom_p(k, len(xs))}
    return out


def decide(comb):
    a, b = comb["n150_c20"], comb["n150_c50"]
    ok = bool(a["fisher_p"] < 0.05 and a["median_sharpe_diff"] > 0 and b["median_sharpe_diff"] > 0)
    return {"adopt": ok, "fisher_p_n150_c20": a["fisher_p"], "median_sharpe_diff_c20": a["median_sharpe_diff"],
            "median_sharpe_diff_c50": b["median_sharpe_diff"],
            "rule": "Fisher p < 0.05 (n=150, 0.2%) AND median Sharpe diff > 0 at 0.2% AND at 0.5%"}


def main():
    included, excluded = [], []
    for c in COINS:
        CD.download(c)
        df = CD.load(c)
        if len(df) < MIN_BARS:
            excluded.append({"asset": c, "bars": len(df), "first": str(df.index[0].date()),
                             "reason": f"fewer than {MIN_BARS} daily bars (3 years after the 150-day warm-up)"})
            print(f"EXCLUDED {c}: {len(df)} bars from {df.index[0].date()}")
        else:
            included.append((c, df))
    rows = [compare(c, df) for c, df in included]
    desc = [compare(c, CD.load(c)) for c in DESCRIPTIVE]
    comb = combine(rows)
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan": PLAN, "cost_per_side": COST / 1e4,
           "stress_per_side": STRESS / 1e4, "n_boot": N_BOOT, "block": BLOCK, "coins": rows, "descriptive": desc,
           "excluded": excluded, "combined": comb, "decision": decide(comb)}
    import crypto10_lab
    out["lab"] = crypto10_lab.build(rows, dict(included), desc, CD.load("BTC-USD"), out["decision"])
    OUT.write_text(json.dumps(out, default=float))
    print(f"wrote {OUT}")
    report(out)


def report(out):
    for n in NS:
        for label, nm in (("c20", "0.2%"), ("c50", "0.5%")):
            print(f"\nn={n} cost {nm}   Sharpe no/skip | MaxDD no/skip | Total% no/skip | trades/yr no/skip | boot p")
            for r in out["coins"] + out["descriptive"]:
                x = r["n"][str(n)][label]
                a, b = x["noskip"], x["skip"]
                tag = " (seen)" if r in out["descriptive"] else ""
                print(f"  {r['asset']:9s} {a['sharpe']:5.2f} {b['sharpe']:5.2f} | {a['max_dd']*100:6.1f} {b['max_dd']*100:6.1f} | "
                      f"{a['total_return']*100:9.0f} {b['total_return']*100:9.0f} | {a['trades_per_year']:5.1f} {b['trades_per_year']:5.1f} | {x['p_boot']:.3f}{tag}")
    print("\nCOMBINED", json.dumps(out["combined"], indent=1))
    print("DECISION", out["decision"])


if __name__ == "__main__":
    main()
