"""System B historical backtest: the SAME pipeline as system_dts.py, replayed day by day with no lookahead.

    python src/system_dts_backtest.py [--start 2024-01-01] [--procs 7] [--phase1-only]

Per session, at 10:15 New York, using only bars that were complete by then:
  scanner (vendored scan_orb, 15-min opening range) -> top 20 by dollars traded -> entry timer -> exit planner (stop = range
  midpoint, 2R, flat 15:55) -> time machine (10,000 replays; its history and its bar pool use ONLY sessions before that day)
  -> budget (0.5% of equity max loss, 25% cap) -> risk (trader-risk) -> shadow fills (next bar's open + 0.02% slippage a side
  + 0.01% fees a side) -> bar-by-bar stop / target / flat-by-15:55 on the 5-minute bars.
Phase 1 (parallel, equity-independent) computes plans and time-machine odds for every candidate once; phase 2 (sequential)
replays the ledger per variant: time-machine gate loose (verdict at least "close to break-even", the live default), strict
(verdict "leans toward the target"), and no gate. Also run for daily-scanner families (flag = bull_flag, oversold =
oversold / oversold_bounce) entered with the entry timer's "level" setup (flag: highest of the last 5 closes, oversold: the
prior close) and a 1% stop, 2R target.

Honest limits: the universe is today's 100 most liquid names (survivorship and look-back bias in the universe choice); news
gate is not applied (no historical headlines); 5-minute bars (not 1-minute) manage the position; sessions that are not full
78-bar days (half days) are skipped; prices are split/dividend adjusted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import dts_data
import dts_ledger as L
from dts_core import CFG, ROOT, scan_m, tm_m
from dts_gates import odds_gate, plan_trade, size_and_risk

OUT = ROOT / "results" / "system_dts_backtest.json"
CACHE = dts_data.DIR / "phase1.pkl"
T_SLOT = 9                     # 10:15 = the 10th bar (index 9); bars 0..8 are complete
ARR: dict = {}
UNIVERSE: list = []
COST_RT = 2 * L.SLIPPAGE + 2 * L.FEE_RATE
MODES = ("loose", "strict", "none")
FAMILIES = ("orb", "flag", "oversold")


# ---------------------------------------------------------------- data

def load_arrays(symbols):
    out = {}
    for s in symbols:
        if not (dts_data.DIR / f"{s}_5m.pkl").exists():
            continue
        df = dts_data.load(s)
        if df.empty:
            continue
        idx = df.index
        t = (idx.tz_convert("UTC").tz_localize(None) - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")
        dates = np.array([d.toordinal() for d in idx.date])
        starts = {}
        for i, d in enumerate(dates):
            starts.setdefault(int(d), [i, i])[1] = i + 1
        out[s] = {"t": t.to_numpy().astype("int64"), "o": df.open.to_numpy(float), "h": df.high.to_numpy(float),
                  "l": df.low.to_numpy(float), "c": df.close.to_numpy(float), "v": df.volume.to_numpy(float),
                  "days": starts}
    return out


def pick_universe(candidates):
    """Top 100 by median daily dollar volume over the last 250 sessions (+ SPY, QQQ). Fixed list, saved with the results."""
    dv = {}
    for s in candidates:
        if s in ("SPY", "QQQ") or s not in ARR:
            continue
        a = ARR[s]
        vals = [float((a["c"][i0:i1] * a["v"][i0:i1]).sum()) for d, (i0, i1) in sorted(a["days"].items())]
        dv[s] = float(np.median(vals[-250:]))
    top = sorted(dv, key=lambda k: -dv[k])[:100]
    return top + ["SPY", "QQQ"], dv


def full_days(a):
    return sorted(d for d, (i0, i1) in a["days"].items() if i1 - i0 == 78)


def series_upto(a, day_ord, n):
    i0, _ = a["days"][day_ord]
    sl = slice(i0, i0 + n)
    return {"t": a["t"][sl].tolist(), "o": a["o"][sl].tolist(), "h": a["h"][sl].tolist(), "l": a["l"][sl].tolist(),
            "c": a["c"][sl].tolist(), "v": a["v"][sl].tolist(), "meta": {}}


def chart_text(sym, day_ord, n=T_SLOT):
    """60 calendar days of 5-min bars before the day plus the day's own bars up to (not including) the bar at 10:15."""
    a = ARR[sym]
    i0, _ = a["days"][day_ord]
    lo_day = next((d for d in sorted(a["days"]) if d >= day_ord - 60), day_ord)
    lo = a["days"][lo_day][0]
    hi = i0 + n
    q = {k: a[k][lo:hi].tolist() for k in "ohlcv"}
    q = {"open": q["o"], "high": q["h"], "low": q["l"], "close": q["c"], "volume": q["v"]}
    res = {"meta": {"symbol": sym, "dataGranularity": "5m"}, "timestamp": a["t"][lo:hi].tolist(), "indicators": {"quote": [q]}}
    return json.dumps({"chart": {"result": [res], "error": None}})


