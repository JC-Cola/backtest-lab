"""Study 18 (EARNINGS_PLAN_18.md): post-earnings-announcement drift on the news-scan universe. DESCRIPTIVE ONLY: the Finnhub
free plan gives 4 quarters of surprises per symbol, so there are no tune/test years.

    python src/earnings18.py [--limit N]     # needs FINNHUB_API_KEY and Alpaca keys; writes results/earnings18.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import finnhub_client as FH
from stats import bh_qvalues

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "earnings18.json"
COST_SIDE = 0.0005
HOLDS = (5, 20, 60)
MIN_EST = 0.05
WIN_FROM, WIN_TO = 10, 75          # days after the period end in which the reaction day is searched
RELVOL_MIN, RELVOL_DAYS = 2.0, 20
N_SIMS = 10000
LOCK_PERIOD = "2026-09-30"


def reaction_day(bars, period, relvol_min=RELVOL_MIN):
    """Index of the first session in [period+10d, period+75d] with volume >= relvol_min x mean of the prior 20 sessions."""
    p = date.fromisoformat(str(period)[:10])
    lo, hi = str(p + timedelta(days=WIN_FROM)), str(p + timedelta(days=WIN_TO))
    for i, b in enumerate(bars):
        if i < RELVOL_DAYS or not (lo <= b[0] <= hi):
            continue
        prior = [x[3] for x in bars[i - RELVOL_DAYS:i] if len(x) > 3 and x[3]]
        if len(prior) >= 10 and b[3] and b[3] >= relvol_min * statistics.fmean(prior):
            return i
    return None


def build_events(surprises, bars_by_sym):
    """surprises {sym: [rows]} -> (events, dropped). event = {symbol, period, surprise, react (date), i (bar index of R)}."""
    ev, dropped = [], {"no_surprise": 0, "small_estimate": 0, "no_reaction_day": 0}
    for sym, rows in surprises.items():
        bars = bars_by_sym.get(sym) or []
        for r in rows or []:
            if r.get("surprisePercent") is None or r.get("period") is None:
                dropped["no_surprise"] += 1
            elif abs(float(r.get("estimate") or 0)) < MIN_EST:
                dropped["small_estimate"] += 1
            else:
                i = reaction_day(bars, r["period"])
                if i is None:
                    dropped["no_reaction_day"] += 1
                else:
                    ev.append({"symbol": sym, "period": str(r["period"])[:10], "surprise": float(r["surprisePercent"]),
                               "react": bars[i][0], "i": i})
    return ev, dropped


def window_return(bars, i, h):
    """Open-to-open return entering at the open of bar i+1, exiting h sessions later; None when not realised / bad data."""
    a, b = i + 1, i + 1 + h
    if b >= len(bars) or not bars[a][1] or not bars[b][1]:
        return None
    return bars[b][1] / bars[a][1] - 1


def returns_for(ev, bars_by_sym, spy, h):
    """Per event: (net return of a long, SPY return over the same window) or None. Dates of SPY are matched by date."""
    bars = bars_by_sym[ev["symbol"]]
    r = window_return(bars, ev["i"], h)
    if r is None:
        return None
    a, b = bars[ev["i"] + 1][0], bars[ev["i"] + 1 + h][0]
    s = {x[0]: x[1] for x in spy}
    if a not in s or b not in s or not s[a]:
        return None
    return r, s[b] / s[a] - 1


def side_stats(rows, side):
    """rows [(raw return, spy return)] -> stats for going long (side=1) or short (side=-1), 0.10% round-trip cost."""
    net = np.array([side * r - 2 * COST_SIDE for r, _ in rows])
    exc = np.array([side * (r - s) - 2 * COST_SIDE for r, s in rows])
    n = len(net)
    if n < 2:
        return {"n": n}
    sd = exc.std(ddof=1)
    return {"n": n, "mean_net": round(float(net.mean()), 5), "mean_excess": round(float(exc.mean()), 5),
            "hit": round(float((net > 0).mean()), 3), "t_excess": round(float(exc.mean() / sd * np.sqrt(n)), 2) if sd > 0 else None}


def coin_flip_p(pool, n, side, observed, n_sims=N_SIMS, seed=0):
    """P(random n events from the pool do at least as well as `observed` mean excess), same side. pool [(r, spy)]."""
    if n < 2 or len(pool) < n or observed is None:
        return None
    rng = np.random.default_rng(seed)
    exc = np.array([side * (r - s) - 2 * COST_SIDE for r, s in pool])
    means = np.array([exc[rng.choice(len(exc), n, replace=False)].mean() for _ in range(n_sims)])
    return round((1 + int((means >= observed - 1e-12).sum())) / (n_sims + 1), 4)


def analyse(events, bars_by_sym, spy, n_sims=N_SIMS):
    """The whole descriptive table. Decile cut-offs come from the surprises only."""
    if len(events) < 20:
        return {"status": "too few events", "n_events": len(events)}
    sur = np.array([e["surprise"] for e in events])
    lo, hi = np.percentile(sur, 10), np.percentile(sur, 90)
    main = [e for e in events if e["period"] != LOCK_PERIOD]
    lock = [e for e in events if e["period"] == LOCK_PERIOD]
    dates = sorted(e["react"] for e in main)
    mid = dates[len(dates) // 2] if dates else None
    out = {"cutoffs": {"p10": round(float(lo), 3), "p90": round(float(hi), 3)}, "n_events": len(events), "n_main": len(main),
           "n_lockbox": len(lock), "holds": {}, "halves": {}, "lockbox": {}}
    tests = []
    for h in HOLDS:
        def rows_of(evs):
            return [(e, returns_for(e, bars_by_sym, spy, h)) for e in evs]
        allr = [x for e, x in rows_of(main) if x]
        longs = [x for e, x in rows_of(main) if x and e["surprise"] >= hi]
        shorts = [x for e, x in rows_of(main) if x and e["surprise"] <= lo]
        res = {"long": side_stats(longs, 1), "short": side_stats(shorts, -1), "unrealised_or_missing": len(main) - len(allr)}
        for name, side, rr in (("long", 1, longs), ("short", -1, shorts)):
            res[name]["coin_flip_p"] = coin_flip_p(allr, len(rr), side, res[name].get("mean_excess"), n_sims)
            tests.append((h, name, res[name]["coin_flip_p"]))
        res["long_minus_short_net"] = (None if "mean_net" not in res["long"] or "mean_net" not in res["short"]
                                       else round(res["long"]["mean_net"] - res["short"]["mean_net"], 5))
        out["holds"][str(h)] = res
        for tag, sel in (("early", lambda e: e["react"] < mid), ("late", lambda e: e["react"] >= mid)):
            part = [(e, x) for e, x in rows_of([e for e in main if sel(e)]) if x]
            out["halves"].setdefault(tag, {})[str(h)] = {
                "long": side_stats([x for e, x in part if e["surprise"] >= hi], 1),
                "short": side_stats([x for e, x in part if e["surprise"] <= lo], -1)}
        lp = [(e, x) for e, x in rows_of(lock) if x]
        out["lockbox"][str(h)] = {"long": side_stats([x for e, x in lp if e["surprise"] >= hi], 1),
                                  "short": side_stats([x for e, x in lp if e["surprise"] <= lo], -1)}
    ps = [p for _, _, p in tests]
    if all(p is not None for p in ps):
        for (h, name, _), q in zip(tests, bh_qvalues(ps)):
            out["holds"][str(h)][name]["bh_q"] = round(float(q), 4)
    out["verdict"] = verdict(out)
    return out


def verdict(out):
    """'suggestive' per the plan's rule, else 'no visible drift'. Always descriptive."""
    for name in ("long", "short"):
        good = 0
        for h in HOLDS:
            s = out["holds"].get(str(h), {}).get(name, {})
            if s.get("mean_excess", 0) > 0 and (s.get("coin_flip_p") or 1) < 0.05 and (s.get("bh_q") or 1) < 0.10:
                halves = [out["halves"][t][str(h)][name].get("mean_excess") for t in ("early", "late")]
                lock = out["lockbox"][str(h)][name].get("mean_excess")
                if all(x is not None and x > 0 for x in halves) and (lock is None or lock > 0):
                    good += 1
        if good >= 2:
            return "suggestive (descriptive only, not tradable evidence)"
    return "no visible drift (descriptive only)"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only the first N symbols (testing)")
    a = ap.parse_args(argv)
    if not FH.available():
        print("earnings18: FINNHUB_API_KEY not set", file=sys.stderr)
        return 1
    import news_scan
    import universe
    syms = universe.load(refresh=False)[0]
    syms = syms[:a.limit] if a.limit else syms
    surprises = {s: FH.earnings_surprises(s) for s in syms}
    start = date(2025, 9, 1)
    bars = news_scan.fetch_bars(sorted(set(syms) | {"SPY"}), start, date.today())
    spy = bars.pop("SPY", [])
    events, dropped = build_events(surprises, bars)
    res = analyse(events, bars, spy)
    res.update({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan": "EARNINGS_PLAN_18.md",
                "symbols": len(syms), "symbols_with_surprises": sum(1 for v in surprises.values() if v), "dropped": dropped,
                "note": "descriptive only: free-plan history is 4 quarters, no tune/test years"})
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(res.get("verdict") or res.get("status"), res["n_events"], "events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
