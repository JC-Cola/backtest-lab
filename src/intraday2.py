"""Intraday study 2 (INTRADAY_PLAN_2.md): many symbols, 2019 on; tune 2019-2021, test 2022 on."""
import json
import sys
from datetime import datetime, timezone

import numpy as np

import intraday as I
from run import fisher_p
from stats import bh_qvalues

I.IS_END, I.OOS_START = "2021-12-31", "2022-01-01"
DISC = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "XOM", "UNH", "V", "HD", "GLD", "TLT", "USO"]
LOCK = ["AVGO", "COST", "LLY", "WMT", "BAC", "CVX", "PG", "KO", "MA", "ADBE", "NFLX", "AMD", "SLV", "IEF", "UNG"]
STRATS = [s for s in I.STRATEGIES if s["coin"]]
OUT = I.ROOT / "results" / "intraday2.json"


def run(symbols):
    rows = []
    for sym in symbols:
        D = I.Days(sym)
        for st in STRATS:
            r = I.evaluate(st, sym, D)
            r.pop("_t", None)
            rows.append(r)
        print(f"{sym}: " + " ".join(f"{r['strategy'][3:9]} {r['oos']['sharpe']:+.2f}/p{r['coin_flip']['p_value']:.2f}"
                                    for r in rows[-len(STRATS):] if r["coin_flip"]), flush=True)
    return rows


if __name__ == "__main__":
    rows = run(DISC)
    for r, q in zip(rows, bh_qvalues([r["coin_flip"]["p_value"] for r in rows])):
        r["coin_flip"]["q_value"] = float(q)
        r["candidate"] = bool(r["coin_flip"]["p_value"] < 0.05 and q < 0.10 and r["oos"]["sharpe"] > 0 and r["oos_cost2x_sharpe"] > 0)
    cands = [r for r in rows if r["candidate"]]
    print(f"\n{len(rows)} tests; candidates: {[(r['strategy'], r['asset']) for r in cands] or 'none'}")
    lock = {}
    if cands and "--lockbox" in sys.argv:
        lrows = run(LOCK)
        for sid in {r["strategy"] for r in cands}:
            ls = [r for r in lrows if r["strategy"] == sid]
            fp = fisher_p([r["coin_flip"]["p_value"] for r in ls])
            med = float(np.median([r["oos"]["sharpe"] for r in ls]))
            lock[sid] = {"fisher_p": fp, "median_oos_sharpe": med, "confirmed": bool(fp < 0.05 and med > 0),
                         "markets": [{"asset": r["asset"], "p": r["coin_flip"]["p_value"], "oos_sharpe": r["oos"]["sharpe"]} for r in ls]}
            print(f"lockbox {sid}: Fisher p={fp:.3f} median Sharpe={med:.2f} -> {'CONFIRMED' if lock[sid]['confirmed'] else 'not confirmed'}")
    OUT.write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan": "INTRADAY_PLAN_2.md",
                               "discovery": DISC, "lockbox_symbols": LOCK, "results": rows, "lockbox": lock}, default=float))
    print(f"wrote {OUT}")
