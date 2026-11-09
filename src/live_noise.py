"""Live paper trading of the Noise-Band Breakout on SPY and QQQ in the Alpaca PAPER account.

Rules = the backtest's tuning-year choice (INTRADAY_PLAN.md; lookback 14 days, hourly checks):
  sigma(m) = average |close(m)/open - 1| at minute m over the last 14 complete sessions
  upper = max(today's open, yesterday's close) x (1 + sigma), lower = min(open, prev close) x (1 - sigma)
  at 10:00, 11:00, ... 15:00 New York: long if price > max(upper, VWAP), short if < min(lower, VWAP), else flat
  flat at the close: a plain market (DAY) order in the run at ~15:56 New York (no market-on-close orders: Alpaca paper
  let CLS orders expire unfilled). The backtest closes at the official close; this is a few minutes earlier.

Sizing: its own $25k INTRADAY paper account (keys ALPACA_INTRADAY_API_KEY / ALPACA_INTRADAY_SECRET_KEY), FRACTION
x equity per symbol (default 50%, so SPY + QQQ use the whole account), always flat by the close. The daily
strategies trade a separate $25k account (alpaca_paper.py).
Differences from the backtest, stated honestly: today's price and VWAP come from Alpaca's free IEX feed
(IEX volume only; the full-market feed is not available for the last 15 minutes on the free plan), and
fills are at the live price a few seconds after the check, not exactly the next minute's open.

    python src/live_noise.py            # run until the close, acting at each check
    python src/live_noise.py --once     # act on the latest check point if not done yet (safe to run every 30 min, twice, or late)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "agent_memory" / "noise_live.csv"
SNAPSHOT = ROOT / "agent_memory" / "alpaca_intraday.json"
NY = ZoneInfo("America/New_York")
SYMBOLS, LOOKBACK = ["SPY", "QQQ"], 14
CHECKS = [(10, 0), (11, 0), (12, 0), (13, 0), (14, 0), (15, 0)]
FLATTEN = (15, 55)  # plain market order for the close; the cloud run at ~15:56 acts on this point
RISK = ROOT / "agent_memory" / "noise_risk.json"
START_EQUITY = 25000.0
DAILY_STOP = 0.03   # flatten and stop for the day when equity is this far below the previous close's equity
KILL_DD = 0.12      # flatten and stop until --rearm when equity is this far below the account's peak equity
FIELDS = ["time_ny", "symbol", "event", "price", "vwap", "upper", "lower", "decision", "qty_before", "qty_after", "order_id", "note"]


def clients():
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient
    k, s = os.environ.get("ALPACA_INTRADAY_API_KEY"), os.environ.get("ALPACA_INTRADAY_SECRET_KEY")
    if not (k and s):
        raise SystemExit("ALPACA_INTRADAY_API_KEY / ALPACA_INTRADAY_SECRET_KEY not set (the intraday account's keys)")
    return TradingClient(k, s, paper=True), StockHistoricalDataClient(k, s)


def history_bands(data_client, today, sym):
    """sigma per minute from the last LOOKBACK complete sessions (full-market SIP, older than today)."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    start = datetime.combine(today - timedelta(days=LOOKBACK * 2 + 10), datetime.min.time(), NY)
    end = datetime.combine(today, datetime.min.time(), NY)
    df = data_client.get_stock_bars(StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, start=start,
                                                     end=end, feed=DataFeed.SIP, adjustment=Adjustment.ALL)).df.droplevel(0)
    df.index = df.index.tz_convert(NY)
    t = df.index.hour * 60 + df.index.minute
    df = df[(t >= 570) & (t < 960)]
    days = sorted(set(df.index.date))[-LOOKBACK:]
    moves, prev_close = [], None
    for d in days:
        x = df[df.index.date == d]
        o = x.open.iloc[0]
        m = ((x.index.hour * 60 + x.index.minute) - 570).values
        row = np.full(390, np.nan)
        row[m] = np.abs(x.close.values / o - 1)
        moves.append(pd_ffill(row))
        prev_close = x.close.iloc[-1]
    return np.nanmean(np.array(moves), axis=0), prev_close, days[-1]


def pd_ffill(a):
    out = a.copy()
    for i in range(1, len(out)):
        if np.isnan(out[i]):
            out[i] = out[i - 1]
    return out


