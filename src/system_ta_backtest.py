"""Historical backtest of System C (TradingAgents on the Gemini free API tier) with the shadow-ledger rules of system_ta.

    cd src && python system_ta_backtest.py [--tickers SPY,QQQ,NVDA] [--start D] [--end D] [--step 3] [--cutoff D]
                                           [--dry-run] [--report-only] [--trials 200]

Decisions come from TradingAgents' own backtest runner (tradingagents.backtest.run_backtest: point-in-time data, one
decision per ticker/date cell, resumable). Every decision is cached in data/ta_cache/ (its memory log), so an interrupted
run continues where it stopped; re-running with --report-only needs no model calls. Decisions become positions by the
same rules as the live shadow ledger (ta_ledger.py: entry at the next open, 20%/10% per ticker of $25,000, shorts allowed,
0.02% slippage + 0.01% fees per side) and are compared with buy-and-hold over the same dates and with random ratings drawn
with the same frequencies (coin flip).

CONTAMINATION: an LLM remembers market history up to its training cutoff. Dates on or before --cutoff are reported
separately as "possibly contaminated"; the "clean" result uses only decisions AFTER the cutoff. TradingAgents' catalogue
has no knowledge-cutoff data; the default is the conservative Google-documented date of the quick-tier model.
Output: results/system_ta_backtest.json.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ta_ledger as L

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "ta_cache"
OUT = ROOT / "results" / "system_ta_backtest.json"
RUN_ID = "system_c"
DEFAULT_CUTOFF = "2026-03-31"     # gemini-3.5-flash-lite model card: March 2026 (some domains Jan 2025); gemini-3.5-flash docs say Jan 2025
CALLS_PER_DECISION = 18           # measured: one real SPY decision on Gemini = 18 model calls, 183 s at the free-tier rate limits
MAX_DECISIONS = 300


def pick_dates(days, start, end, step):
    ds = [d for d in sorted(days) if str(start) <= d <= str(end)]
    return ds[::max(1, step)]


def plan(days, tickers, start, end, step, max_decisions=MAX_DECISIONS):
    """Dates to sample; the step grows until tickers x dates fits `max_decisions`."""
    step = max(1, step)
    while True:
        dates = pick_dates(days, start, end, step)
        if len(dates) * len(tickers) <= max_decisions or len(dates) <= 1:
            return dates, step
        step += 1


def collect(tickers, dates, cache=CACHE, chunk=3, config=None, runner=None, loader=None, log=print, budget=None):
    """Decide every missing (ticker, date) cell, `chunk` dates at a time, and return the cached decisions
    [{id,date,symbol,rating,rationale,horizon,target,source}]. runner/loader are injectable for tests."""
    if runner is None or loader is None:
        from tradingagents.backtest import run_backtest
        from tradingagents.memory import TradingMemoryLog
        import system_ta
        system_ta.install_rate_limits()   # free-tier requests per minute are per project and model
        cfg = {**(config or system_ta.ta_config()), "results_dir": str(cache)}
        runner = lambda tk, ds: run_backtest(tk, ds, cfg, run_id=RUN_ID)
        loader = lambda path: TradingMemoryLog({"memory_log_path": str(path)}).load_entries()
    path = Path(cache) / "backtest" / RUN_ID / "trading_memory.md"
    import system_ta
    start_n = len(loader(path))
    for i in range(0, len(dates), chunk):
        part = dates[i:i + chunk]
        log(f"backtest: dates {part[0]}..{part[-1]} ({i + len(part)}/{len(dates)})")
        res = runner(tickers, part)
        stop = None
        for tk, d, why in getattr(res, "failures", []):
            log(f"backtest: {tk} {d} failed: {system_ta.scrub(why, 100)}")
            if system_ta.classify(RuntimeError(str(why))) in ("limit", "auth"):
                stop = "quota/key problem"
        if stop:
            log(f"backtest: stopping ({stop}); run again later, finished decisions are cached")
            break
        if budget is not None and len(loader(path)) - start_n >= budget:
            log(f"backtest: per-run budget of {budget} decisions reached; run again tomorrow to continue")
            break
    want = {(t, d) for t in tickers for d in dates}
    out = []
    for e in loader(path):
        if (e["ticker"], e["date"]) in want and e.get("rating") in L.TARGETS:
            summary, horizon = L.parse_decision(e.get("decision"))
            out.append(L.make_decision(e["date"], e["ticker"], e["rating"], summary, horizon))
    return sorted(out, key=lambda d: (d["date"], d["symbol"]))


def _ledger_stats(decisions, bars):
    led = L.replay(decisions, bars)
    return led, L.stats(led)


def benchmarks(decisions, bars, tickers):
    """Buy-and-hold over the same dates: equal slots (same sizing and costs as the system) and 100% SPY."""
    first = min(d["date"] for d in decisions)
    eq = [L.make_decision(first, t, "Buy", "buy and hold") for t in tickers]
    spy = [dict(L.make_decision(first, "SPY", "Buy", "buy and hold"), target=1.0)]
    return {"buy_hold_equal": _ledger_stats(eq, bars)[1], "buy_hold_spy": _ledger_stats(spy, bars)[1]}


def coin_flip(decisions, bars, system_total, trials=200, seed=7):
    """Random ratings for the same ticker/date cells, drawn with the same rating frequencies."""
    ratings = [d["rating"] for d in decisions]
    cells = [(d["date"], d["symbol"]) for d in decisions]
    totals, sharpes = [], []
    for i in range(trials):
        rng = random.Random(seed * 100003 + i)
        fake = [L.make_decision(dt, sym, rng.choice(ratings), "coin flip") for dt, sym in cells]
        _, st = _ledger_stats(fake, bars)
        totals.append(st["total_pct"])
        sharpes.append(st["sharpe"] if st["sharpe"] is not None else 0.0)
    totals.sort()
    q = lambda p: totals[min(len(totals) - 1, int(p * len(totals)))]
    return {"trials": trials, "mean_total_pct": round(statistics.fmean(totals), 3), "median_total_pct": round(q(0.5), 3),
            "p5_total_pct": round(q(0.05), 3), "p95_total_pct": round(q(0.95), 3),
            "mean_sharpe": round(statistics.fmean(sharpes), 3),
            "share_of_trials_beating_system": round(sum(t >= system_total for t in totals) / len(totals), 3),
            "rating_frequencies": {r: ratings.count(r) for r in L.TARGETS}}


def evaluate(decisions, bars, tickers, trials=200, seed=7):
    """Full comparison for one decision subset; None when empty."""
    if not decisions:
        return None
    led, st = _ledger_stats(decisions, bars)
    return {"decisions": len(decisions), "first": decisions[0]["date"], "last": decisions[-1]["date"], "system": st,
            **benchmarks(decisions, bars, tickers), "coin_flip": coin_flip(decisions, bars, st["total_pct"], trials, seed),
            "equity_curve": led["calendar"], "trades": led["trades"], "open_positions": led["positions"]}


def build(decisions, bars, tickers, cutoff, trials=200, seed=7):
    clean = [d for d in decisions if d["date"] > cutoff]
    early = [d for d in decisions if d["date"] <= cutoff]
    return {"label": "System C backtest · TradingAgents · " + L.LABEL, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tickers": tickers, "cutoff": cutoff, "cutoff_note": "unverified assumption of the model's knowledge cutoff; dates on or before it may be contaminated",
            "rules": "entry next open; Buy +20%, Overweight +10%, Hold keep, Underweight 0% (no position), Sell -20% of equity per ticker; "
                     "cost 0.02% slippage + 0.01% fees per side",
            "decisions": [{k: d[k] for k in ("date", "symbol", "rating", "rationale", "horizon")} for d in decisions],
            "clean": evaluate(clean, bars, tickers, trials, seed),
            "possibly_contaminated": evaluate(early, bars, tickers, trials, seed) if early else None,
            "all_dates": evaluate(decisions, bars, tickers, trials, seed) if early and clean else None}


def main(argv=None, collect_fn=collect, bars_fn=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="SPY,QQQ,NVDA")
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF, help="knowledge cutoff of the plan's model (YYYY-MM-DD)")
    ap.add_argument("--start", help="first date (default: day after the cutoff)")
    ap.add_argument("--end", help="last date (default: last finished session)")
    ap.add_argument("--step", type=int, default=3, help="every Nth trading day")
    ap.add_argument("--max-decisions", type=int, default=MAX_DECISIONS)
    ap.add_argument("--budget", type=int, default=None, help="stop after this many NEW decisions in this run (free-tier daily quota)")
    ap.add_argument("--chunk", type=int, default=3, help="dates per TradingAgents call (results are written after each)")
    ap.add_argument("--trials", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--dry-run", action="store_true", help="print the plan and the call estimate; no model calls")
    ap.add_argument("--report-only", action="store_true", help="no model calls: score the decisions already cached")
    a = ap.parse_args(argv)
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    import system_ta
    end = a.end or system_ta.session_day().isoformat()
    start = a.start or (date.fromisoformat(a.cutoff) + timedelta(days=1)).isoformat()
    bars_fn = bars_fn or (lambda syms: system_ta.fetch_bars(syms, date.fromisoformat(start) - timedelta(days=7),
                                                           date.fromisoformat(end)))
    bars = bars_fn(sorted({*tickers, "SPY"}))
    dates, step = plan(list(bars.get("SPY", {})), tickers, start, end, a.step, a.max_decisions)
    n = len(dates) * len(tickers)
    print(f"backtest: {len(dates)} dates ({dates[0] if dates else '-'}..{dates[-1] if dates else '-'}, every {step}th session) "
          f"x {len(tickers)} tickers = {n} decisions, about {n * CALLS_PER_DECISION} model calls (estimate)")
    if start <= a.cutoff:
        print(f"backtest: WARNING dates on or before {a.cutoff} are reported as possibly contaminated")
    if a.dry_run or not dates:
        return 0
    if a.report_only:
        decisions = _cached(tickers, dates, a.cache)
    else:
        decisions = collect_fn(tickers, dates, cache=a.cache, chunk=a.chunk, budget=a.budget)
    if not decisions:
        print("backtest: no decisions available (GOOGLE_API_KEY missing or quota used up; try again after midnight Pacific)")
        return 1
    res = build(decisions, bars, tickers, a.cutoff, a.trials, a.seed)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    for k in ("clean", "possibly_contaminated"):
        r = res[k]
        if r:
            s, c = r["system"], r["coin_flip"]
            print(f"{k}: {r['decisions']} decisions · system {s['total_pct']:+.2f}% (Sharpe {s['sharpe']}, maxDD {s['max_dd_pct']}%, "
                  f"win {s['win_rate']}) · buy&hold {r['buy_hold_equal']['total_pct']:+.2f}% / SPY {r['buy_hold_spy']['total_pct']:+.2f}% · "
                  f"coin flip mean {c['mean_total_pct']:+.2f}% (beats system in {c['share_of_trials_beating_system']:.0%})")
    print(f"backtest: wrote {a.out}")
    return 0


def _cached(tickers, dates, cache):
    from tradingagents.memory import TradingMemoryLog
    return collect(tickers, dates, cache, chunk=10 ** 6, runner=lambda *_: None,
                   loader=lambda p: TradingMemoryLog({"memory_log_path": str(p)}).load_entries())


if __name__ == "__main__":
    sys.exit(main())
