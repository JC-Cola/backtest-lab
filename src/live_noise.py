"""Live paper trading of the Noise-Band Breakout on QQQ in the Alpaca PAPER account.

Rules = the backtest's tuning-year choice (INTRADAY_PLAN.md; lookback 14 days, hourly checks):
  sigma(m) = average |close(m)/open - 1| at minute m over the last 14 complete sessions
  upper = max(today's open, yesterday's close) x (1 + sigma), lower = min(open, prev close) x (1 - sigma)
  at 10:00, 11:00, ... 15:00 New York: long if price > max(upper, VWAP), short if < min(lower, VWAP), else flat
  flat at the close (market-on-close order, i.e. the official closing price, like the backtest)

Sizing: a sleeve of FRACTION x account equity (default 25%), traded ON TOP of the daily strategies'
positions and always flat by the close, so the nightly daily-strategy orders never see it.
Differences from the backtest, stated honestly: today's price and VWAP come from Alpaca's free IEX feed
(IEX volume only; the full-market feed is not available for the last 15 minutes on the free plan), and
fills are at the live price a few seconds after the check, not exactly the next minute's open.

    python src/live_noise.py            # run until the close, acting at each check
    python src/live_noise.py --once     # act on the most recent check only (for cron, hourly at :00:30)
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
STATE = ROOT / "agent_memory" / "noise_state.json"
LOG = ROOT / "agent_memory" / "noise_live.csv"
NY = ZoneInfo("America/New_York")
SYMBOL, LOOKBACK = "QQQ", 14
CHECKS = [(10, 0), (11, 0), (12, 0), (13, 0), (14, 0), (15, 0)]
FLATTEN = (15, 46)  # market-on-close orders must be in before 15:50
FIELDS = ["time_ny", "event", "price", "vwap", "upper", "lower", "decision", "qty_before", "qty_after", "order_id", "note"]


def clients():
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient
    k, s = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
    return TradingClient(k, s, paper=True), StockHistoricalDataClient(k, s)


def history_bands(data_client, today):
    """sigma per minute from the last LOOKBACK complete sessions (full-market SIP, older than today)."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    start = datetime.combine(today - timedelta(days=LOOKBACK * 2 + 10), datetime.min.time(), NY)
    end = datetime.combine(today, datetime.min.time(), NY)
    df = data_client.get_stock_bars(StockBarsRequest(symbol_or_symbols=SYMBOL, timeframe=TimeFrame.Minute, start=start,
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


def today_bars(data_client, now):
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    df = data_client.get_stock_bars(StockBarsRequest(symbol_or_symbols=SYMBOL, timeframe=TimeFrame.Minute, start=start,
                                                     end=now, feed=DataFeed.IEX)).df.droplevel(0)
    df.index = df.index.tz_convert(NY)
    return df[df.index < now.replace(second=0, microsecond=0)]  # completed bars only


def load_state(today):
    try:
        s = json.loads(STATE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        s = {}
    if s.get("date") != str(today):
        s = {"date": str(today), "qty": 0}
    return s


def log(row):
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})
    print(" | ".join(f"{k}={row[k]}" for k in FIELDS if row.get(k) not in (None, "")), flush=True)


def submit(trading, qty, tif):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
    side = OrderSide.BUY if qty > 0 else OrderSide.SELL
    o = trading.submit_order(MarketOrderRequest(symbol=SYMBOL, qty=abs(qty), side=side,
                                                time_in_force=TimeInForce.CLS if tif == "cls" else TimeInForce.DAY,
                                                client_order_id=f"noise-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"))
    return str(o.id)


def act_check(trading, data_client, now, fraction):
    today = now.date()
    sigma, prev_close, last_day = history_bands(data_client, today)
    bars = today_bars(data_client, now)
    if bars.empty:
        log({"time_ny": now.strftime("%Y-%m-%d %H:%M:%S"), "event": "check", "note": "no bars yet"})
        return
    o = bars.open.iloc[0]
    m = (now.hour * 60 + now.minute) - 570 - 1          # the bar that just completed
    p = float(bars.close.iloc[-1])
    tp = (bars.high + bars.low + bars.close) / 3
    vwap = float((tp * bars.volume).sum() / bars.volume.sum()) if bars.volume.sum() > 0 else p
    up = max(o, prev_close) * (1 + sigma[min(m, 389)])
    dn = min(o, prev_close) * (1 - sigma[min(m, 389)])
    side = 1 if p > max(up, vwap) else (-1 if p < min(dn, vwap) else 0)
    st = load_state(today)
    equity = float(trading.get_account().equity)
    target = side * math.floor(equity * fraction / p)
    delta = target - st["qty"]
    oid = submit(trading, delta, "day") if delta else ""
    log({"time_ny": now.strftime("%Y-%m-%d %H:%M:%S"), "event": "check", "price": round(p, 2), "vwap": round(vwap, 2),
         "upper": round(up, 2), "lower": round(dn, 2), "decision": {1: "long", -1: "short", 0: "flat"}[side],
         "qty_before": st["qty"], "qty_after": target, "order_id": oid,
         "note": f"bands from {LOOKBACK} sessions to {last_day}; prev close {prev_close:.2f}; open {o:.2f} (IEX)"})
    st["qty"] = target
    STATE.write_text(json.dumps(st))


def act_flatten(trading, now):
    st = load_state(now.date())
    if st["qty"]:
        oid = submit(trading, -st["qty"], "cls")
        log({"time_ny": now.strftime("%Y-%m-%d %H:%M:%S"), "event": "flatten at close", "qty_before": st["qty"],
             "qty_after": 0, "order_id": oid, "note": "market-on-close order"})
        st["qty"] = 0
        STATE.write_text(json.dumps(st))
    else:
        log({"time_ny": now.strftime("%Y-%m-%d %H:%M:%S"), "event": "flatten at close", "qty_before": 0, "qty_after": 0,
             "note": "already flat"})


def schedule(now):
    day = now.date()
    pts = [datetime.combine(day, datetime.min.time(), NY).replace(hour=h, minute=mi, second=20) for h, mi in CHECKS]
    pts.append(datetime.combine(day, datetime.min.time(), NY).replace(hour=FLATTEN[0], minute=FLATTEN[1], second=0))
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--fraction", type=float, default=0.25)
    ap.add_argument("--until", default=None, help="stop after this New York time, HH:MM")
    a = ap.parse_args()
    trading, data_client = clients()
    if not trading.get_clock().is_open:
        print("market is closed; nothing to do")
        return
    now = datetime.now(NY)
    pts = schedule(now)
    if a.once:
        past = [p for p in pts if p <= now]
        if not past:
            print("before the first check (10:00 New York)")
            return
        p = past[-1]
        (act_flatten(trading, now) if p == pts[-1] else act_check(trading, data_client, now, a.fraction))
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
        if i == len(pts) - 1:
            act_flatten(trading, now)
        else:
            act_check(trading, data_client, now, a.fraction)


if __name__ == "__main__":
    main()