def today_bars(data_client, now, sym):
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    df = data_client.get_stock_bars(StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute, start=start,
                                                     end=now, feed=DataFeed.IEX)).df.droplevel(0)
    df.index = df.index.tz_convert(NY)
    return df[df.index < now.replace(second=0, microsecond=0)]  # completed bars only


def state_path(sym):
    return ROOT / "agent_memory" / f"noise_state_{sym}.json"


def load_state(today, sym):
    try:
        s = json.loads(state_path(sym).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        s = {}
    if s.get("date") != str(today):
        s = {"date": str(today), "qty": 0}
    s.setdefault("done", [])
    s.setdefault("moc", False)
    return s


def save_state(sym, st):
    state_path(sym).write_text(json.dumps(st))


def plan_once(now, pts, states, held):
    """Pure decision for one --once run: a list of actions {sym, kind, point[, qty]}.
    kind: safety (close a position left from an earlier day), check (act on a check point), flatten_market (close the
    day's position with a plain market order at the flatten point, before 16:00), mismatch (account and state disagree:
    only logged, never traded). A check point is acted on once: the latest past point, only if not already in `done`.
    State key `moc` means "closed for the day" (kept for compatibility with existing state files)."""
    past = [p for p in pts if p <= now]
    close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    out = []
    for sym, st in states.items():
        h, q, closed = held.get(sym, 0), st.get("qty", 0), st.get("moc")
        at_flatten = bool(past) and past[-1] == pts[-1]
        label = f"{past[-1]:%H:%M}" if past else ""
        if at_flatten and not closed and h and now < close:
            # the account holds shares (state agrees or not, e.g. a crashed run): close what is really held
            out.append({"sym": sym, "kind": "flatten_market", "point": label, "qty": h})
            continue
        if h and not q and not closed:  # shares held but today's state expects none: left from an earlier day
            out.append({"sym": sym, "kind": "safety", "point": ""})
        elif h != q and now < close:
            out.append({"sym": sym, "kind": "mismatch", "point": label, "held": h, "state": q})
        if past and not at_flatten and label not in st.get("done", []):
            out.append({"sym": sym, "kind": "check", "point": label})
    return out


def log(row):
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})
    print(" | ".join(f"{k}={row[k]}" for k in FIELDS if row.get(k) not in (None, "")), flush=True)


def submit(trading, qty, tif, sym):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
    side = OrderSide.BUY if qty > 0 else OrderSide.SELL
    o = trading.submit_order(MarketOrderRequest(symbol=sym, qty=abs(qty), side=side,
                                                time_in_force=TimeInForce.CLS if tif == "cls" else TimeInForce.DAY,
                                                client_order_id=f"noise-{sym}-{tif}-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}"))
    return str(o.id)


def act_check(trading, data_client, now, fraction, sym, point=None):
    today = now.date()
    sigma, prev_close, last_day = history_bands(data_client, today, sym)
    bars = today_bars(data_client, now, sym)
    if bars.empty:
        log({"time_ny": now.strftime("%Y-%m-%d %H:%M:%S"), "symbol": sym, "event": "check", "note": "no bars yet"})
        return
    o = bars.open.iloc[0]
    m = (now.hour * 60 + now.minute) - 570 - 1          # the bar that just completed
    p = float(bars.close.iloc[-1])
    tp = (bars.high + bars.low + bars.close) / 3
    vwap = float((tp * bars.volume).sum() / bars.volume.sum()) if bars.volume.sum() > 0 else p
    up = max(o, prev_close) * (1 + sigma[min(m, 389)])
    dn = min(o, prev_close) * (1 - sigma[min(m, 389)])
    side = 1 if p > max(up, vwap) else (-1 if p < min(dn, vwap) else 0)
    st = load_state(today, sym)
    equity = float(trading.get_account().equity)
    target = side * math.floor(equity * fraction / p)
    delta = target - st["qty"]
    oid = submit(trading, delta, "day", sym) if delta else ""
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    log({"time_ny": stamp, "symbol": sym, "event": "check", "price": round(p, 2), "vwap": round(vwap, 2),
         "upper": round(up, 2), "lower": round(dn, 2), "decision": {1: "long", -1: "short", 0: "flat"}[side],
         "qty_before": st["qty"], "qty_after": target, "order_id": oid,
         "note": f"bands from {LOOKBACK} sessions to {last_day}; prev close {prev_close:.2f}; open {o:.2f} (IEX)"})
    st["qty"] = target
    st["done"] = sorted(set(st["done"]) | {point or f"{now:%H:%M}"})
    save_state(sym, st)


