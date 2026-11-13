"""System C plan state + the 5-minute monitor (PAPER account only, no model calls).

    python ta_monitor.py                 # one monitor pass (workflow system_ta_monitor, every 5 min in market hours)
    python ta_monitor.py --adopt DATE    # one-off: register + protect the plans of DATE's saved decisions (extracts with Gemini)

State: agent_memory/system_ta_plans.json  {"plans": {SYM: {decision_id, plan, status, opened, scale: [...], exits: [...], placed}}}
Idempotent by construction: orders carry deterministic client ids, scale-in size is computed from the LIVE position (never
exceeds the plan's slot), protection is compared with the live orders and only touched when it differs, and the state file
only changes when something happened (so the workflow's commit is empty on quiet runs).

Per run: scale-ins ("above X" = last completed 5-min close >= X on SUSTAIN=3 consecutive checks, "below" mirrored, "near X"
= within 0.5% on one check), plan exit triggers (market close, stops cancelled first), expiry (valid_days sessions), and
"every open position has its stop live" (re-placed if it was cancelled). Telegram only when an order is placed/cancelled.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ta_broker as TB
import ta_ledger as L
import ta_plan as P

ROOT = Path(__file__).resolve().parents[1]
MEM = ROOT / "agent_memory"
PLANS = MEM / "system_ta_plans.json"
SUSTAIN = 3
NEAR = 0.005
BAR_MIN = 5
STALE_MIN = 20
DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"


# ---------- state ----------
def load(path=PLANS):
    try:
        s = json.loads(Path(path).read_text())
        if isinstance(s, dict) and isinstance(s.get("plans"), dict):
            return s
    except (OSError, ValueError):
        pass
    return {"plans": {}}


def save(state, path=PLANS):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def add_sessions(day, n):
    d = date.fromisoformat(str(day))
    while n > 0:
        d += timedelta(days=1)
        n -= d.weekday() < 5
    return d.isoformat()


def _fp(plan):
    return json.dumps([plan.get("rating"), plan.get("initial_x"), plan.get("stop"), plan.get("tp"),
                       [(s["condition"], s["price"], s["size_x"]) for s in plan.get("scale_in", [])],
                       [(e["condition"], e["price"]) for e in plan.get("exits", [])]])


def register(state, sym, decision_id, day, plan, held=0.0):
    """Store tonight's plan for `sym`. Same decision id: no-op. Same plan as before: keep the live state (streaks, done
    scale-ins). Changed rating/plan: fresh record (stop and pending scale-ins are replaced on the next protect pass).
    Returns 'same' | 'kept' | 'new'."""
    old = state["plans"].get(sym)
    if old and old["decision_id"] == decision_id:
        return "same"
    if old and old.get("status") == "active" and _fp(old["plan"]) == _fp(plan):
        old.update(decision_id=decision_id, date=str(day))
        old["plan"]["notes"], old["plan"]["not_executed"] = plan["notes"], plan["not_executed"]
        return "kept"
    state["plans"][sym] = {
        "decision_id": decision_id, "date": str(day), "plan": plan, "status": "active", "opened": bool(held), "placed": None,
        "scale": [dict(s, state="pending", streak=0, last_bar=None, expires=add_sessions(day, s.get("valid_days") or P.DEFAULT_VALID_DAYS))
                  for s in plan.get("scale_in", [])],
        "exits": [dict(e, state="pending") for e in plan.get("exits", [])], "log": []}
    return "new"


# ---------- bars (completed 5-minute bars, batched, SIP then IEX like the rest of the repo) ----------
def fetch_5min(symbols, now=None, urlopen=urllib.request.urlopen):
    """{symbol: (bar start ISO, close)} of the latest COMPLETED 5-minute bar, from one batched request."""
    now = now or datetime.now(timezone.utc)
    if not symbols:
        return {}
    try:
        import alpaca_keys
        key, sec = alpaca_keys.data_keys()
    except Exception:
        key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not (key and sec):
        raise RuntimeError("no Alpaca keys")
    last = None
    for feed in ("sip", "iex"):
        try:
            q = {"symbols": ",".join(sorted(set(symbols))), "timeframe": f"{BAR_MIN}Min", "feed": feed, "adjustment": "raw", "limit": 1000,
                 "start": (now - timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ")}
            req = urllib.request.Request(f"{DATA_URL}?{urllib.parse.urlencode(q)}",
                                         headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
            with urlopen(req, timeout=20) as r:
                j = json.load(r)
            out = {}
            for sym, rows in (j.get("bars") or {}).items():
                done = [b for b in rows if datetime.fromisoformat(b["t"].replace("Z", "+00:00")) + timedelta(minutes=BAR_MIN) <= now]
                if done:
                    out[sym] = (done[-1]["t"], float(done[-1]["c"]))
            return out
        except Exception as ex:
            last = ex
    raise last


# ---------- order helpers ----------
def _stamp(now):
    import uuid   # protection is compared with the live orders, so its client id only has to be unique
    return now.strftime("%Y%m%d%H%M") + "-" + uuid.uuid4().hex[:6]


def sync_protection(broker, sym, rec, qty, orders, now, sleep=time.sleep):
    """Make the live protective orders for `sym` equal to the plan (stop / take-profit for the whole shares held).
    Cancels wrong/stale ones first and waits, then places. Returns an event string or None."""
    plan, n = rec["plan"], int(abs(qty))
    stop, tp = plan.get("stop"), plan.get("tp")
    live = TB.protective_orders(orders, sym, qty)
    ok = bool(live) and all(int(float(o.qty)) == n for o in live)
    if ok:
        stops = [o for o in live if TB._v(o.order_type) in ("stop", "stop_limit")]
        lims = [o for o in live if TB._v(o.order_type) == "limit"]
        ok = (bool(stop) == bool(stops) and bool(tp) == bool(lims)
              and all(abs(float(o.stop_price) - stop) < 0.005 for o in stops) and all(abs(float(o.limit_price) - tp) < 0.005 for o in lims)
              and len(live) <= (2 if stop and tp else 1))
    if ok or (not live and not (stop or tp)):
        return None
    ev = []
    if live:
        if not TB.cancel_and_wait(broker, live, sleep):
            return f"{sym}: could not cancel the old stop/target orders in time, nothing replaced (retry next run)"
        ev.append(f"cancelled {len(live)} old protective order(s)")
    rec["placed"] = None
    if not rec.get("status") == "active" or n < 1:
        return f"{sym}: " + ", ".join(ev) if ev else None
    try:
        got = TB.place_protection(broker, sym, qty, stop, tp, _stamp(now))
    except Exception as ex:
        return f"{sym}: " + ", ".join(ev + [f"protection FAILED: {str(ex)[:90]}"])
    if got:
        rec["placed"] = {"stop": stop, "tp": tp, "qty": n, "ids": [g[1] for g in got], "at": now.isoformat(timespec="seconds")}
        what = (f"stop ${stop:g}" if stop else "") + (" + " if stop and tp else "") + (f"target ${tp:g}" if tp else "")
        ev.append(f"placed GTC {got[0][0].upper()} {what} for {n} sh (order {got[0][1][:8]}, {got[0][2]})")
    return f"{sym}: " + ", ".join(ev)


def _fill(broker, oid, sleep, tries=5):
    """(status, filled_qty) after a few seconds of polling a market order."""
    st, fq = "unknown", 0.0
    for _ in range(tries):
        try:
            o = broker.get_order_by_id(oid)
            st, fq = TB._v(o.status), float(o.filled_qty or 0)
            if st not in TB.LIVE:
                break
        except Exception:
            pass
        sleep(1)
    return st, fq


def _market(broker, sym, side, qty, cid):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
    try:
        res = broker.submit_order(MarketOrderRequest(symbol=sym, qty=qty, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                                     time_in_force=TimeInForce.DAY, client_order_id=cid))
        return str(res.id), None
    except Exception as ex:
        if "unique" in str(ex).lower() or "duplicate" in str(ex).lower():   # we already sent this one (state not saved): adopt it
            try:
                return str(broker.get_order_by_client_id(cid).id), None
            except Exception:
                pass
        return None, str(ex)[:90]


# ---------- one pass ----------
def check_scale(s, bar, now):
    """Update the trigger state of scale-in `s` with the latest completed bar; True when it fires."""
    if not bar:
        return False
    t, close = bar
    if datetime.fromisoformat(t.replace("Z", "+00:00")) < now - timedelta(minutes=STALE_MIN):
        return False   # no fresh bar (illiquid / feed gap): neither count nor reset
    c, px = s["condition"], s["price"]
    if c == "near":
        return abs(close / px - 1) <= NEAR
    if t == s.get("last_bar"):
        return s["streak"] >= SUSTAIN   # same bar as the previous (or an overlapping) run: do not count twice
    hit = close >= px if c == "above" else close <= px
    s["streak"] = s["streak"] + 1 if hit else 0
    s["last_bar"] = t if s["streak"] else None
    return s["streak"] >= SUSTAIN


def run_once(broker, state, bars, now=None, sleep=time.sleep):
    """Returns the list of event strings (orders placed/cancelled). Mutates `state`."""
    now = now or datetime.now(timezone.utc)
    events = []
    equity = float(broker.get_account().equity)
    held = TB.held_qty(broker)
    orders = TB.open_orders(broker)
    for sym, rec in sorted(state["plans"].items()):
        if rec.get("status") != "active":
            continue
        plan, qty = rec["plan"], held.get(sym, 0.0)
        bar = bars.get(sym)
        close = bar[1] if bar else None
        if not qty:
            mine = [o for o in orders if o.symbol == sym]
            if rec.get("opened") and not mine:
                rec["status"] = "closed"
                rec["log"] = (rec.get("log", []) + [f"{now.date()}: position closed"])[-20:]
            elif rec.get("opened"):
                stale = TB.protective_orders(orders, sym, 1) + TB.protective_orders(orders, sym, -1)
                if stale and TB.cancel_and_wait(broker, stale, sleep):
                    events.append(f"{sym}: position is flat, cancelled {len(stale)} leftover protective order(s)")
            continue
        rec["opened"] = True
        side = 1 if qty > 0 else -1
        # reconcile scale-in orders sent earlier
        for i, s in enumerate(rec["scale"]):
            if s["state"] == "ordered":
                try:
                    o = broker.get_order_by_id(s["order_id"])
                    st = TB._v(o.status)
                except Exception:
                    continue
                if st == "filled":
                    s["state"] = "done"
                elif st not in TB.LIVE:
                    s.update(state="pending", streak=0, last_bar=None, order_id=None)
        # plan exit triggers
        for e in rec["exits"]:
            if e["state"] != "pending" or close is None:
                continue
            if (close <= e["price"]) if e["condition"] == "below" else (close >= e["price"]):
                live = TB.protective_orders(orders, sym, qty)
                if live and not TB.cancel_and_wait(broker, live, sleep):
                    events.append(f"{sym}: exit {e['condition']} ${e['price']:g} hit but the stop could not be cancelled yet; retry next run")
                    break
                oid, err = _market(broker, sym, TB.exit_side(qty), abs(qty), f"tap-ex-{sym}-{rec['date']}-{int(e['price'] * 100)}")
                if err:
                    events.append(f"{sym}: exit order FAILED: {err}")
                    break
                e.update(state="ordered", order_id=oid)
                rec["status"] = "exited"
                events.append(f"{sym}: closed {abs(qty):g} sh at market, plan exit {e['condition']} ${e['price']:g} (last 5-min close ${close:g}); stop cancelled")
                break
        if rec["status"] != "active":
            continue
        # scale-ins
        for i, s in enumerate(rec["scale"]):
            if s["state"] != "pending":
                continue
            if now.date().isoformat() > s["expires"]:
                s["state"] = "expired"
                continue
            if not check_scale(s, bar, now):
                continue
            base = plan.get("base") or 0.0
            room = base * equity - abs(qty) * close          # live room: never above the plan's slot
            value = min(s["size_x"] * base * equity, room, L.SLOT * equity - abs(qty) * close)
            n = int(math.trunc(value / close))
            if n < 1:
                s.update(state="done", note="no room in the slot")
                continue
            oid, err = _market(broker, sym, "buy" if side > 0 else "sell", n, f"tap-sc-{sym}-{rec['date']}-{i}")
            if err:
                events.append(f"{sym}: scale-in order FAILED: {err}")
                continue
            s.update(state="ordered", order_id=oid)
            st, fq = _fill(broker, oid, sleep)
            if st == "filled":
                s["state"] = "done"
                qty += side * fq
            events.append(f"{sym}: scale-in +{s['size_x']:g}x ({s['condition']} ${s['price']:g}) bought {n} sh at market ({st})")
        ev = sync_protection(broker, sym, rec, qty, TB.open_orders(broker, sym) if events else orders, now, sleep)
        if ev:
            events.append(ev)
    return events


def message(events, now):
    return f"🅲 System C monitor {now:%H:%M} UTC (paper)\n" + "\n".join(events)


# ---------- nightly hooks (called from system_ta) ----------
def release(broker, state, orders_planned, sleep=time.sleep):
    """Before tonight's market orders: cancel (and wait for) the protective orders of every ticker whose order reduces,
    closes or flips the position, so Alpaca does not reject it for reserved shares. Returns the set of such symbols."""
    held = TB.held_qty(broker)
    out = set()
    for o in orders_planned:
        q = held.get(o["symbol"], 0.0)
        if o.get("side") and q and o["side"] == TB.exit_side(q):
            out.add(o["symbol"])
    if out:
        live = [x for x in TB.open_orders(broker) if x.symbol in out and TB.protective_orders([x], x.symbol, held[x.symbol])]
        TB.cancel_and_wait(broker, live, sleep)
        for s in out:
            if s in state["plans"]:
                state["plans"][s]["placed"] = None
    return out


def protect_all(broker, state, skip=(), now=None, sleep=time.sleep):
    """After tonight's orders: stops/targets for every held position of an active plan (new/changed plans replace the old
    ones; positions still waiting for tomorrow's entry get theirs from the monitor after the fill)."""
    now = now or datetime.now(timezone.utc)
    held, orders, ev = TB.held_qty(broker), TB.open_orders(broker), []
    for sym, rec in sorted(state["plans"].items()):
        if rec.get("status") == "active" and held.get(sym) and sym not in skip:
            rec["opened"] = True
            e = sync_protection(broker, sym, rec, held[sym], orders, now, sleep)
            if e:
                ev.append(e)
    return ev


