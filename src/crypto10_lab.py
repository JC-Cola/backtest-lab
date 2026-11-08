"""Strategy Lab payload for crypto study 10 (weekend-entry skip on the trend filter), shown on BTC-USD for viewing only:
the four settings {n 50, 150} x {weekend skip off, on}. The verdict is the study's pooled decision on untouched coins."""
from __future__ import annotations

import numpy as np

import crypto9_base as C
import crypto10 as S
from engine import extract_trades, returns_np

STRATEGY = {"id": "c10_weekend", "name": "Crypto Trend + Weekend Skip",
            "source": "Study 9 observation, tested on untouched coins in study 10",
            "plain": "The crypto trend filter (long above its average, else cash) that does not open new positions on weekends."}


def _r5(a):
    return [round(float(x), 5) for x in a]


def build(rows, dfs, desc, btc, decision):
    import ftmo_lab
    sl = slice(int(btc.index.searchsorted(__import__("pandas").Timestamp(C.IS_START))), len(btc))
    o, c = btc.Open.values.astype(float)[sl], btc.Close.values.astype(float)[sl]
    combos = []
    for n in S.NS:
        for wk in (False, True):
            s = np.clip(np.nan_to_num(S.positions(btc, n, wk)[sl]), 0, 1)
            t = extract_trades(o, c, s, C.COST)
            combos.append({"params": {"n": n, "weekend": wk}, "r": _r5(returns_np(o, c, s, C.COST)),
                           "trades": [[int(e), round(float(x), 5)] for e, x in zip(t["entry"], t["ret"])],
                           "pos": [int(round(x * 100)) for x in s]})
    chosen = next(i for i, k in enumerate(combos) if k["params"] == {"n": 150, "weekend": True})
    s150 = np.clip(np.nan_to_num(S.positions(btc, 150, True)[sl]), 0, 1)
    ftab = ftmo_lab.daily_table(btc.iloc[sl], s150, C.COST, C.OOS_START)
    mk = {"dates": [str(d.date()) for d in btc.index[sl]], "bench": _r5(returns_np(o, c, np.ones(len(o)), C.COST)),
          "oos_start": C.OOS_START, "is_end": C.IS_END, "ann": C.ANN, "cost_bps": C.COST}
    ok = decision["adopt"]
    per_year = float(desc[0]["n"]["150"]["c20"]["skip"]["trades_per_year"])
    return {"strategy": STRATEGY, "markets": {"BTC-USD-s10": mk},
            "entries": [{"sid": "c10_weekend", "market": "BTC-USD-s10", "asset": "BTC-USD", "combos": combos,
                         "params": {"n": 150, "weekend": True}, "ftmo": ftab,
                         "verdict": {"verdict": "PASS" if ok else "FAIL", "p": decision["fisher_p_n150_c20"], "q": None,
                                     "lockbox": None, "pool": len(rows), "params": {"n": 150, "weekend": True}, "cost2x": None},
                         "evidence": {"fresh": ok, "lockbox": False, "beat": round(1 - decision["fisher_p_n150_c20"], 4),
                                      "per_year": round(per_year, 1), "few": bool(per_year < 5), "tested": True,
                                      "score": (2 if ok else 0) - (1 if per_year < 5 else 0)}}]}
