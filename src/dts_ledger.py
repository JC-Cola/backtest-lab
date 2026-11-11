"""System B (day-trader-skill) shadow ledger: simulated fills, costs, bar-by-bar exits, snapshot shape.

NOT a broker. Nothing here can place an order. State lives in agent_memory/system_dts.json in the same
shape as the Alpaca account snapshots (equity, positions, calendar, balance_history, history) so the
Strategy Lab can show it next to our accounts.

Costs (documented, deliberately conservative): every fill is 0.02% worse than the reference price
(buys higher, sells lower, stops and targets included) plus 0.01% of the notional per side for the
SEC/TAF-style fees. Commission is zero, like Alpaca's stock plan.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

START_EQUITY = 25000.0
SLIPPAGE = 0.0002          # per side, adverse
FEE_RATE = 0.0001          # per side, on the notional (SEC/TAF style)
CURVE_KEEP = 3000
DAYS_KEEP = 25


def new_state() -> dict:
    return {"system": "day-trader-skill (System B)", "start_equity": START_EQUITY, "cash": START_EQUITY,
            "equity": START_EQUITY, "last_equity": START_EQUITY, "positions": [], "trades": [], "orders": [],
            "curve": [], "days": {}, "universe_refreshed": None}


def load(path) -> dict:
    p = Path(path)
    if p.exists():
        try:
            st = json.loads(p.read_text())
            base = new_state()
            base.update(st)
            return base
        except (ValueError, OSError):
            pass
    return new_state()


def save(state: dict, path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n")
    os.replace(tmp, p)


# ---------------------------------------------------------------- costs

def buy_fill(ref: float) -> float:
    return round(ref * (1 + SLIPPAGE), 4)


def sell_fill(ref: float) -> float:
    return round(ref * (1 - SLIPPAGE), 4)


def fee(notional: float) -> float:
    return round(notional * FEE_RATE, 4)


# ---------------------------------------------------------------- positions and trades

def _utc_iso(utc: dt.datetime) -> str:
    return utc.strftime("%Y-%m-%dT%H:%M+00:00")


def open_position(state, sym, qty, ref, entry_et, entry_utc, plan, extra=None, real_fill=None) -> dict:
    """Simulated market buy at `ref` (the next bar's open) plus slippage and fees. With `real_fill` (a paper
    broker's actual fill price) nothing is simulated: no extra slippage or fees, and cash is the broker's."""
    fill = real_fill if real_fill is not None else buy_fill(ref)
    cost = qty * fill
    f = 0.0 if real_fill is not None else fee(cost)
    if real_fill is None:
        state["cash"] = round(state["cash"] - cost - f, 4)
    pos = {"id": "%s-%s" % (entry_et.date().isoformat(), sym), "symbol": sym, "qty": qty, "avg_price": fill,
           "entry_ref": ref, "entry_et": entry_et.strftime("%Y-%m-%d %H:%M"), "entry_utc": _utc_iso(entry_utc),
           "date": entry_et.date().isoformat(), "stop": plan["stop"], "target": plan["target"],
           "flat_by": plan["flat_by"], "risk_per_share": plan["risk_per_share"], "fees_in": f,
           "checked_to": entry_et.strftime("%Y-%m-%d %H:%M"), "price": ref}
    pos.update(extra or {})
    state["positions"].append(pos)
    state["orders"].append({"date": _utc_iso(entry_utc), "symbol": sym, "side": "buy", "qty": float(qty),
                            "status": "filled", "price": fill})
    return pos


def close_position(state, pos, ref, exit_et, exit_utc, reason, real_fill=False) -> dict:
    fill = ref if real_fill else sell_fill(ref)
    proceeds = pos["qty"] * fill
    f = 0.0 if real_fill else fee(proceeds)
    if not real_fill:
        state["cash"] = round(state["cash"] + proceeds - f, 4)
    cost = pos["qty"] * pos["avg_price"]
    fees = round(pos["fees_in"] + f, 4)
    pnl = round(proceeds - cost - fees, 2)
    risk_usd = pos["qty"] * pos["risk_per_share"]
    trade = {"id": pos["id"], "symbol": pos["symbol"], "side": "long", "qty": pos["qty"],
             "entry_time": pos["entry_et"], "entry_price": pos["avg_price"], "entry_ref": pos["entry_ref"],
             "exit_time": exit_et.strftime("%Y-%m-%d %H:%M"), "exit_price": fill, "exit_ref": round(ref, 4),
             "stop": pos["stop"], "target": pos["target"], "fees": fees, "pnl": pnl,
             "pnl_pct": round(100.0 * pnl / cost, 3) if cost else 0.0,
             "r": round(pnl / risk_usd, 3) if risk_usd else None, "reason": reason,
             "date": exit_et.date().isoformat(), "exit_utc": _utc_iso(exit_utc),
             "odds": pos.get("odds"), "gates": pos.get("gates"), "trigger": pos.get("trigger")}
    state["positions"] = [p for p in state["positions"] if p["id"] != pos["id"]]
    state["trades"].append(trade)
    state["orders"].append({"date": _utc_iso(exit_utc), "symbol": pos["symbol"], "side": "sell",
                            "qty": float(pos["qty"]), "status": "filled", "price": fill})
    return trade


def walk_position(pos, bars, cutoff_et, close_et, flat_et):
    """Replay `bars` (sorted dicts: start, end, o, h, l, c; naive New York times) from pos["checked_to"].

    Rules, bar by bar: only a COMPLETE bar (end <= cutoff) is tested; a bar that touches the stop exits
    at min(open, stop) (a gap fills at the open); else a bar that touches the target exits at max(open,
    target); a bar touching both counts as the stop (same as the Time Machine). The first bar starting
    at or after flat-by exits at its open ("flat by 15:55"). No bar at flat-by and the session over:
    exit at the last close. Returns (exit or None, last_price, checked_to)."""
    start = dt.datetime.strptime(pos["checked_to"], "%Y-%m-%d %H:%M")
    last, checked = pos.get("price", pos["entry_ref"]), start
    for b in bars:
        if b["start"] < start:
            continue
        if b["start"] >= flat_et and b["start"] <= cutoff_et:
            return {"ref": b["o"], "reason": "flat by %s" % flat_et.strftime("%H:%M"), "at": b["start"]}, b["o"], b["start"]
        if b["end"] > cutoff_et:
            break
        if b["l"] <= pos["stop"]:
            return {"ref": min(b["o"], pos["stop"]), "reason": "stop", "at": b["end"]}, pos["stop"], b["end"]
        if b["h"] >= pos["target"]:
            return {"ref": max(b["o"], pos["target"]), "reason": "target", "at": b["end"]}, pos["target"], b["end"]
        last, checked = b["c"], b["end"]
    if cutoff_et >= close_et:
        return {"ref": last, "reason": "flat at the close (no bar at flat-by)", "at": close_et}, last, checked
    return None, last, checked


# ---------------------------------------------------------------- snapshot (same shape as the Alpaca accounts)

def equity_of(state) -> float:
    return round(state["cash"] + sum(p["qty"] * p.get("price", p["avg_price"]) for p in state["positions"]), 2)


def add_curve(state, utc: dt.datetime, equity: float) -> None:
    ts = int((utc - dt.datetime(1970, 1, 1)).total_seconds())
    cur = [pt for pt in state["curve"] if pt[0] != ts]
    cur.append([ts, round(equity, 2)])
    state["curve"] = sorted(cur)[-CURVE_KEEP:]


def calendar_rows(state) -> list:
    by_day: dict[str, float] = {}
    for t in state["trades"]:
        by_day[t["date"]] = round(by_day.get(t["date"], 0.0) + t["pnl"], 2)
    eq, out = state["start_equity"], []
    for d in sorted(by_day):
        pl = by_day[d]
        out.append({"date": d, "pl": pl, "pct": round(100.0 * pl / eq, 2), "equity": round(eq + pl, 2)})
        eq += pl
    return out


def balance_rows(state) -> list:
    bal, rows = state["start_equity"], []
    for t in sorted(state["trades"], key=lambda x: x["exit_utc"]):
        after = round(bal + t["pnl"], 2)
        rows.append({"time": t["exit_utc"][:10] + " " + t["exit_utc"][11:16] + ":00",
                     "balance_before": round(bal, 2), "balance_after": after, "realized_pnl": t["pnl"],
                     "action": "Close long position for symbol %s at price %.2f for %d units. Position AVG Price was %.3f (%s)"
                               % (t["symbol"], t["exit_price"], t["qty"], t["entry_price"], t["reason"])})
        bal = after
    return rows


def refresh_snapshot(state, utc: dt.datetime) -> dict:
    """Recompute the Alpaca-shaped top-level fields from cash, positions and trades (idempotent)."""
    if state.get("source") == "alpaca-paper":      # the paper account's own numbers are the truth (dts_broker.py)
        return state
    eq = equity_of(state)
    state["generated"] = _utc_iso(utc)
    state["equity"] = eq
    for p in state["positions"]:
        p["price"] = round(p.get("price", p["avg_price"]), 4)
        p["value"] = round(p["qty"] * p["price"], 2)
        p["pl"] = round(p["value"] - p["qty"] * p["avg_price"], 2)
        p["pl_pct"] = round(p["pl"] / (p["qty"] * p["avg_price"]), 5)
    state["calendar"] = calendar_rows(state)
    state["last_equity"] = state["calendar"][-2]["equity"] if len(state["calendar"]) > 1 else state["start_equity"]
    realized = round(sum(t["pnl"] for t in state["trades"]), 2)
    unreal = round(sum(p["pl"] for p in state["positions"]), 2)
    state["balance_history"] = {"rows": balance_rows(state), "realized": realized, "unrealized": unreal,
                                "equity": eq, "available": round(state["cash"], 2), "margin": 0.0,
                                "start_balance": state["start_equity"]}
    now_ts = int((utc - dt.datetime(1970, 1, 1)).total_seconds())
    utc_day = lambda d: int((dt.datetime.fromisoformat(d + "T21:00:00") - dt.datetime(1970, 1, 1)).total_seconds())
    state["history"] = {"1D": [pt for pt in state["curve"] if pt[0] >= now_ts - 86400][-400:],
                        "ALL": [[utc_day(r["date"]), r["equity"]] for r in state["calendar"]]}
    keep = sorted(state["days"])[-DAYS_KEEP:]
    state["days"] = {d: state["days"][d] for d in keep}
    return state