def nightly(broker, state, night, closes, build, held=None, sleep=time.sleep):
    """Register tonight's plans. night: [decision]; closes: {sym: last close}; build(text, rating, ref, held_side) -> plan.
    Returns ({sym: plan}, {sym: initial_size_x}). A decision whose plan is already stored is not re-extracted (no model call)."""
    plans, sizes, held = {}, {}, held or {}
    for d in night:
        sym = d["symbol"]
        if d.get("source") != "ta" or not d.get("rating"):
            continue
        rec = state["plans"].get(sym)
        if rec and rec["decision_id"] == d["id"]:
            plans[sym] = rec["plan"]
        else:
            q = held.get(sym, 0.0)
            plan = build(d["rationale"], d["rating"], closes.get(sym), "long" if q > 0 else "short" if q < 0 else None)
            register(state, sym, d["id"], d["date"], plan, q)
            plans[sym] = state["plans"][sym]["plan"]
        sizes[sym] = plans[sym].get("initial_x", 1.0)
    return plans, sizes


# ---------- CLI ----------
def monitor(broker=None, state_path=PLANS, bars_fn=fetch_5min, send=None, now=None, sleep=time.sleep):
    now = now or datetime.now(timezone.utc)
    broker = broker or TB.client_from_env()
    if broker is None:
        print("ta_monitor: no ALPACA_TA keys")
        return 0
    if not broker.get_clock().is_open:
        print("ta_monitor: market closed")
        return 0
    state = load(state_path)
    before = json.dumps(state, sort_keys=True)
    syms = sorted(state["plans"])
    if not any(r.get("status") == "active" for r in state["plans"].values()):
        print("ta_monitor: no active plans")
        return 0
    try:
        bars = bars_fn(sorted(s for s, r in state["plans"].items() if r.get("status") == "active"), now)
    except Exception as ex:
        print(f"ta_monitor: bars unavailable ({type(ex).__name__}); only protection checked", file=sys.stderr)
        bars = {}
    events = run_once(broker, state, bars, now, sleep)
    if json.dumps(state, sort_keys=True) != before:
        save(state, state_path)
    if events:
        print("\n".join(events))
        if send is None:
            import telegram
            send = telegram.send
        send(message(events, now))
    else:
        print(f"ta_monitor: {len(syms)} plan(s), nothing to do")
    return 0


