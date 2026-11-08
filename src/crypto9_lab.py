"""Strategy Lab payload for crypto study 9: for each result that made money in its test years, every grid setting's daily
returns, trades and position size (the same shape build_lab.series() makes for the daily strategies) plus the chosen
setting's FTMO table. Stored in results/crypto9.json under "lab" so build_lab.py needs no price data."""
from __future__ import annotations

import numpy as np

import crypto9_base as C
import crypto9_rot as R
from engine import extract_trades, returns_np


def _r5(a):
    return [round(float(x), 5) for x in a]


def _single(row, coin, pos, b, st):
    sl = slice(coin.I.start, coin.n)
    o, c = coin.o[sl], coin.c[sl]
    combos = []
    for p, s in zip(st["grid"], pos):
        s = np.clip(np.nan_to_num(s[sl]), 0, 1)
        t = extract_trades(o, c, s, C.COST)
        combos.append({"params": p, "r": _r5(returns_np(o, c, s, C.COST)),
                       "trades": [[int(e), round(float(x), 5)] for e, x in zip(t["entry"], t["ret"])],
                       "pos": [int(round(x * 100)) for x in s]})
    import ftmo_lab
    ftmo = ftmo_lab.daily_table(coin.df.iloc[sl], np.clip(np.nan_to_num(pos[b][sl]), 0, 1), C.COST, C.OOS_START)
    mk = {"dates": [str(d.date()) for d in coin.dates[sl]], "bench": _r5(returns_np(o, c, np.ones(len(o)), C.COST)),
          "oos_start": C.OOS_START, "is_end": C.IS_END, "ann": C.ANN, "cost_bps": C.COST}
    return combos, ftmo, mk


def _rotation(row, P, Ws, b):
    import ftmo
    sl = slice(P.I.start, P.n)
    combos = []
    for p, W in zip(C.ROT_GRID, Ws):
        t = R.rot_trades(P, W, C.COST, sl)
        combos.append({"params": p, "r": _r5(R.rot_returns(P, W, C.COST, sl)),
                       "trades": [[int(e), round(float(x), 5)] for e, x in zip(t["entry"], t["ret"])],
                       "pos": [int(round(x * 100)) for x in np.abs(W[sl]).sum(axis=1)]})
    fin = np.array(combos[b]["r"])
    ftab = ftmo.table_from_paths(fin, np.minimum(fin, 0.0), int(P.dates[sl].searchsorted(np.datetime64(C.OOS_START))))
    bench = np.vstack([np.zeros(P.m), P.C[1:] / P.C[:-1] - 1])
    bench[~P.listed] = 0
    bench[1:][~P.listed[:-1]] = 0
    n_l = np.maximum(P.listed.sum(axis=1), 1)
    mk = {"dates": [str(d.date()) for d in P.dates[sl]], "bench": _r5((bench.sum(axis=1) / n_l)[sl]),
          "oos_start": C.OOS_START, "is_end": C.IS_END, "ann": C.ANN, "cost_bps": C.COST}
    return combos, ftab, mk


def build(rows, packs, strats, pool=None):
    markets, entries = {}, []
    pool = pool or sum(1 for r in rows if r.get("coin_flip"))
    for row in rows:
        if row["oos"]["total_return"] <= 0:       # the page shows only what made money in its test years
            continue
        sid, asset = row["strategy"], row["asset"]
        src, pos, b = packs[(sid, asset)]
        combos, ftab, mk = _rotation(row, src, pos, b) if sid == "c9_rotation" else _single(row, src, pos, b, strats[sid])
        key = f"{asset}-s9"
        markets[key] = mk
        cf, lb = row.get("coin_flip"), row.get("lockbox")
        lock, cand = bool(lb and lb.get("confirmed")), bool(row.get("candidate"))
        per_year = row["oos"]["trades_per_year"]
        verdict = "NOTEST" if cf is None else ("PASS" if cand and lock else ("WEAK" if cf["p_value"] < 0.05 else "FAIL"))
        entries.append({"sid": sid, "market": key, "asset": asset, "combos": combos, "params": row["params"], "ftmo": ftab,
                        "verdict": {"verdict": verdict, "p": cf["p_value"] if cf else None, "q": cf.get("q_value") if cf else None,
                                    "lockbox": lock if cand else None, "pool": pool, "params": row["params"], "cost2x": row["oos_cost2x_sharpe"]},
                        "evidence": {"fresh": lock, "lockbox": cand, "beat": round(1 - cf["p_value"], 4) if cf else 0.0,
                                     "per_year": round(per_year, 1), "few": bool(per_year < 5), "tested": cf is not None,
                                     "score": (2 if lock else 0) + (1 if cand else 0) - (1 if per_year < 5 else 0) - (0 if cf else 2)}})
    return {"markets": markets, "entries": entries}