def daily_closes(sym, day_ord):
    a = ARR[sym]
    ds = [d for d in sorted(a["days"]) if d < day_ord]
    return [(d, float(a["c"][a["days"][d][1] - 1]), float(a["v"][a["days"][d][0]:a["days"][d][1]].sum())) for d in ds[-90:]]


# ---------------------------------------------------------------- phase 1: one session (equity independent)

def level_candidates(day_ord, day, T_et):
    """Daily-scanner families: yesterday's setups from closes only, rank by yesterday's dollars traded."""
    s = scan_m()
    out = []
    for sym in UNIVERSE:
        if sym in ("SPY", "QQQ") or sym not in ARR:
            continue
        dc = daily_closes(sym, day_ord)
        if len(dc) < 30 or dc[-1][1] < CFG["min_price"] or dc[-1][2] < 1_000_000:
            continue
        closes = [c for _, c, _ in dc]
        tags, _ = s.find_setups(closes)
        fam = "flag" if "bull_flag" in tags else "oversold" if {"oversold", "oversold_bounce"} & set(tags) else None
        if fam:
            level = max(closes[-5:]) if fam == "flag" else closes[-1]
            out.append((fam, sym, round(level, 2), closes[-1] * dc[-1][2]))
    return out


def eval_candidate(fam, sym, day_ord, day, T_et, T_utc, level=None):
    a = ARR[sym]
    i0, _ = a["days"][day_ord]
    p0 = round(float(a["o"][i0 + T_SLOT]), 2)
    text = chart_text(sym, day_ord)
    rec = {"family": fam, "symbol": sym, "p0": p0, "date": day.isoformat()}
    if fam == "orb":
        plan, res = plan_trade(sym, text, T_et, T_utc, day, p0)
    else:
        plan, res = plan_level(sym, text, T_et, T_utc, day, p0, level)
    if plan is None:
        rec.update(stage="plan", reason=str(res)[:160])
        return rec
    odds, why = odds_gate(text, plan, T_utc)
    rec.update(stage="odds", plan=plan, odds=odds, why=why)
    if res.get("trigger"):
        rec["trigger"] = {"time": res["trigger"]["close_time"], "pct_over": res["trigger"]["pct_over"]}
    return rec


