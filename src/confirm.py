"""Pre-registered confirmation test (see CONFIRMATION.md). Fixed candidates, fixed parameters,
untouched data, one run. Writes results/confirmation.json."""
from __future__ import annotations

import json
import math
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import data
from engine import extract_trades, returns_np
from run import N_SIMS, SEED, clean, fisher_p
from stats import cagr_np, coin_flip, sharpe_np
from strategies import STRATEGIES, positions

OUT = Path(__file__).resolve().parent.parent / "results" / "confirmation.json"
COST = 3
CANDIDATES = {
    "williams_r": {"n": 2, "trend": False},
    "ibs": {"a": 0.2, "b": 0.7},
    "qp_january_barometer": {"rule": "first5"},
}
US_PRE = {"SPY": "2004-12-31", "DIA": "2004-12-31", "QQQ": "2004-12-31"}
INTL = ["^GDAXI", "^FTSE", "^N225", "^HSI", "^AXJO", "^GSPTSE", "^FCHI", "^SSMI", "^AEX", "^KS11"]
ALPHA = 0.05 / len(CANDIDATES)


def quality_start(df) -> pd.Timestamp:
    """1 Jan of the first year from which every later FULL calendar year has >= 98% valid bars."""
    ok = (df.High >= df[["Open", "Close"]].max(axis=1)) & (df.Low <= df[["Open", "Close"]].min(axis=1)) & (df.High > df.Low)
    yr = ok.groupby(df.index.year).mean()
    full = yr.index[:-1]  # last year is partial
    good = yr[full] >= 0.98
    for y in full:
        if good.loc[y:].all():
            return pd.Timestamp(f"{y}-01-01")
    return pd.Timestamp(f"{yr.index[-1]}-01-01")


def markets():
    out = []
    for t, end in US_PRE.items():
        df = data.load(t).loc[:end]
        out.append((t + " pre-2005", df))
    for t in INTL:
        df = data.load(t)
        out.append((t, df.loc[quality_start(df):]))
    return out


def run_one(st, params, name, df):
    s = np.clip(np.nan_to_num(positions(st, df, None, params).values.astype(float)), -1, 1)
    o, c = df.Open.values, df.Close.values
    r = returns_np(o, c, s, COST)
    tr = extract_trades(o, c, s, COST)
    exposure = float((np.abs(s) > 0).mean())
    cf = None
    if exposure <= 0.95 and len(tr["ret"]):
        cf = coin_flip(o, tr, COST, N_SIMS, SEED + zlib.crc32(f"confirm{st['id']}{name}".encode()) % 100000)
    bh = returns_np(o, c, np.ones(len(c)), COST)
    return {"market": name, "start": str(df.index[0].date()), "end": str(df.index[-1].date()), "bars": len(df),
            "p_value": cf["p_value"] if cf else 1.0, "sharpe": sharpe_np(r, 252), "cagr": cagr_np(r, 252),
            "buy_hold_sharpe": sharpe_np(bh, 252), "buy_hold_cagr": cagr_np(bh, 252),
            "n_trades": int(len(tr["ret"])), "exposure": exposure}


def main():
    by_id = {s["id"]: s for s in STRATEGIES}
    mk = markets()
    res = {}
    for sid, params in CANDIDATES.items():
        rows = [run_one(by_id[sid], params, n, df) for n, df in mk]
        ps = [r["p_value"] for r in rows]
        fp = fisher_p(ps)
        med = float(np.median([r["sharpe"] for r in rows]))
        n10 = int(sum(p < 0.10 for p in ps))
        ok = fp < ALPHA and med > 0 and n10 >= math.ceil(len(rows) / 2)
        res[sid] = {"params": params, "markets": rows, "fisher_p": fp, "median_sharpe": med,
                    "markets_p_below_0.10": n10, "n_markets": len(rows), "confirmed": bool(ok)}
        print(f"\n{sid} {params}")
        for r in rows:
            print(f"  {r['market']:16s} {r['start']}..{r['end']}  p={r['p_value']:.3f}  Sh={r['sharpe']:5.2f} "
                  f"(hold {r['buy_hold_sharpe']:5.2f})  CAGR={r['cagr']:6.1%} (hold {r['buy_hold_cagr']:6.1%})  trades={r['n_trades']}")
        print(f"  => Fisher p={fp:.2e} (need < {ALPHA:.4f})  median Sharpe={med:.2f}  "
              f"markets p<0.10: {n10}/{len(rows)}  CONFIRMED={ok}")
    OUT.write_text(json.dumps(clean({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                     "preregistration": "CONFIRMATION.md", "alpha_per_candidate": ALPHA,
                                     "candidates": res}), indent=1, allow_nan=False))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
