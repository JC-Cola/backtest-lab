"""System C on a dedicated Alpaca PAPER account (keys ALPACA_TA_API_KEY / ALPACA_TA_SECRET_KEY). Paper only: the client is
built with paper=True and the account must report a paper account number ("PA..."), otherwise nothing is sent.

After the close the night's decisions become market orders (time in force DAY) that queue for the next open. Sizing is the
ledger's: target fraction x account equity / last close, whole shares (shorts cannot be fractional). A flip (long <-> short)
is two legs: tonight closes the old side, the next night's re-decision opens the new one (a queued short while a long is
open is rejected by Alpaca). Not shortable -> no short is opened. Without keys the shadow ledger is used instead.
"""
from __future__ import annotations

import math
import os
import re
import tempfile
import time
from pathlib import Path

PREFIX = "ta-"
MIN_TRADE_USD = 50.0
KEY, SECRET = "ALPACA_TA_API_KEY", "ALPACA_TA_SECRET_KEY"


def client_from_env(env=None, factory=None):
    """A paper TradingClient, or None when the keys are not set. Raises RuntimeError if the account is not a paper one."""
    env = os.environ if env is None else env
    k, s = env.get(KEY), env.get(SECRET)
    if not (k and s):
        return None
    if factory is None:
        from alpaca.trading.client import TradingClient
        factory = lambda key, sec: TradingClient(key, sec, paper=True)   # never paper=False
    client = factory(k, s)
    acct = client.get_account()
    if not str(getattr(acct, "account_number", "")).startswith("PA"):
        raise RuntimeError("System C account is not an Alpaca PAPER account; refusing to trade")
    return client


def sign(x):
    return (x > 0) - (x < 0)


def plan_orders(decisions, history, held, prices, equity, shortable=lambda s: True, sizes=None):
    """decisions: tonight's decisions [{symbol, target, ...}] (target None = Hold: no order). history: all earlier
    decisions (to see whether the tier changed). held: {symbol: signed qty}. prices: {symbol: last close}.
    sizes: {symbol: initial_size_x} from the validated trading plan (Overweight 0.5x = half of the 10% slot); default 1.
    Returns [{symbol, side, qty, note}]."""
    out, sizes = [], sizes or {}
    for d in sorted(decisions, key=lambda d: d["symbol"]):
        sym, t = d["symbol"], d["target"]
        px = prices.get(sym)
        if t is None or not px:
            continue
        cur = held.get(sym, 0.0)
        prev = [h["target"] for h in history if h["symbol"] == sym and h["target"] is not None and h["date"] < d["date"]]
        if cur and t and prev and abs(prev[-1] - t) < 1e-9 and sign(cur) == sign(t):
            continue   # same tier and already positioned: hold
        desired = float(math.trunc(t * sizes.get(sym, 1.0) * equity / px))
        if t == 0 or (cur and sign(cur) != sign(desired)):   # exit, or the first leg of a flip: go flat
            if cur:
                out.append({"symbol": sym, "side": "sell" if cur > 0 else "buy", "qty": abs(cur),
                            "note": "exit" if t == 0 else "flip: close first, open next night"})
            continue
        if desired < 0 and not shortable(sym):
            out.append({"symbol": sym, "side": None, "qty": 0, "note": "not shortable: no short opened"})
            continue
        diff = desired - cur
        if abs(diff) * px < MIN_TRADE_USD:
            continue
        out.append({"symbol": sym, "side": "buy" if diff > 0 else "sell", "qty": abs(diff),
                    "note": "open" if not cur else "resize"})
    return [o for o in out if o["side"]] + [o for o in out if not o["side"]]


def held_qty(client):
    return {p.symbol: float(p.qty) * (-1 if str(getattr(p, "side", "")).lower().endswith("short") and float(p.qty) > 0 else 1)
            for p in client.get_all_positions()}


