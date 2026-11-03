"""Intraday study 2, amendment 1: gap fade on the 15 untouched lockbox symbols (rule in INTRADAY_PLAN_2.md)."""
import json

import numpy as np

import intraday as I
import intraday2 as S2
from run import fisher_p

st = next(s for s in I.STRATEGIES if s["id"] == "id_gap_fade")
rows = []
for sym in S2.LOCK:
    r = I.evaluate(st, sym, I.Days(sym))
    r.pop("_t", None)
    rows.append(r)
    print(f"{sym:5s} params {r['params']}  test Sharpe {r['oos']['sharpe']:+.2f} (2x {r['oos_cost2x_sharpe']:+.2f})  "
          f"trades {r['oos']['trades']:4d}  p={r['coin_flip']['p_value']:.3f}", flush=True)
stocks = [r for r in rows if r["asset"] not in ("SLV", "IEF", "UNG")]
fp = fisher_p([r["coin_flip"]["p_value"] for r in stocks])
med = float(np.median([r["oos"]["sharpe"] for r in stocks]))
pos = sum(r["oos"]["sharpe"] > 0 for r in stocks)
verdict = bool(fp < 0.05 and med > 0)
print(f"\n12 lockbox stocks: Fisher p={fp:.4f}, median test Sharpe {med:+.2f}, positive on {pos}/12 -> "
      f"{'CONFIRMED' if verdict else 'NOT confirmed'}")
out = I.ROOT / "results" / "intraday2_gapfade_lockbox.json"
out.write_text(json.dumps({"rule": "INTRADAY_PLAN_2.md amendment 1", "fisher_p_stocks": fp, "median_sharpe_stocks": med,
                           "positive_stocks": pos, "confirmed": verdict, "results": rows}, default=float))
print(f"wrote {out}")
