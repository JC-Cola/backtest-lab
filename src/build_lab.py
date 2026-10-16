"""Builds report/lab.html (the Strategy Lab dashboard) from the engine, results.json,
confirmation.json and the agent's memory. Every number on the page is net of costs."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

import data
from agent import platform as AP
from agent import spec as AS
from agent.memory import Memory
from engine import extract_trades, returns_np
from run import CONFIG
from strategies import STRATEGIES, positions

ROOT = Path(__file__).resolve().parent.parent
BY_ID = {s["id"]: s for s in STRATEGIES}

# The book: (code, strategy id, market) plus the plain-language panels for each.
BOOK = [
    {"code": "MR-IBS", "id": "ibs", "market": "SPY",
     "signal": ["ibs_t = (C_t - L_t) / (H_t - L_t)      // where the close sits in the day's range",
                "", "ENTRY  ibs_t < a                     // closed near the low",
                "EXIT   ibs_t > b                     // closed near the high",
                "TRADE  decide at close t, fill at open t+1"],
     "edge": "Index ETFs that close near the day's low tend to bounce the next day. Short-term liquidity "
             "providers are paid to absorb end-of-day selling.",
     "kills": ["Earns less than just holding: in the market only about a third of the time.",
               "Since it was published (2014) it only works on big US index ETFs, not abroad.",
               "Each round trip pays about 6 bps in costs, a real share of a ~40 bp average edge."]},
    {"code": "MR-WR", "id": "williams_r", "market": "QQQ",
     "signal": ["wr_t = -100 * (HH_n - C_t) / (HH_n - LL_n)   // Williams %R over n days",
                "", "ENTRY  wr_t < -90  [and C_t > SMA200]", "EXIT   wr_t > -20",
                "TRADE  decide at close t, fill at open t+1"],
     "edge": "Same idea as IBS with a longer lookback: buy short, sharp dips in a market that drifts up.",
     "kills": ["Did not hold up on fresh data: only 4 of 13 untouched markets were convincing.",
               "Overlaps heavily with IBS, so it is not a second, independent edge.",
               "Sits out the strongest up-trends, so it trails buy-and-hold in bull markets."]},
    {"code": "CAL-JAN", "id": "qp_january_barometer", "market": "QQQ",
     "signal": ["jan5 = C(5th session of Jan) / C(last session of Dec) - 1",
                "", "HOLD   every January", "HOLD   Feb-Dec only if jan5 > 0 (rule 'first5')",
                "       or full-January return > 0 (rule 'january')"],
     "edge": "Folklore from the Stock Trader's Almanac: a weak start to the year has preceded several bear markets.",
     "kills": ["One decision a year. Most of the edge is 2000, 2001 and 2008, about three lucky calls.",
               "Januaries move together worldwide, so more markets do not add independent evidence.",
               "Often sits out rising years (1991, 1995, 2016, 2024 in the fresh-data test)."]},
    {"code": "CAL-MIX", "id": "calendar_composite", "market": "GLD",
     "signal": ["tom   = last session + first 3 sessions of the month", "hol   = session before an NYSE holiday",
                "fomc  = day before + day of a scheduled Fed statement", "",
                "HOLD   any chosen parts, optionally only Nov-Apr"],
     "edge": "Cash flows cluster around month ends, holidays and Fed days, and some of that pressure shows up in prices.",
     "kills": ["Best single result in the whole search (p = 0.0015) but it failed on the lockbox markets.",
               "Gold has no obvious reason to follow equity cash-flow calendars.",
               "In the market only a few days a month: small, lumpy returns."]},
    {"code": "TS-TREND", "id": "trend_filter", "market": "BTC-USD",
     "signal": ["ENTRY  C_t > SMA(n)", "EXIT   C_t < SMA(n)", "",
                "TRADE  decide at close t, fill at open t+1", "COST   10 bps per side (crypto)"],
     "edge": "Trends in crypto persist because new money arrives in waves and leverage forces late buyers and sellers.",
     "kills": ["Bitcoin is in the test only because it survived. That is hindsight.",
               "The ETH lockbox moves with BTC, so it is weak confirmation.",
               "Whipsaws in sideways years cost 10 bps each way."]},
    {"code": "TS-VOL", "id": "vol_target_trend", "market": "SPY",
     "signal": ["rv_t  = stdev(daily returns, 20d) * sqrt(252)", "w_t   = min(1, target / rv_t)", "",
                "HOLD   w_t of the portfolio while C_t > SMA200", "FLAT   otherwise"],
     "edge": "Volatility clusters, so cutting size when it rises should lower drawdowns for a similar return.",
     "kills": ["No better than random timing on the test years (p = 0.77).",
               "Halves exposure in the sharp rebounds that follow volatility spikes.",
               "Lower risk, but much lower return than just holding SPY."]},
]

ABOUT = {
    "MR-IBS": ("Mean reversion", "Daily, 1-3 day holds", "IBS reversal (Connors/Alvarez; Pagonidis 2014)"),
    "MR-WR": ("Mean reversion", "Daily, 1-5 day holds", "Larry Williams; TradingView built-in"),
    "CAL-JAN": ("Calendar", "Yearly decision", "Quantpedia: January barometer"),
    "CAL-MIX": ("Calendar", "A few days a month", "Ariel 1987/1990; Lucca & Moench 2015"),
    "TS-TREND": ("Trend", "Daily, weeks-long holds", "Faber (2007) trend filter"),
    "TS-VOL": ("Trend", "Daily", "Moreira & Muir (2017)"),
    "AGENT": ("Found by the agent", "Daily", "Self-improving agent, best idea so far"),
}


def _axis_key(x):
    v = json.loads(x)
    return (0, float(v), "") if isinstance(v, (int, float)) and not isinstance(v, bool) else (1, 0.0, x)


def r5(a):
    return [None if not math.isfinite(x) else round(float(x), 5) for x in a]


def verdict_for(sid, market, results, conf):
    row = next((r for r in results if r["strategy"] == sid and r["asset"] == market), None)
    if row is None:
        return None
    cf, lb = row.get("coin_flip") or {}, row.get("lockbox") or {}
    v = {"verdict": row["verdict"], "p": cf.get("p_value"), "q": cf.get("q_value"),
         "lockbox": lb.get("confirmed"), "lockbox_p": lb.get("fisher_p"), "params": row["params"]}
    c = (conf or {}).get("candidates", {}).get(sid)
    if c:
        v["fresh"] = {"confirmed": c["confirmed"], "fisher_p": c["fisher_p"],
                      "hits": c["markets_p_below_0.10"], "n": c["n_markets"]}
    return v


def build_market(ticker, ctx_raw, cache):
    if ticker not in cache:
        cfg = CONFIG[ticker]
        df = data.load(ticker).loc[cfg["is_start"]:]
        cache[ticker] = (df, data.align_ctx(ctx_raw, df.index), cfg)
    return cache[ticker]


def series(df, pos, cost):
    o, c = df.Open.values, df.Close.values
    r = returns_np(o, c, pos, cost)
    tr = extract_trades(o, c, pos, cost)
    return {"r": r5(r), "trades": [[int(e), round(float(x), 5)] for e, x in zip(tr["entry"], tr["ret"])],
            "pos": [int(round(p * 100)) for p in pos]}


def main():
    results = json.loads((ROOT / "results" / "results.json").read_text())["results"]
    cp = ROOT / "results" / "confirmation.json"
    conf = json.loads(cp.read_text()) if cp.exists() else None
    ctx_raw = data.load_context()
    cache, markets, book = {}, {}, []
    for b in BOOK:
        st = BY_ID[b["id"]]
        df, ctx, cfg = build_market(b["market"], ctx_raw, cache)
        v = verdict_for(b["id"], b["market"], results, conf)
        combos = []
        for p in st["grid"]:
            pos = np.clip(np.nan_to_num(positions(st, df, ctx, p).values.astype(float)), 0, 1)
            combos.append({"params": p} | series(df, pos, cfg["cost_bps"]))
        chosen = next(i for i, c in enumerate(combos) if json.loads(json.dumps(c["params"])) == v["params"])
        fam, freq, src = ABOUT[b["code"]]
        book.append({"code": b["code"], "id": b["id"], "name": st["name"], "market": b["market"],
                     "desc": st["description"], "family": fam, "freq": freq, "source": src,
                     "signal": b["signal"], "edge": b["edge"], "kills": b["kills"],
                     "axes": {k: sorted({json.dumps(c["params"][k]) for c in combos}, key=_axis_key)
                              for k in st["grid"][0]},
                     "combos": combos, "chosen": chosen, "verdict": v})
    # the self-improving agent's current best idea
    mem = Memory()
    top = mem.top(1)
    if top:
        t = top[0]
        s = t["spec"]
        m = AP.Market(s["market"], ctx_raw)
        df, ctx, cfg = build_market(s["market"], ctx_raw, cache)
        pos = AS.positions(s, m.table, m.th)
        assert len(pos) == len(df)
        fam, freq, src = ABOUT["AGENT"]
        book.append({"code": "AGENT", "id": "agent_" + t["hash"], "name": "Agent's best idea", "market": s["market"],
                     "desc": AS.describe(s), "family": fam, "freq": freq, "source": src,
                     "signal": ["ENTRY  " + " AND ".join(f"{c['feature']} {c['op']} p{c['q']}" for c in s["entry"])]
                     + ([f"FILTER {s['filter']['feature']} {s['filter']['op']} p{s['filter']['q']}"] if s.get("filter") else [])
                     + ([f"EXIT   after {s['exit']['n']} sessions"] if s["exit"]["type"] == "hold" else
                        [f"EXIT   {s['exit']['cond']['feature']} {s['exit']['cond']['op']} p{s['exit']['cond']['q']}"
                         f" or after {s['exit']['max_hold']} sessions"])
                     + ["", "pN = N-th percentile of that feature on the tuning years"],
                     "edge": f"Found by the agent after {len(mem.trials)} trials. Its score on the tuning years, "
                             f"deflated for every trial run, is {mem.current_reward(t):.2f} (0.95 is needed before a held-out test).",
                     "kills": ["Chosen as the best of hundreds of tries: some of its score is luck.",
                               "Has not earned a held-out test yet, so its test-year numbers below are for viewing only.",
                               "Three or more conditions: each one is another chance to fit noise."],
                     "axes": {}, "combos": [{"params": {}} | series(df, pos, cfg["cost_bps"])], "chosen": 0,
                     "verdict": {"verdict": "AGENT", "reward": round(mem.current_reward(t), 3),
                                 "trials": len(mem.trials)}})
    for ticker, (df, _, cfg) in cache.items():
        o, c = df.Open.values, df.Close.values
        markets[ticker] = {"dates": [d.strftime("%Y-%m-%d") for d in df.index],
                           "bench": r5(returns_np(o, c, np.ones(len(c)), cfg["cost_bps"])),
                           "oos_start": cfg["oos_start"], "is_end": cfg["is_end"], "ann": cfg["ann"],
                           "cost_bps": cfg["cost_bps"]}
    hv = mem.holdout_verdicts()
    research = {"trials": len(mem.trials), "scored": len(mem.scored()),
                "rejected": sum(1 for t in mem.trials if t.get("status") == "rejected"),
                "holdout_shots": len(hv), "holdout_pass": sum(v["pass"] for v in hv),
                "top": [{"desc": AS.describe(t["spec"]), "now": round(mem.current_reward(t), 3),
                         "found": t["reward"], "folds": t["fold_sharpes"]} for t in mem.top(8)],
                "hunt_tests": sum(1 for r in results if r.get("coin_flip")),
                "hunt_strategies": len({r["strategy"] for r in results}) - 1}
    blob = json.dumps({"book": book, "markets": markets, "research": research,
                       "generated": json.loads((ROOT / "results" / "results.json").read_text())["generated"]},
                      separators=(",", ":")).replace("</", "<\\/")
    html = (ROOT / "report" / "lab_template.html").read_text().replace("/*LAB*/", blob)
    out = ROOT / "report" / "lab.html"
    out.write_text(html)
    print(f"wrote {out} ({len(html) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
