"""Inputs of the evening briefing: backtest daily-return series (test years) and what the research agents did today."""
from __future__ import annotations

import json
import sys
from datetime import datetime

import numpy as np

from report import NY, ROOT


def longterm_backtest():
    """Equal-weight mix of the 5 daily strategies (build_lab.BOOK in LAB_DAILY, chosen params), daily returns 2021+."""
    import pandas as pd
    import build_lab as B
    import data
    import ftmo_lab as FL
    import intraday as I
    from strategies import positions
    results = json.loads((ROOT / "results" / "results.json").read_text())["results"]
    cp = ROOT / "results" / "confirmation.json"
    conf = json.loads(cp.read_text()) if cp.exists() else None
    ctx_raw, cache, parts, cal = data.load_context(), {}, [], None
    for bk in B.BOOK:
        if bk["code"] not in B.LAB_DAILY:
            continue
        st = B.BY_ID[bk["id"]]
        df, ctx, cfg = B.build_market(bk["market"], ctx_raw, cache)
        v = B.verdict_for(bk["id"], bk["market"], results, conf)
        p = next(g for g in st["grid"] if json.loads(json.dumps(g)) == v["params"])
        pos = np.clip(np.nan_to_num(positions(st, df, ctx, p).values.astype(float)), 0, 1)
        fin, low = FL.daily_paths(df, pos, cfg["cost_bps"])
        parts.append((df.index.tz_localize(None) if df.index.tz is not None else df.index, fin, low))
    spy = B.build_market("SPY", ctx_raw, cache)[0].index
    spy = spy.tz_localize(None) if spy.tz is not None else spy
    cal = spy[spy >= pd.Timestamp(I.OOS_START)]
    return sum(FL._onto(d, f, lo, cal)[0] for d, f, lo in parts) / len(parts)


def intraday_backtest():
    """Noise-band breakout, SPY 50% + QQQ 50% (params from results/intraday.json), daily returns 2021+."""
    import ftmo
    import intraday as I
    rows = json.loads((ROOT / "results" / "intraday.json").read_text())["results"]
    out = []
    for sym in ("SPY", "QQQ"):
        D = I.Days(sym)
        p = next(r["params"] for r in rows if r["strategy"] == "id_noise_breakout" and r["asset"] == sym)
        f, _ = ftmo.day_paths(D, I.noise_breakout(D, **p))
        out.append(f[int(D.dates.searchsorted(np.datetime64(I.OOS_START))):])
    return 0.5 * out[0] + 0.5 * out[1]


def safe_bt(fn):
    try:
        return fn()
    except Exception as ex:
        print(f"backtest series failed: {type(ex).__name__}: {str(ex)[:100]}", file=sys.stderr)
        return None


def research_today(path, day):
    """{'tested','scored','best','holdout'} for journal records stamped with New York date `day`."""
    out = {"tested": 0, "scored": 0, "best": None, "holdout": 0}
    try:
        lines = open(path).read().splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            r = json.loads(ln)
            ts = datetime.fromisoformat(r["ts"])
        except (ValueError, KeyError):
            continue
        if ts.astimezone(NY).date() != day:
            continue
        out["tested"] += 1
        if r.get("status") in ("scored", "promoted"):
            out["scored"] += 1
            out["best"] = r["reward"] if out["best"] is None else max(out["best"], r["reward"])
        out["holdout"] += bool(r.get("holdout"))
    return out


def research_line(label, s):
    if not s["tested"]:
        return f"{label}: no ideas tested today"
    best = "n/a" if s["best"] is None else f"{s['best']:.3f}"
    return f"{label}: {s['tested']} tested, {s['scored']} scored, best score {best}, {s['holdout']} held-out shots"
