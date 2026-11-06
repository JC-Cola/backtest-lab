"""Mirror the lab's daily paper-trading decisions into an Alpaca PAPER account (visible in TradingView).

    python src/alpaca_paper.py --dry-run   # show the orders it would place (no keys needed)
    python src/alpaca_paper.py             # place them (needs ALPACA_API_KEY / ALPACA_SECRET_KEY)

How it sizes: each strategy in the Strategy Lab book gets an equal sleeve of DAILY_SHARE (100%) of equity; a
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
START_EQUITY = 25_000.0  # new $25k paper account from 2026-11-04 (the first account started at $100k)
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


DAILY_SHARE = 1.0  # this account is the daily strategies' alone; intraday trades its own account (live_noise.py)


def targets(decisions: dict, equity: float) -> dict:
    """Dollar target per Alpaca symbol."""
    sleeve = DAILY_SHARE * equity / max(1, len(decisions))
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


def calendar_from_history(timestamps, equity, profit_loss, profit_loss_pct):
    """Per-trading-day P&L from Alpaca portfolio history (1D bars). pct is in percent. Days before the
    account started (equity 0/None) are skipped."""
    from zoneinfo import ZoneInfo
    ny, out = ZoneInfo("America/New_York"), []
    for i, ts in enumerate(timestamps or []):
        eq = equity[i] if i < len(equity or []) else None
        if not eq or eq <= 0:
            continue
        pl = profit_loss[i] if i < len(profit_loss or []) else None
        pc = profit_loss_pct[i] if i < len(profit_loss_pct or []) else None
        if pl is None:
            continue
        if not out and not pl:  # the funding day before any trade: nothing to show
            continue
        # 1D bars are stamped at 20:00 New York on the trading day itself, so the New York date is the trading date
        d = datetime.fromtimestamp(ts, ny).date()
        out.append({"date": d.isoformat(), "pl": round(float(pl), 2),
                    "pct": round(float(pc) * 100, 3) if pc is not None else 0.0, "equity": round(float(eq), 2)})
    return out


def account_calendar(client, account):
    """History from Alpaca plus today's live day when the market is open. Never raises."""
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        h = client.get_portfolio_history(GetPortfolioHistoryRequest(period="3M", timeframe="1D"))
        cal = calendar_from_history(h.timestamp, h.equity, h.profit_loss, h.profit_loss_pct)
        try:
            from alpaca.trading.requests import GetCalendarRequest
            clock = client.get_clock()
            day = clock.timestamp.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).date()
            today = day.isoformat()
            # today is a trading day (open now, or already closed: Alpaca adds the day's history bar only later)
            if clock.is_open or client.get_calendar(GetCalendarRequest(start=day, end=day)):
                eq, last = float(account.equity), float(account.last_equity)
                if last > 0:
                    row = {"date": today, "pl": round(eq - last, 2), "pct": round((eq - last) / last * 100, 3), "equity": round(eq, 2)}
                    cal = [c for c in cal if c["date"] != today] + [row]
        except Exception:
            pass
        return cal
    except Exception as e:
        print(f"calendar unavailable: {e}", flush=True)
        return None


def history_points(timestamps, equity):
    """[[unix_ts, equity], ...] from a portfolio-history series, dropping points with equity 0/None (before funding)."""
    out = []
    for i, ts in enumerate(timestamps or []):
        eq = equity[i] if i < len(equity or []) else None
        if ts is None or not eq or eq <= 0:
            continue
        out.append([int(ts), round(float(eq), 2)])
    return out


HISTORY_RANGES = {"1D": ("1D", "5Min", True), "1W": ("1W", "15Min", False), "1M": ("30D", "1H", False), "ALL": ("1A", "1D", False)}


def account_history(client):
    """Balance history per range for the Strategy Lab chart. Never raises (None on error)."""
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        out = {}
        for key, (period, tf, ext) in HISTORY_RANGES.items():
            kw = {"extended_hours": True} if ext else {}
            h = client.get_portfolio_history(GetPortfolioHistoryRequest(period=period, timeframe=tf, **kw))
            out[key] = history_points(h.timestamp, h.equity)
        return out
    except Exception as e:
        print(f"history unavailable: {e}", flush=True)
        return None


def _fmt_num(x):
    """Trim trailing zeros: 1.9 -> '1.9', 3.0 -> '3'."""
    return f"{x:.8f}".rstrip("0").rstrip(".") or "0"