def act_flatten(trading, now, sym, qty=None):
    """Close the day's position with a plain market (DAY) order. qty = shares held (default: the state's qty)."""
    st = load_state(now.date(), sym)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    qty = st["qty"] if qty is None else qty
    if qty and not st["moc"]:
        oid = submit(trading, -qty, "day", sym)
        log({"time_ny": stamp, "symbol": sym, "event": "flatten at close", "qty_before": qty, "qty_after": 0, "order_id": oid,
             "note": "plain market order"})
        st["qty"], st["moc"] = 0, True  # moc = closed for the day: a repeat run must not close twice
        save_state(sym, st)
    else:
        log({"time_ny": stamp, "symbol": sym, "event": "flatten at close", "qty_before": 0, "qty_after": 0, "note": "already flat"})


def act_mismatch(now, sym, held, state):
    log({"time_ny": now.strftime("%Y-%m-%d %H:%M:%S"), "symbol": sym, "event": "position mismatch", "qty_before": state,
         "qty_after": held, "note": f"account holds {held}, state says {state}; no trade"})


def held_qty(trading):
    """Shares the intraday account holds in our symbols (negative = short)."""
    return {p.symbol: int(float(p.qty)) for p in trading.get_all_positions() if p.symbol in SYMBOLS}


def cancel_open(trading, sym):
    """Cancel this symbol's open orders (e.g. a resting break-even limit) so a market close cannot double up."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    for o in trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[sym])):
        trading.cancel_order_by_id(o.id)
        log({"time_ny": datetime.now(NY).strftime("%Y-%m-%d %H:%M:%S"), "symbol": sym, "event": "cancel open order",
             "order_id": str(o.id), "note": "cancelled before a market close"})


def act_safety(trading, now, sym, qty):
    cancel_open(trading, sym)
    held = held_qty(trading).get(sym, 0)  # a resting order may have filled meanwhile
    if not held:
        log({"time_ny": now.strftime("%Y-%m-%d %H:%M:%S"), "symbol": sym, "event": "safety flatten (left over from an earlier day)",
             "qty_before": qty, "qty_after": 0, "note": "already closed by its resting order"})
        return
    qty = held
    submit(trading, -qty, "day", sym)
    st = load_state(now.date(), sym)
    st["qty"] = 0
    save_state(sym, st)
    log({"time_ny": now.strftime("%Y-%m-%d %H:%M:%S"), "symbol": sym, "event": "safety flatten (left over from an earlier day)",
         "qty_before": qty, "qty_after": 0, "note": "market order"})


# ---------- emergency loss limits (daily stop, drawdown kill switch) ----------
def risk_action(equity, last_equity, peak, state, today):
    """Pure risk decision: "ok" | "daily_stop" | "kill" | "halted" | "killed".
    killed (persisted flag) and kill (equity <= peak x (1 - KILL_DD)) outrank everything; halted = a daily stop already
    fired on `today` (state["date"]); daily_stop = equity <= last_equity x (1 - DAILY_STOP). A missing, zero or negative
    equity / last_equity / peak disables the threshold that needs it (no false stop); persisted flags still apply."""
    state = state or {}
    pos = lambda x: isinstance(x, (int, float)) and math.isfinite(x) and x > 0
    if state.get("killed"):
        return "killed"
    if pos(equity) and pos(peak) and equity <= peak * (1 - KILL_DD) + 1e-9:
        return "kill"
    if state.get("halted_day") and state.get("date") == str(today):
        return "halted"
    if pos(equity) and pos(last_equity) and equity <= last_equity * (1 - DAILY_STOP) + 1e-9:
        return "daily_stop"
    return "ok"


def load_risk():
    try:
        r = json.loads(RISK.read_text())
        return r if isinstance(r, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_risk(r):
    RISK.write_text(json.dumps(r, indent=1, sort_keys=True))


def initial_peak(equity):
    """max(start equity, current equity, highest equity in the account snapshot's calendar/history if present)."""
    vals = [START_EQUITY, equity or 0]
    try:
        d = json.loads(SNAPSHOT.read_text())
        vals += [c.get("equity") for c in d.get("calendar") or [] if isinstance(c, dict)]
        for series in (d.get("history") or {}).values():
            vals += [pt[1] for pt in series if isinstance(pt, (list, tuple)) and len(pt) > 1]
    except (OSError, ValueError, AttributeError):
        pass
    return max(v for v in vals if isinstance(v, (int, float)) and math.isfinite(v))


def alert(text):
    try:
        import telegram
        telegram.send(text)
    except Exception as e:  # an alert must never block the close
        print(f"alert failed: {type(e).__name__}", flush=True)


def risk_flatten(trading, now, why):
    """Cancel open orders and close what the account really holds, per symbol. Returns the symbols that failed.
    If an open order cannot be cancelled (already filling) the symbol is skipped this run: never risk a second close."""
    failed = []
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    for sym in SYMBOLS:
        try:
            in_flight = False
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest
            for o in trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[sym])):
                try:
                    trading.cancel_order_by_id(o.id)
                    log({"time_ny": stamp, "symbol": sym, "event": "cancel open order", "order_id": str(o.id), "note": f"cancelled: {why}"})
                except Exception as e:
                    in_flight = True
                    print(f"{sym} cancel {o.id} failed: {e}", flush=True)
            held = held_qty(trading).get(sym, 0)
            if in_flight:
                log({"time_ny": stamp, "symbol": sym, "event": "risk flatten", "qty_before": held, "note": "order in flight; retry next run"})
                continue
            oid = submit(trading, -held, "day", sym) if held else ""
            log({"time_ny": stamp, "symbol": sym, "event": "risk flatten", "qty_before": held, "qty_after": 0, "order_id": oid,
                 "note": f"{why}; market order" if held else f"{why}; already flat"})
            st = load_state(now.date(), sym)
            st["qty"], st["moc"] = 0, True
            save_state(sym, st)
        except Exception as e:
            print(f"{sym} risk flatten failed: {e}", flush=True)
            failed.append(sym)
    return failed


