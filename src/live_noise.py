"""Live paper trading of the Noise-Band Breakout on SPY and QQQ in the Alpaca PAPER account.

Rules = the backtest's tuning-year choice (INTRADAY_PLAN.md; lookback 14 days, hourly checks):
  sigma(m) = average |close(m)/open - 1| at minute m over the last 14 complete sessions
  upper = max(today's open, yesterday's close) x (1 + sigma), lower = min(open, prev close) x (1 - sigma)
  at 10:00, 11:00, ... 15:00 New York: long if price > max(upper, VWAP), short if < min(lower, VWAP), else flat
  flat at the close (market-on-close order, i.e. the official closing price, like the backtest)

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
FLATTEN = (15, 46)  # market-on-close orders must be in before 15:50
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
    """Pure decision for one --once run: a list of actions {sym, kind, point[, last]}.
    kind: safety (close a position left from an earlier day), check (act on a check point), flatten_moc / flatten_market
    (backstop at the 15:46 point). A check point is acted on once: the latest past point, only if not already in `done`."""
    past = [p for p in pts if p <= now]
    out = []
    for sym, st in states.items():
        # shares held but today's state expects none and no close order is pending: left from an earlier day
        if held.get(sym, 0) and not st.get("qty") and not st.get("moc"):
            out.append({"sym": sym, "kind": "safety", "point": ""})
        if not past:
            continue
        label = f"{past[-1]:%H:%M}"
        if past[-1] == pts[-1]:  # backstop point: only if the 15:00 check did not already send the close order
            if st.get("qty") and not st.get("moc"):
                out.append({"sym": sym, "kind": "flatten_moc" if now < now.replace(hour=15, minute=50, second=0, microsecond=0)
                            else "flatten_market" if now < now.replace(hour=16, minute=0, second=0, microsecond=0) else "none",
                            "point": label})
        elif label not in st.get("done", []):
            out.append({"sym": sym, "kind": "check", "point": label, "last": past[-1] == pts[-2]})
    return [a for a in out if a["kind"] != "none"]


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


def wait_filled(trading, oid, timeout=60):
    """Wait until an order is no longer open (filled, cancelled or rejected); market orders fill in seconds."""
    end = time.time() + timeout
    while time.time() < end:
        if str(trading.get_order_by_id(oid).status).split(".")[-1].lower() in ("filled", "canceled", "rejected", "expired"):
            return
        time.sleep(1)
    raise RuntimeError(f"order {oid} still open after {timeout}s")


def act_check(trading, data_client, now, fraction, sym, point=None, last=False):
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
    save_state(sym, st)  # saved before the close order so a repeat run never acts on this point twice
    if last and target:  # last check: send the market-on-close order now, no separate run needed
        if oid:  # Alpaca rejects an opposite-side order while the entry is still open ("potential wash trade")
            wait_filled(trading, oid)
        oid = submit(trading, -target, "cls", sym)
        log({"time_ny": stamp, "symbol": sym, "event": "flatten at close", "qty_before": target, "qty_after": 0,
             "order_id": oid, "note": "market-on-close order, sent with the last check"})
        st["qty"], st["moc"] = 0, True
        save_state(sym, st)
    elif last:
        st["moc"] = True
        save_state(sym, st)


def act_flatten(trading, now, sym, kind="flatten_moc"):
    st = load_state(now.date(), sym)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    if st["qty"] and not st["moc"]:
        market = kind == "flatten_market"
        oid = submit(trading, -st["qty"], "day" if market else "cls", sym)
        log({"time_ny": stamp, "symbol": sym, "event": "flatten at close", "qty_before": st["qty"], "qty_after": 0, "order_id": oid,
             "note": "plain market order (after 15:50, too late for market-on-close)" if market else "market-on-close order (backstop)"})
        st["qty"], st["moc"] = 0, not market
        save_state(sym, st)
    else:
        log({"time_ny": stamp, "symbol": sym, "event": "flatten at close", "qty_before": 0, "qty_after": 0, "note": "already flat"})


def held_qty(trading):
    """Shares the intraday account holds in our symbols (negative = short)."""
    return {p.symbol: int(float(p.qty)) for p in trading.get_all_positions() if p.symbol in SYMBOLS}


def act_safety(trading, now, sym, qty):
    submit(trading, -qty, "day", sym)
    st = load_state(now.date(), sym)
    st["qty"] = 0
    save_state(sym, st)
    log({"time_ny": now.strftime("%Y-%m-%d %H:%M:%S"), "symbol": sym, "event": "safety flatten (left over from an earlier day)",
         "qty_before": qty, "qty_after": 0, "note": "market order"})


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
    ap.add_argument("--fraction", type=float, default=0.5)
    ap.add_argument("--until", default=None, help="stop after this New York time, HH:MM")
    a = ap.parse_args()
    trading, data_client = clients()
    if not trading.get_clock().is_open:
        print("market is closed; nothing to do")
        return
    now = datetime.now(NY)
    pts = schedule(now)
    if a.once:
        states = {sym: load_state(now.date(), sym) for sym in SYMBOLS}
        held = held_qty(trading)
        failed = []
        for act in plan_once(now, pts, states, held):
            sym = act["sym"]
            try:  # one symbol failing must not stop the other one
                if act["kind"] == "safety":
                    act_safety(trading, now, sym, held[sym])
                elif act["kind"] == "check":
                    act_check(trading, data_client, now, a.fraction, sym, act["point"], act["last"])
                else:
                    act_flatten(trading, now, sym, act["kind"])
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
        for sym in SYMBOLS:
            if i == len(pts) - 1:
                act_flatten(trading, now, sym)
            else:
                act_check(trading, data_client, now, a.fraction, sym, f"{p:%H:%M}", i == len(CHECKS) - 1)


if __name__ == "__main__":
    main()