def ledger_from_fills(fills, start_balance=START_EQUITY):
    """TradingView-style balance history from account fills (dicts with time, symbol, side, qty, price).
    Walks oldest to newest per symbol, tracking signed qty and average price (long and short, partial
    closes, flips through zero = close then open). Only fills that reduce a position make a row.
    Returns {"rows": [...newest first...], "realized": total}."""
    from zoneinfo import ZoneInfo
    ny, pos, bal, rows = ZoneInfo("America/New_York"), {}, float(start_balance), []
    for f in sorted(fills, key=lambda f: (f["time"], str(f.get("id", "")))):
        sym, qty, px = f["symbol"], float(f["qty"]), float(f["price"])
        signed = qty if f["side"] == "buy" else -qty
        q, avg = pos.get(sym, (0.0, 0.0))
        if q == 0 or (q > 0) == (signed > 0):  # opens or adds
            nq = q + signed
            pos[sym] = (nq, (abs(q) * avg + abs(signed) * px) / abs(nq))
            continue
        closed = min(abs(signed), abs(q))
        pnl = (px - avg) * closed * (1 if q > 0 else -1)
        before, bal = bal, bal + pnl
        t = f["time"]
        t = datetime.fromisoformat(t.replace("Z", "+00:00")) if isinstance(t, str) else t
        rows.append({"time": t.astimezone(ny).strftime("%Y-%m-%d %H:%M:%S"), "balance_before": round(before, 2),
                     "balance_after": round(bal, 2), "realized_pnl": round(pnl, 2),
                     "action": f"Close {'long' if q > 0 else 'short'} position for symbol {sym} at price {_fmt_num(px)} "
                               f"for {_fmt_num(closed)} units. Position AVG Price was {_fmt_num(avg)}"})
        rem = q + signed
        if abs(abs(signed) - closed) > 1e-12:  # flipped through zero: the rest opens the other way at this price
            pos[sym] = (rem, px)
        else:
            pos[sym] = (rem, avg if abs(rem) > 1e-12 else 0.0)
            if abs(rem) <= 1e-12:
                pos[sym] = (0.0, 0.0)
    rows.reverse()
    return {"rows": rows, "realized": round(bal - float(start_balance), 2)}


def fetch_fills(client):
    """All FILL account activities, oldest first (REST, paged by id). Raises on error."""
    out, token = [], None
    while True:
        params = {"direction": "asc", "page_size": 100}
        if token:
            params["page_token"] = token
        page = client.get("/account/activities/FILL", params)
        if not page:
            break
        out += [{"id": a["id"], "time": a["transaction_time"], "symbol": a["symbol"], "side": a["side"],
                 "qty": a["qty"], "price": a["price"], "order_id": a.get("order_id")} for a in page]
        if len(page) < 100:
            break
        token = page[-1]["id"]
    return out


def balance_history(client, start_balance=START_EQUITY):
    """Closed-trade ledger for the Balance history tab. Never raises (None on error)."""
    try:
        return ledger_from_fills(fetch_fills(client), start_balance)
    except Exception as e:
        print(f"balance history unavailable: {e}", flush=True)
        return None


def snapshot(client, decisions=None, path=SNAPSHOT, behind=None):
    """Save a paper account's state for the Strategy Lab page (no trading). The intraday account passes its
    own path and `behind` (symbol -> strategies holding it)."""
    import json
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    decisions = decisions if decisions is not None else ({} if behind is not None else latest_decisions())
    behind = {} if behind is None else behind
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
    cal = account_calendar(client, a)
    if cal is not None:
        snap["calendar"] = cal
    hist = account_history(client)
    if hist is not None:
        now = int(datetime.now(timezone.utc).timestamp())
        if hist.get("ALL") and hist["ALL"][-1][0] < now - 3600:  # daily bars lag: end the All line at the live balance
            hist["ALL"].append([now, round(float(a.equity), 2)])
        snap["history"] = hist
    bh = balance_history(client)
    if bh is not None:
        def _f(v):
            try:
                return round(float(v), 2)
            except (TypeError, ValueError):
                return None
        # "Available funds": non_marginable_buying_power = what can still be bought without leverage,
        # the closest match to TradingView's cash-account style number; falls back to cash.
        bh.update({"unrealized": round(sum(p["pl"] for p in pos), 2), "equity": round(float(a.equity), 2),
                   "available": _f(getattr(a, "non_marginable_buying_power", None)) if getattr(a, "non_marginable_buying_power", None) is not None else round(float(a.cash), 2),
                   "margin": _f(getattr(a, "initial_margin", None)), "start_balance": START_EQUITY})
        snap["balance_history"] = bh
    path.write_text(json.dumps(snap, indent=1))
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
        for o in plan(targets(decisions, START_EQUITY), {}, prices):
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