def risk_step(trading, now, pts):
    """Run before planning. Returns (proceed, failed): proceed False = no checks/entries this run (positions are
    flattened here if the limits fired or leftovers exist)."""
    today = now.date()
    rs = load_risk()
    try:
        acct = trading.get_account()
        equity = float(acct.equity)
        last = float(getattr(acct, "last_equity", 0) or 0)
    except Exception as e:
        print(f"risk: account read failed ({type(e).__name__}); persisted flags only", flush=True)
        equity = last = None
    before = dict(rs)
    if "peak" not in rs:
        rs["peak"] = initial_peak(equity)
    if equity:
        rs["peak"] = max(rs["peak"], equity)
    act = risk_action(equity, last, rs["peak"], rs, today)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    failed = []
    if act in ("daily_stop", "kill"):
        kill = act == "kill"
        why = "kill switch" if kill else "daily loss stop"
        failed = risk_flatten(trading, now, why)
        ref = rs["peak"] if kill else last
        pct = (equity / ref - 1) * 100
        base = {"time": stamp, "equity": round(equity, 2)}
        if kill:
            rs.update(killed=True, kill_time=stamp, kill_equity=round(equity, 2), kill_peak=round(rs["peak"], 2))
        else:
            past = [p for p in pts if p <= now]
            rs.update(halt_logged=f"{past[-1]:%H:%M}" if past else "", date=str(today), halted_day=True, reason=why, last_equity=round(last, 2), **base)
        save_risk(rs)  # before the alert: a crash must not make the next run alert again
        log({"time_ny": stamp, "symbol": ",".join(SYMBOLS), "event": why, "note": f"equity {equity:.2f} vs {'peak' if kill else 'last equity'} {ref:.2f} ({pct:+.1f}%)"})
        if kill:
            alert(f"\U0001F6D1 Intraday KILL SWITCH: \u2212{abs(pct):.1f}% from peak (${equity:,.0f} vs peak ${ref:,.0f}). Trading stopped until restarted.")
        else:
            alert(f"\U0001F6D1 Intraday daily loss stop: \u2212{abs(pct):.1f}% today (${equity:,.0f} vs ${last:,.0f}). Positions closed; trading resumes tomorrow.")
        return False, failed
    if act in ("halted", "killed"):
        if held_qty(trading):  # positions must never be stranded, whatever the state
            failed = risk_flatten(trading, now, "leftover position while " + ("halted" if act == "halted" else "killed"))
        if act == "halted":
            past = [p for p in pts if p <= now]
            label = f"{past[-1]:%H:%M}" if past else ""
            if rs.get("halt_logged") != label:
                rs["halt_logged"] = label
                log({"time_ny": stamp, "event": "halted (daily loss stop)", "note": "no entries today; resumes next session"})
    if rs != before:
        save_risk(rs)
    return act == "ok", failed