def adopt(day, decisions_path=MEM / "system_ta.json", state_path=PLANS, broker=None, build=None, sleep=time.sleep):
    """Register the plans of `day`'s saved decisions for the positions open now and protect them. No entry/resize orders."""
    broker = broker or TB.client_from_env()
    decs = [d for d in json.loads(Path(decisions_path).read_text())["decisions"] if d["date"] == day and d.get("source") == "ta"]
    state, held = load(state_path), TB.held_qty(broker)
    pos = {p.symbol: float(p.current_price) for p in broker.get_all_positions()}
    build = build or P.build
    plans, _ = nightly(broker, state, decs, pos, build, held, sleep)
    ev = protect_all(broker, state, now=datetime.now(timezone.utc), sleep=sleep)
    save(state, state_path)
    return plans, ev


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--adopt", metavar="DATE")
    a = ap.parse_args(argv)
    if a.adopt:
        plans, ev = adopt(a.adopt)
        for s, p in plans.items():
            print(P.plan_line(s, p), "| not executed:", p["not_executed"])
        print("\n".join(ev))
        return 0
    try:
        return monitor()
    except Exception as ex:   # never fail the workflow on a transient broker/data error
        print(f"ta_monitor: failed ({type(ex).__name__}): {str(ex)[:120]}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