def plan_level(sym, text, T_et, T_utc, day, p0, level):
    import types
    from dts_core import entry_m, exit_m
    en, ex = entry_m(), exit_m()
    res = en.evaluate(en.parse_chart(text, sym), T_utc, "level", 15, "5m", level=level)
    if res["status"] != "triggered":
        return None, "entry timer: %s" % res["status"]
    k = int((T_et - dt.datetime.combine(day, scan_m().OPEN)).total_seconds() // 300) - 1
    ns = types.SimpleNamespace(symbol=sym, from_path=None, side="buy", entry=p0, setup="level", date=day.isoformat(),
                               time=ex.ampm(T_et.time()), entry_bar=k, interval="5m", or_high=None, or_low=None,
                               or_minutes=15, stop="pct:1", target="2R", flat_by=None, offline=None, now=None, config=None)
    try:
        plan, _ = ex.build_plan(ns, T_utc)
    except ex.PlanError as e:
        return None, "exit planner: %s" % e
    return plan, res


def eval_day(day_ord):
    s = scan_m()
    day = dt.date.fromordinal(day_ord)
    T_et = dt.datetime.combine(day, dt.time(10, 15))
    T_utc = s.eastern_to_utc(T_et)
    stage1 = {sym: series_upto(ARR[sym], day_ord, T_SLOT) for sym in UNIVERSE if sym in ARR and day_ord in ARR[sym]["days"]}
    hits, counts, _ = s.scan_orb(stage1, list(stage1), day, T_et, CFG["or_minutes"], CFG["min_price"], CFG["min_volume"])
    dv = {h["symbol"]: sum(b["v"] * b["c"] for b in s.session_bars(stage1[h["symbol"]], day, T_et)) for h in hits}
    cands = [("orb", h["symbol"], None, dv[h["symbol"]]) for h in hits]
    cands.sort(key=lambda x: -x[3])
    cands = cands[:CFG["max_eval"]]
    lv = sorted(level_candidates(day_ord, day, T_et), key=lambda x: -x[3])
    for fam in ("flag", "oversold"):
        cands += [(f, sy, lvl, d) for f, sy, lvl, d in lv if f == fam][:CFG["max_eval"]]
    out = []
    for fam, sym, level, _ in cands:
        try:
            out.append(eval_candidate(fam, sym, day_ord, day, T_et, T_utc, level))
        except Exception as e:  # noqa: BLE001 - one bad symbol-day must not stop the run
            out.append({"family": fam, "symbol": sym, "date": day.isoformat(), "stage": "error", "reason": "%s: %s" % (type(e).__name__, e)})
    return day_ord, out, counts["hits"]


def init_worker():
    tm = tm_m()
    tm.BAND_PATHS, tm.SAMPLE_PATHS = 1, 0       # fan-chart extras only: the odds (same seed, same draws) are unchanged


# ---------------------------------------------------------------- phase 2: ledger replay

def passes(rec, mode):
    if rec.get("stage") != "odds":
        return False
    if mode == "none":
        return True
    if rec["why"]:
        return False
    return mode == "loose" or (rec["odds"]["lean"] or 0) >= 0.05


def run_variant(days, by_day, family, mode):
    s = scan_m()
    st = L.new_state()
    eq_curve, trades = [], []
    for d in days:
        day = dt.date.fromordinal(d)
        T_et = dt.datetime.combine(day, dt.time(10, 15))
        T_utc = s.eastern_to_utc(T_et)
        for rec in by_day.get(d, []):
            if rec["family"] != family or not passes(rec, mode) or len(st["positions"]) >= CFG["max_concurrent"]:
                continue
            qty, note = size_and_risk(rec["symbol"], rec["plan"], rec["p0"], st, T_et, T_utc, L.equity_of(st))
            if qty <= 0:
                continue
            pos = L.open_position(st, rec["symbol"], qty, rec["p0"], T_et, T_utc, rec["plan"], {"odds": rec["odds"]})
            a = ARR[rec["symbol"]]
            i0, i1 = a["days"][d]
            bars = [{"start": dt.datetime.combine(day, dt.time(9, 30)) + dt.timedelta(minutes=5 * j),
                     "end": dt.datetime.combine(day, dt.time(9, 30)) + dt.timedelta(minutes=5 * (j + 1)),
                     "o": float(a["o"][i0 + j]), "h": float(a["h"][i0 + j]), "l": float(a["l"][i0 + j]),
                     "c": float(a["c"][i0 + j])} for j in range(T_SLOT, i1 - i0)]
            close_et, flat_et = dt.datetime.combine(day, dt.time(16, 0)), dt.datetime.combine(day, dt.time(15, 55))
            ex, _, _ = L.walk_position(pos, bars, close_et, close_et, flat_et)
            tr = L.close_position(st, pos, ex["ref"], ex["at"], s.eastern_to_utc(ex["at"]), ex["reason"])
            tr["family"] = family
            trades.append(tr)
        eq_curve.append((day.isoformat(), round(L.equity_of(st), 2)))
    return trades, eq_curve


def metrics(trades, eq_curve, start_eq=L.START_EQUITY):
    eq = np.array([start_eq] + [e for _, e in eq_curve])
    rets = np.diff(eq) / eq[:-1]
    peak = np.maximum.accumulate(eq)
    dd = (eq / peak - 1).min()
    by_day: dict = {}
    for t in trades:
        by_day[t["date"]] = by_day.get(t["date"], 0.0) + t["pnl"]
    n_days = len(eq_curve)
    wins = [t for t in trades if t["pnl"] > 0]
    worst = min(by_day.items(), key=lambda kv: kv[1]) if by_day else (None, 0.0)
    holds = [(dt.datetime.strptime(t["exit_time"], "%Y-%m-%d %H:%M") - dt.datetime.strptime(t["entry_time"], "%Y-%m-%d %H:%M")).seconds / 60 for t in trades]
    return {"trades": len(trades), "trading_days": n_days, "trades_per_day": round(len(trades) / n_days, 3) if n_days else 0,
            "days_with_trades": len(by_day), "final_equity": round(float(eq[-1]), 2),
            "total_return_pct": round(100 * (eq[-1] / start_eq - 1), 2), "total_pnl": round(float(eq[-1] - start_eq), 2),
            "sharpe": round(float(rets.mean() / rets.std(ddof=1) * np.sqrt(252)), 2) if rets.std(ddof=1) > 0 else 0.0,
            "max_drawdown_pct": round(100 * float(dd), 2), "worst_day_usd": round(worst[1], 2), "worst_day": worst[0],
            "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
            "avg_R": round(float(np.mean([t["r"] for t in trades if t["r"] is not None])), 3) if trades else 0.0,
            "avg_pnl": round(float(np.mean([t["pnl"] for t in trades])), 2) if trades else 0.0,
            "avg_hold_min": round(float(np.mean(holds)), 1) if holds else 0.0,
            "by_reason": {r: sum(1 for t in trades if t["reason"].split(" ")[0] == r) for r in ("stop", "target", "flat")}}


# ---------------------------------------------------------------- benchmarks

def coin_flip(trades, days, n=2000, seed=7):
    """n random-timing traders: each real trade's symbol, notional and holding time, but a random full session and a random
    start bar; long, same costs. Compares the total P&L with the real one."""
    rng = np.random.default_rng(seed)
    cache, tot = {}, np.zeros(n)
    for t in trades:
        sym = t["symbol"]
        if sym not in cache:
            a = ARR[sym]
            ds = [d for d in days if d in a["days"] and a["days"][d][1] - a["days"][d][0] == 78]
            o = np.stack([a["o"][a["days"][d][0]:a["days"][d][1]] for d in ds])
            c = np.stack([a["c"][a["days"][d][0]:a["days"][d][1]] for d in ds])
            cache[sym] = (o, c)
        o, c = cache[sym]
        hold = max(1, int(round((dt.datetime.strptime(t["exit_time"], "%Y-%m-%d %H:%M") - dt.datetime.strptime(t["entry_time"], "%Y-%m-%d %H:%M")).seconds / 300)))
        di = rng.integers(0, len(o), n)
        si = rng.integers(0, 78 - hold + 1, n)
        ret = c[di, si + hold - 1] / o[di, si] - 1 - COST_RT
        tot += t["qty"] * t["entry_ref"] * ret
    real = sum(t["pnl"] for t in trades)
    return {"traders": n, "trades_each": len(trades), "real_total_pnl": round(real, 2), "random_mean": round(float(tot.mean()), 2),
            "random_std": round(float(tot.std()), 2), "random_p5": round(float(np.percentile(tot, 5)), 2),
            "random_p95": round(float(np.percentile(tot, 95)), 2),
            "share_of_random_traders_beaten_pct": round(100 * float((tot < real).mean()), 1)}


def buy_hold(sym, days):
    a = ARR[sym]
    ds = [d for d in days if d in a["days"]]
    cl = np.array([a["c"][a["days"][d][1] - 1] for d in ds])
    first_open = float(a["o"][a["days"][ds[0]][0]])
    eq = L.START_EQUITY * np.concatenate([[1.0], cl / first_open])
    curve = [(dt.date.fromordinal(d).isoformat(), round(float(e), 2)) for d, e in zip(ds, eq[1:])]
    return metrics([], curve)


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--procs", type=int, default=7)
    ap.add_argument("--phase1-only", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="reuse data/dts/phase1.pkl")
    a = ap.parse_args(argv)
    global ARR, UNIVERSE
    t0 = time.time()
    ARR = load_arrays(dts_data.CANDIDATES)
    UNIVERSE, dvs = pick_universe(dts_data.CANDIDATES)
    ARR = {k: v for k, v in ARR.items() if k in UNIVERSE}
    spy = ARR["SPY"]
    days = [d for d in full_days(spy) if d >= dt.date.fromisoformat(a.start).toordinal() and (not a.end or d <= dt.date.fromisoformat(a.end).toordinal())]
    print("universe %d symbols, %d sessions %s..%s" % (len(UNIVERSE), len(days), dt.date.fromordinal(days[0]), dt.date.fromordinal(days[-1])), flush=True)
    if a.reuse and CACHE.exists():
        by_day = pd.read_pickle(CACHE)
    else:
        by_day, done = {}, 0
        with mp.get_context("fork").Pool(a.procs, initializer=init_worker) as pool:
            for d, recs, nhits in pool.imap_unordered(eval_day, days):
                by_day[d] = recs
                done += 1
                if done % 20 == 0:
                    print("  phase 1: %d/%d days, %.0fs" % (done, len(days), time.time() - t0), flush=True)
        pd.to_pickle(by_day, CACHE)
    if a.phase1_only:
        return 0
    results = {}
    for fam in FAMILIES:
        for mode in MODES:
            trades, curve = run_variant(days, by_day, fam, mode)
            results["%s/%s" % (fam, mode)] = {"metrics": metrics(trades, curve), "equity_curve": curve, "trades": trades}
            m = results["%s/%s" % (fam, mode)]["metrics"]
            print("%-16s trades %4d  total %+8.2f  sharpe %5.2f  maxDD %6.2f%%  win %5.1f%%  avgR %+.3f" % (
                fam + "/" + mode, m["trades"], m["total_pnl"], m["sharpe"], m["max_drawdown_pct"], m["win_rate_pct"], m["avg_R"]), flush=True)
    flips = {k: coin_flip(v["trades"], days) for k, v in results.items() if v["trades"] and k.startswith("orb/")}
    bh = {s: buy_hold(s, days) for s in ("SPY", "QQQ")}
    gate_counts = {}
    for recs in by_day.values():
        for r in recs:
            key = "%s: %s" % (r["family"], r.get("stage") if r.get("stage") != "odds" else ("odds pass" if not r["why"] else "odds fail"))
            gate_counts[key] = gate_counts.get(key, 0) + 1
    doc = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "period": [dt.date.fromordinal(days[0]).isoformat(), dt.date.fromordinal(days[-1]).isoformat()],
           "sessions": len(days), "universe": UNIVERSE, "universe_note": "100 most liquid of %d candidates by median daily dollar volume over the last 250 sessions, + SPY, QQQ; "
           "today's liquidity ranking applied to the past (survivorship / look-ahead bias in the universe)" % len(dts_data.CANDIDATES),
           "config": {k: (sorted(v) if isinstance(v, set) else v) for k, v in CFG.items()}, "cost_model": {"slippage_per_side": L.SLIPPAGE, "fee_per_side": L.FEE_RATE},
           "variants": results, "coin_flip": flips, "buy_and_hold": bh, "candidate_counts": gate_counts,
           "limits": "no news gate; 5-minute bars; full sessions only; adjusted prices; see module docstring"}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(doc))
    print("wrote %s, %.0fs" % (OUT, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