def place(client, orders, stamp):
    """Cancel our own unfilled orders from earlier runs, then submit tonight's. Returns rows with the broker's status."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
    for o in client.get_orders():
        if (o.client_order_id or "").startswith(PREFIX):
            try:
                client.cancel_order_by_id(o.id)
            except Exception:
                pass
    rows = []
    for o in orders:
        row = dict(o)
        if not o["side"]:
            row["status"] = "skipped"
        else:
            try:
                res = client.submit_order(MarketOrderRequest(
                    symbol=o["symbol"], qty=o["qty"], side=OrderSide.BUY if o["side"] == "buy" else OrderSide.SELL,
                    time_in_force=TimeInForce.DAY, client_order_id=f"{PREFIX}{stamp}-{o['symbol']}"))
                row["status"] = str(getattr(res.status, "value", res.status))
            except Exception as ex:
                row["status"] = "error: " + re.sub(r"\s+", " ", str(ex))[:80]
        rows.append(row)
    return rows


def shortable_fn(client):
    def f(sym):
        try:
            return bool(client.get_asset(sym).shortable)
        except Exception:
            return False
    return f


def snapshot(client, snapshot_fn=None):
    """The account's real state in the other ledgers' shape (equity, cash, positions, orders, calendar, history,
    balance_history from the fills), via alpaca_paper.snapshot."""
    import json
    if snapshot_fn is None:
        from alpaca_paper import snapshot as snapshot_fn
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "snap.json"
        snapshot_fn(client, decisions={}, path=p, behind={})
        return json.loads(p.read_text())


def trades_from_rows(rows, decisions):
    """Closed round trips (one per balance_history row) with the agents' rationale for that symbol at the time."""
    out = []
    for r in rows:
        m = re.match(r"Close (long|short) position for symbol (\S+) at price (\S+) for (\S+) units\. Position AVG Price was (\S+)",
                     r.get("action", ""))
        if not m:
            continue
        side, sym, px, qty, avg = m.groups()
        day = r["time"][:10]
        earlier = [d for d in decisions if d["symbol"] == sym and d["date"] < day and d.get("source") == "ta"]
        why = max(earlier, key=lambda d: d["date"]) if earlier else {}
        out.append({"symbol": sym, "side": side, "qty": float(qty), "entry_px": float(avg), "exit_px": float(px),
                    "exit_date": day, "pnl": r["realized_pnl"], "rating": why.get("rating"), "rationale": why.get("rationale", "")})
    return out


# ---------- protective orders (stops / take-profits / plan exits) ----------
# Plan orders use client ids "tap-..." (not "ta-"), so tonight's place() never cancels them. Alpaca reserves the shares of a
# live sell order, so a second sell (a market exit) is rejected until the stop is cancelled: always cancel and WAIT first.
LIVE = {"new", "accepted", "held", "partially_filled", "pending_new", "pending_replace", "pending_cancel", "accepted_for_bidding"}
PROTECTIVE = {"stop", "stop_limit", "limit", "trailing_stop"}


def _v(x):
    return str(getattr(x, "value", x)).lower()


def open_orders(client, symbol=None):
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol] if symbol else None, limit=500)
    return list(client.get_orders(filter=req))


def exit_side(qty):
    return "sell" if qty > 0 else "buy"


def protective_orders(orders, symbol, qty):
    """Open exit-side stop/limit orders for `symbol` (a long exits by selling, a short by buying)."""
    return [o for o in orders if o.symbol == symbol and _v(o.side) == exit_side(qty) and _v(o.order_type or getattr(o, "type", "")) in PROTECTIVE]


def cancel_and_wait(client, orders, sleep=time.sleep, tries=20):
    """Cancel every order and poll until none is live any more. Returns True when all are gone."""
    ids = []
    for o in orders:
        try:
            client.cancel_order_by_id(o.id)
        except Exception:
            pass   # already filled/cancelled (e.g. the other OCO leg)
        ids.append(o.id)
    for _ in range(tries):
        live = []
        for i in ids:
            try:
                if _v(client.get_order_by_id(i).status) in LIVE:
                    live.append(i)
            except Exception:
                pass
        if not live:
            return True
        ids = live
        sleep(0.5)
    return False


def place_protection(client, symbol, qty, stop, tp, stamp):
    """GTC stop-market for the whole shares of the position (stop-market orders cannot be fractional); with a take-profit too,
    one OCO pair so the other leg is cancelled automatically. Returns [(kind, order id, status)]."""
    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, StopOrderRequest, TakeProfitRequest
    n = int(abs(qty))
    side = OrderSide.SELL if qty > 0 else OrderSide.BUY
    if n < 1 or not (stop or tp):
        return []
    if stop and tp:
        req = LimitOrderRequest(symbol=symbol, qty=n, side=side, time_in_force=TimeInForce.GTC, limit_price=round(tp, 2),
                                order_class=OrderClass.OCO, take_profit=TakeProfitRequest(limit_price=round(tp, 2)),
                                stop_loss=StopLossRequest(stop_price=round(stop, 2)), client_order_id=f"tap-oco-{symbol}-{stamp}")
        kind = "oco"
    elif stop:
        req = StopOrderRequest(symbol=symbol, qty=n, side=side, time_in_force=TimeInForce.GTC, stop_price=round(stop, 2),
                               client_order_id=f"tap-stop-{symbol}-{stamp}")
        kind = "stop"
    else:
        req = LimitOrderRequest(symbol=symbol, qty=n, side=side, time_in_force=TimeInForce.GTC, limit_price=round(tp, 2),
                                client_order_id=f"tap-tp-{symbol}-{stamp}")
        kind = "tp"
    res = client.submit_order(req)
    return [(kind, str(res.id), _v(res.status))]