def rearm(trading, now):
    rs = load_risk()
    equity = float(trading.get_account().equity)
    for k in ("killed", "kill_time", "kill_equity", "kill_peak"):
        rs.pop(k, None)
    rs["peak"] = equity
    save_risk(rs)
    log({"time_ny": now.strftime("%Y-%m-%d %H:%M:%S"), "event": "rearm", "note": f"kill switch cleared; peak reset to {equity:.2f}"})


def snapshot(trading):
    """The intraday account's state for the Strategy Lab page."""
    import alpaca_paper
    try:
        alpaca_paper.snapshot(trading, path=SNAPSHOT, behind={s: [{"key": "ID-NOISE", "name": "Noise-Band Breakout", "position": 1.0,
                                                                     "decided": "each hourly check"}] for s in SYMBOLS})
    except Exception as e:  # the page can wait; trading must not
        print(f"snapshot failed: {e}", flush=True)


def schedule(now):
    day = now.date()
    pts = [datetime.combine(day, datetime.min.time(), NY).replace(hour=h, minute=mi, second=20) for h, mi in CHECKS]
    pts.append(datetime.combine(day, datetime.min.time(), NY).replace(hour=FLATTEN[0], minute=FLATTEN[1], second=0))
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--rearm", action="store_true", help="clear the kill switch and reset the peak equity to the current equity")
    ap.add_argument("--fraction", type=float, default=0.5)
    ap.add_argument("--until", default=None, help="stop after this New York time, HH:MM")
    a = ap.parse_args()
    trading, data_client = clients()
    if a.rearm:
        rearm(trading, datetime.now(NY))
        return
    if not trading.get_clock().is_open:
        print("market is closed; nothing to do")
        return
    now = datetime.now(NY)
    pts = schedule(now)
    if a.once:
        proceed, failed = risk_step(trading, now, pts)
        if not proceed:
            snapshot(trading)
            if failed:
                raise SystemExit(f"failed: {', '.join(failed)}")
            return
        states = {sym: load_state(now.date(), sym) for sym in SYMBOLS}
        held = held_qty(trading)
        for act in plan_once(now, pts, states, held):
            sym = act["sym"]
            try:  # one symbol failing must not stop the other one
                if act["kind"] == "safety":
                    act_safety(trading, now, sym, held[sym])
                elif act["kind"] == "check":
                    act_check(trading, data_client, now, a.fraction, sym, act["point"])
                elif act["kind"] == "mismatch":
                    act_mismatch(now, sym, act["held"], act["state"])
                else:
                    act_flatten(trading, now, sym, act["qty"])
            except Exception as e:
                print(f"{sym} {act['kind']} failed: {e}", flush=True)
                failed.append(sym)
        snapshot(trading)
        if failed:
            raise SystemExit(f"failed: {', '.join(failed)}")
        return
    stop = None
    if a.until:
        h, mi = map(int, a.until.split(":"))
        stop = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    for i, p in enumerate(pts):
        if p <= now - timedelta(minutes=2):
            continue
        if stop and p > stop:
            print(f"stopping before {p:%H:%M} (--until {a.until})", flush=True)
            return
        wait = (p - datetime.now(NY)).total_seconds()
        if wait > 0:
            print(f"next: {'flatten' if i == len(pts) - 1 else 'check'} at {p:%H:%M:%S} New York (in {wait / 60:.0f} min)", flush=True)
            time.sleep(wait)
        now = datetime.now(NY)
        if not risk_step(trading, now, pts)[0]:
            continue
        if i == len(pts) - 1:  # same rule as --once: close what the account really holds, market order, before 16:00
            states = {sym: load_state(now.date(), sym) for sym in SYMBOLS}
            for act in plan_once(now, pts, states, held_qty(trading)):
                if act["kind"] == "flatten_market":
                    act_flatten(trading, now, act["sym"], act["qty"])
                elif act["kind"] == "mismatch":
                    act_mismatch(now, act["sym"], act["held"], act["state"])
        else:
            for sym in SYMBOLS:
                act_check(trading, data_client, now, a.fraction, sym, f"{p:%H:%M}")


if __name__ == "__main__":
    main()
