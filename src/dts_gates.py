"""System B gates: news, entry timer + exit planner, time-machine odds, budget + risk (thin wiring over the vendored tools)."""
from __future__ import annotations

import datetime as dt
import types

import dts_ledger as L  # noqa: F401
from dts_core import (CFG, entry_m, exit_m, news_m, risk_m, scan_m, size_m, tm_m)

def news_gate(sym, now_utc, sec_state, fetcher, errors):
    """Upstream trader-news over this one ticker; we only add the block rule (see the docstring)."""
    n = news_m()
    now = n.parse_time(now_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"))
    watch = {sym: None}
    errs = []
    try:
        items = n.dedupe(n.tag(n.collect(fetcher, watch, sec_state, sec=bool(fetcher.sec_ua), forms=("8-K",), errors=errs), watch))
        shown = n.select(items, sec_state, now, hours=CFG["news_hours"], show_all=True)
    except Exception as e:  # noqa: BLE001 - fail open: a dead feed must not stop the whole system
        return {"block": False, "reasons": [], "flags": [], "items": 0, "notes": ["news failed: %s" % e]}
    reasons, flags = [], []
    for it in shown:
        tags = set(it.get("tags") or [])
        if "halt" in tags or "risk" in tags:
            reasons.append("%s: %s" % ("/".join(sorted(tags & {"halt", "risk"})), it["title"][:90]))
        if it.get("kind") == "filing" and set(it.get("items") or []) & CFG["distress_items"]:
            reasons.append("8-K distress item: %s" % it["title"][:90])
        if "earnings" in tags:
            flags.append("earnings")
    return {"block": bool(reasons), "reasons": reasons, "flags": sorted(set(flags)), "items": len(shown), "notes": errs[:2]}


def plan_trade(sym, text60, T_et, T_utc, day, p0):
    """entry timer -> exit planner. Returns (plan, entry_result) or (None, reason)."""
    en, ex = entry_m(), exit_m()
    chart = en.parse_chart(text60, sym)
    res = en.evaluate(chart, T_utc, "orb", CFG["or_minutes"], "5m")
    if res["status"] != "triggered":
        return None, "entry timer: %s (%s)" % (res["status"], res.get("reason"))
    orr = res["opening_range"]
    k = int((T_et - dt.datetime.combine(day, scan_m().OPEN)).total_seconds() // 300) - 1
    ns = types.SimpleNamespace(symbol=sym, from_path=None, side="buy", entry=p0, setup="orb", date=day.isoformat(),
                               time=ex.ampm(T_et.time()), entry_bar=k, interval="5m", or_high=orr["high"],
                               or_low=orr["low"], or_minutes=CFG["or_minutes"], stop="or-mid", target="2R",
                               flat_by=None, offline=None, now=None, config=None)
    try:
        plan, _warn = ex.build_plan(ns, T_utc)
    except ex.PlanError as e:
        return None, "exit planner: %s" % e
    return plan, res


def odds_gate(text60, plan, T_utc):
    """Time machine on the plan, then our threshold on its own numbers."""
    tm = tm_m()
    try:
        doc = tm.time_machine(text60, tm.normalize_plan(plan, "system_dts"), T_utc, CFG["tm_replays"], CFG["tm_seed"],
                              2, plan["flat_by"], "yahoo")
    except tm.TMError as e:
        return None, "time machine: %s" % e
    mc, hist = doc["mc"], doc["history"]
    lv = tm.lean(mc)
    odds = {"p_target": mc["p_target"], "p_stop": mc["p_stop"], "p_neither": mc["p_neither"], "p_green": mc["p_green"],
            "avg_R": mc["avg_R"], "lean": round(lv[0], 4) if lv else None, "hist_n": hist.get("triggered", 0),
            "hist_avg_R": hist.get("avg_R"), "verdict": doc["verdict"], "seed": CFG["tm_seed"]}
    why = None
    if not CFG["odds_gate"]:
        return odds, None
    if lv is None or lv[0] < CFG["tm_min_lean"]:
        why = "verdict not toward the target: %s" % doc["verdict"]
    elif mc["avg_R"] <= 0:
        why = "replay average R %+.3f is not positive" % mc["avg_R"]
    elif hist.get("triggered", 0) >= CFG["tm_hist_min_trades"] and (hist.get("avg_R") or 0) < 0:
        why = "history average R %+.3f over %d past triggers is negative" % (hist["avg_R"], hist["triggered"])
    return odds, why


def size_and_risk(sym, plan, p0, state, T_et, T_utc, equity):
    """budget -> risk. Returns (qty, risk verdict text) or (0, reason)."""
    sz, rk = size_m(), risk_m()
    max_loss = round(equity * CFG["risk_pct"] / 100.0, 2)
    cfg = {"budget": equity, "cash_buffer_pct": 0, "max_position_pct": CFG["max_position_pct"],
           "max_order_usd": equity, "min_order_usd": 100, "max_loss_per_trade_usd": max_loss,
           "max_daily_loss_usd": round(max_loss * CFG["max_concurrent"], 2), "max_trades_per_day": CFG["max_concurrent"],
           "max_orders_per_day": 3 * CFG["max_concurrent"], "min_reward_risk": 1.0, "require_stop": True,
           "no_entries_before": "09:45", "no_entries_after": "15:30", "flat_by": "15:55", "paper": True,
           "blocked": [], "allow_margin": False, "allow_short": False}
    try:
        s = sz.size_trade(plan, cfg, equity, None, p0, max(state["cash"], 0.0), T_et.date().isoformat())
    except sz.SizeError as e:
        return 0, "budget: %s" % e
    order = sz.bracket_order(s)
    if not order:
        return 0, "budget: %s" % s["refusal"]
    day = T_et.date().isoformat()
    placed = [{"status": "placed", "date": day, "kind": "entry", "side": "buy", "symbol": p["symbol"],
               "max_loss": p["qty"] * p["risk_per_share"]} for p in state["positions"] if p["date"] == day]
    pf = {"cash": state["cash"], "positions": [{"ticker": p["symbol"], "shares": p["qty"],
                                                "lots": [{"shares": p["qty"], "cost": p["qty"] * p["avg_price"]}]}
                                               for p in state["positions"]]}
    prices = {p["symbol"]: p.get("price", p["avg_price"]) for p in state["positions"]}
    prices[sym] = p0
    res = rk.check([order], cfg, pf, prices, {"orders": placed}, day, T_utc, {k: T_utc for k in prices})
    r = res["results"][0]
    if r["verdict"] != "PASS":
        return 0, "risk: " + "; ".join(r["reasons"])
    return s["qty"], "risk PASS: max loss $%.2f at the stop, %s" % (s["max_loss"], s["bound_by"])


