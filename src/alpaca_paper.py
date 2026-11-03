"""Mirror the lab's daily paper-trading decisions into an Alpaca PAPER account (visible in TradingView).

    python src/alpaca_paper.py --dry-run   # show the orders it would place (no keys needed)
    python src/alpaca_paper.py             # place them (needs ALPACA_API_KEY / ALPACA_SECRET_KEY)

How it sizes: each strategy in the Strategy Lab book gets an equal sleeve of account equity; a
strategy that is "in" holds its sleeve in its market, "cash" holds nothing. Sleeves on the same
symbol are added together, then the account is moved to those targets.
Timing matches the backtest: decisions come from paper_log.csv (made on the last COMPLETE daily
bar); stock orders are market-on-open (time in force OPG) so they fill at the next open; the
Bitcoin order is sent immediately (crypto has no open/close; the scheduled run is at 00:23 UTC,
right after the daily bar closes).
Safety: paper=True is hard-coded and the account must report itself as a paper account.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "agent_memory" / "paper_log.csv"
ORDERS = ROOT / "agent_memory" / "alpaca_orders.csv"
SNAPSHOT = ROOT / "agent_memory" / "alpaca_account.json"
START_EQUITY = 100_000.0
SYMBOL = {"SPY": "SPY", "QQQ": "QQQ", "GLD": "GLD", "IWM": "IWM", "BTC-USD": "BTC/USD"}
MIN_TRADE_USD = 50.0  # ignore tiny rebalances


def latest_decisions() -> dict:
    """Most recent logged decision per book strategy (agent ideas are not traded)."""
    rows = list(csv.DictReader(LOG.open())) if LOG.exists() else []
    last = {}
    for r in rows:
        if r["key"].startswith("AGENT-"):
            continue
        if r["key"] not in last or r["decision_date"] > last[r["key"]]["decision_date"]:
            last[r["key"]] = r
    return last


def targets(decisions: dict, equity: float) -> dict:
    """Dollar target per Alpaca symbol."""
    sleeve = equity / max(1, len(decisions))
    out = {}
    for r in decisions.values():
        sym = SYMBOL[r["market"]]
        out[sym] = out.get(sym, 0.0) + sleeve * float(r["position"])
    return out


def plan(tgt: dict, held: dict, prices: dict) -> list[dict]:
    """Orders that move holdings (qty) to dollar targets. Whole shares for stocks (OPG orders
    cannot be fractional); fractional quantity for crypto."""
    orders = []
    for sym in sorted(set(tgt) | set(held)):
        px = prices.get(sym)
        if not px:
            continue
        crypto = "/" in sym
        want_qty = tgt.get(sym, 0.0) / px
        want_qty = round(want_qty, 6) if crypto else math.floor(want_qty)
        diff = want_qty - held.get(sym, 0.0)
        if abs(diff) * px < MIN_TRADE_USD:
            continue
        orders.append({"symbol": sym, "side": "buy" if diff > 0 else "sell", "qty": round(abs(diff), 6),
                       "tif": "gtc" if crypto else "opg", "target_usd": round(tgt.get(sym, 0.0), 2)})
    return orders


def snapshot(client, decisions=None):
    """Save the paper account's state for the Strategy Lab page (no trading)."""
    import json
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    decisions = decisions if decisions is not None else latest_decisions()
    behind = {}
    for k, r in decisions.items():
        behind.setdefault(SYMBOL[r["market"]].replace("/", ""), []).append(
            {"key": k, "name": r["name"], "position": float(r["position"]), "decided": r["decision_date"]})
    a = client.get_account()
    pos = [{"symbol": p.symbol, "qty": float(p.qty), "avg_price": float(p.avg_entry_price),
            "price": float(p.current_price), "value": float(p.market_value), "pl": float(p.unrealized_pl),
            "pl_pct": float(p.unrealized_plpc)} for p in client.get_all_positions()]
    orders = [{"date": o.submitted_at.isoformat(timespec="minutes"), "symbol": o.symbol, "side": o.side.value,
               "qty": float(o.qty or 0), "status": o.status.value,
               "price": float(o.filled_avg_price) if o.filled_avg_price else None}
              for o in client.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, limit=15))]
    snap = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "start_equity": START_EQUITY,
            "equity": float(a.equity), "cash": float(a.cash), "last_equity": float(a.last_equity),
            "positions": pos, "orders": orders, "strategies_by_symbol": behind}
    SNAPSHOT.write_text(json.dumps(snap, indent=1))
    print(f"saved account snapshot: equity ${snap['equity']:,.0f}, {len(pos)} positions")
    return snap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--snapshot", action="store_true", help="only save the account state for the lab page")
    a = ap.parse_args()
    decisions = latest_decisions()
    if not decisions:
        print("no paper decisions logged yet; run `python src/paper.py record` first")
        return
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if a.dry_run or not (key and secret):
        if not a.dry_run:
            print("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set: showing a dry run on a $100,000 account")
        import data
        prices = {SYMBOL[m]: float(data.load(m).Close.iloc[-1]) for m in {r["market"] for r in decisions.values()}}
        for o in plan(targets(decisions, 100_000.0), {}, prices):
            print(f"  would {o['side']} {o['qty']} {o['symbol']} ({o['tif']}), target ${o['target_usd']:,.0f}")
        return

    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import CryptoLatestTradeRequest, StockLatestTradeRequest

    client = TradingClient(key, secret, paper=True)
    if a.snapshot:
        snapshot(client, decisions)
        return
    # Alpaca only accepts market-on-open (OPG) orders outside 09:28-19:00 New York time. Inside that
    # window the decision's open has already passed, so send a normal DAY market order instead and
    # record it as a late fill compared with the backtest's next-open assumption.
    from zoneinfo import ZoneInfo
    ny = datetime.now(ZoneInfo("America/New_York"))
    late = ny.weekday() < 5 and (9, 28) <= (ny.hour, ny.minute) < (19, 0)
    acct = client.get_account()  # paper=True: this client can only reach paper-api.alpaca.markets
    equity = float(acct.equity)
    held = {("BTC/USD" if p.symbol in ("BTCUSD", "BTC/USD") else p.symbol): float(p.qty)
            for p in client.get_all_positions()}
    tgt = targets(decisions, equity)
    stocks = [s for s in set(tgt) | set(held) if "/" not in s]
    cryptos = [s for s in set(tgt) | set(held) if "/" in s]
    prices = {}
    if stocks:
        tr = StockHistoricalDataClient(key, secret).get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=stocks))
        prices |= {s: float(t.price) for s, t in tr.items()}
    if cryptos:
        tr = CryptoHistoricalDataClient(key, secret).get_crypto_latest_trade(CryptoLatestTradeRequest(symbol_or_symbols=cryptos))
        prices |= {s: float(t.price) for s, t in tr.items()}
    # cancel our own unfilled orders from a previous run, then place the new ones
    for o in client.get_orders():
        if (o.client_order_id or "").startswith("lab-"):
            client.cancel_order_by_id(o.id)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    new = not ORDERS.exists()
    with ORDERS.open("a", newline="") as f:
        w = csv.DictWriter(f, ["sent_at_utc", "symbol", "side", "qty", "tif", "target_usd", "order_id", "status"])
        if new:
            w.writeheader()
        for o in plan(tgt, held, prices):
            req = MarketOrderRequest(symbol=o["symbol"], qty=o["qty"],
                                     side=OrderSide.BUY if o["side"] == "buy" else OrderSide.SELL,
                                     time_in_force=TimeInForce.GTC if o["tif"] == "gtc" else
                                     (TimeInForce.DAY if late else TimeInForce.OPG),
                                     client_order_id=f"lab-{stamp}-{o['symbol'].replace('/', '')}")
            if o["tif"] == "opg" and late:
                o["tif"] = "day (late: after the open)"
            res = client.submit_order(req)
            w.writerow({"sent_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **o,
                        "order_id": str(res.id), "status": str(res.status)})
            print(f"  sent {o['side']} {o['qty']} {o['symbol']} ({o['tif']}): {res.status}")
    print(f"account equity ${equity:,.0f}; targets " + ", ".join(f"{s} ${v:,.0f}" for s, v in sorted(tgt.items())))
    snapshot(client, decisions)


if __name__ == "__main__":
    main()
