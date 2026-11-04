"""One-shot break-even exit for the Alpaca PAPER account's daily-strategy positions (asked for on 2026-11-03).

Each watched symbol is closed the first time its price is back at or above the average entry price recorded
when the watch was set (below it, for a short), then dropped from the watch list, so the strategies may buy
it again later. Only the daily strategies' shares are closed: the QQQ noise-band sleeve (live_noise.py) is
left alone. Stocks are closed with market orders while the market is open; Bitcoin at any time.

    python src/breakeven_exit.py --init    # record the current positions and their entry prices
    python src/breakeven_exit.py           # check once, close what has broken even (launchd, every 5 min)
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import live_noise

ROOT = live_noise.ROOT
WATCH = ROOT / "agent_memory" / "breakeven_watch.json"
LOG = ROOT / "agent_memory" / "breakeven_log.csv"
NY = ZoneInfo("America/New_York")
FIELDS = ["time_ny", "symbol", "basis", "price", "qty_closed", "order_id", "note"]


def _sym(p):  # positions report BTCUSD; orders accept BTC/USD
    return "BTC/USD" if p.symbol == "BTCUSD" else p.symbol


def log(row):
    new = not LOG.exists()
    with LOG.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def init(trading):
    watch = {_sym(p): {"basis": float(p.avg_entry_price), "side": 1 if float(p.qty) > 0 else -1}
             for p in trading.get_all_positions()}
    WATCH.write_text(json.dumps({"set": datetime.now(NY).isoformat(timespec="seconds"), "symbols": watch}, indent=1))
    print(f"watching {watch}")


def check(trading):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
    if not WATCH.exists():
        print("no watch list; run with --init")
        return
    w = json.loads(WATCH.read_text())
    now = datetime.now(NY)
    is_open = trading.get_clock().is_open
    held = {_sym(p): p for p in trading.get_all_positions()}
    for sym, spec in list(w["symbols"].items()):
        p = held.get(sym)
        sleeve = live_noise.load_state(now.date())["qty"] if sym == live_noise.SYMBOL else 0
        qty = (float(p.qty) if p else 0.0) - sleeve
        if qty * spec["side"] <= 0:
            log({"time_ny": f"{now:%Y-%m-%d %H:%M:%S}", "symbol": sym, "basis": spec["basis"], "qty_closed": 0,
                 "note": "no daily-strategy position left; stopped watching"})
            del w["symbols"][sym]
            continue
        crypto = "/" in sym
        if not (crypto or is_open):
            continue
        price = float(p.current_price)
        if (price - spec["basis"]) * spec["side"] < 0:
            print(f"{sym}: {price} vs entry {spec['basis']}, still below break-even")
            continue
        o = trading.submit_order(MarketOrderRequest(symbol=sym, qty=round(abs(qty), 9), side=OrderSide.SELL if qty > 0 else OrderSide.BUY,
                                                    time_in_force=TimeInForce.GTC if crypto else TimeInForce.DAY))
        log({"time_ny": f"{now:%Y-%m-%d %H:%M:%S}", "symbol": sym, "basis": spec["basis"], "price": price,
             "qty_closed": abs(qty), "order_id": str(o.id), "note": "broke even; closed"})
        print(f"{sym}: closed {abs(qty)} at about {price} (entry {spec['basis']})")
        del w["symbols"][sym]
    WATCH.write_text(json.dumps(w, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    a = ap.parse_args()
    trading, _ = live_noise.clients()
    init(trading) if a.init else check(trading)
