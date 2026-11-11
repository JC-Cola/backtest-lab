"""System B execution: Alpaca PAPER orders on the dedicated System B account, or the shadow ledger as a fallback.

Keys: ALPACA_DTS_API_KEY / ALPACA_DTS_SECRET_KEY (GitHub secrets of the same names; ~/.config/backtest-lab/alpaca.env
locally). TradingClient(paper=True) is hard-coded and the client is refused unless it points at paper-api.alpaca.markets.
No keys -> Shadow (simulated fills in dts_ledger), so nothing breaks before the account exists.

Orders: entries are MARKET orders with a bracket (take-profit limit + stop-loss stop) from the exit plan. Each check
reconciles the account: a bracket leg that filled closes the ledger trade at the real fill; at flat-by (15:55 NY) the
open legs are cancelled and a plain MARKET sell flattens (never market-on-close: Alpaca paper let those expire).
Equity, cash, orders, calendar, history and balance_history come from the account (alpaca_paper.snapshot).
"""
from __future__ import annotations

import datetime as dt
import os
import time

import dts_ledger as L

PAPER_HOST = "paper-api.alpaca.markets"


class NoKeys(Exception):
    pass


class Shadow:
    live = False
    name = "shadow ledger"

    def equity(self, state):
        return L.equity_of(state)

    def sync(self, state):
        pass

    def open(self, state, sym, qty, p0, T_et, T_utc, plan, extra):
        return L.open_position(state, sym, qty, p0, T_et, T_utc, plan, extra)

    def finalize(self, state, now_utc):
        L.refresh_snapshot(state, now_utc)


class AlpacaPaper:
    live = True
    name = "Alpaca paper (System B account)"

    def __init__(self, client=None, wait=20.0, sleep=time.sleep):
        if client is None:
            key, secret = os.environ.get("ALPACA_DTS_API_KEY"), os.environ.get("ALPACA_DTS_SECRET_KEY")
            if not (key and secret):
                raise NoKeys("ALPACA_DTS_API_KEY / ALPACA_DTS_SECRET_KEY not set")
            from alpaca.trading.client import TradingClient
            client = TradingClient(key, secret, paper=True)          # paper only: never paper=False
        base = str(getattr(client, "_base_url", ""))
        if PAPER_HOST not in base:
            raise RuntimeError("refusing to run: the trading client is not the Alpaca PAPER endpoint (%r)" % base)
        self.client, self.wait, self.sleep = client, wait, sleep

    # ---- account
    def account(self):
        return self.client.get_account()

    def equity(self, state):
        return float(self.account().equity)

    def sync(self, state):
        state["cash"] = float(self.account().cash)

    def _held(self, sym):
        try:
            return self.client.get_open_position(sym)
        except Exception as e:  # noqa: BLE001 - alpaca raises APIError(404) when flat
            if "position does not exist" in str(e).lower() or "404" in str(e):
                return None
            raise

    def _filled(self, order_id):
        """Poll the order up to `wait` seconds. Returns the order (filled or not)."""
        end, o = time.time() + self.wait, None
        while True:
            o = self.client.get_order_by_id(order_id)
            if str(getattr(o.status, "value", o.status)) in ("filled", "canceled", "expired", "rejected") or time.time() >= end:
                return o
            self.sleep(1.0)

    # ---- entries
    def open(self, state, sym, qty, p0, T_et, T_utc, plan, extra):
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest
        if self._held(sym) is not None:
            raise RuntimeError("%s is already held in the account" % sym)
        o = self.client.submit_order(MarketOrderRequest(
            symbol=sym, qty=int(qty), side=OrderSide.BUY, time_in_force=TimeInForce.DAY, order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(plan["target"], 2)),
            stop_loss=StopLossRequest(stop_price=round(plan["stop"], 2))))
        o = self._filled(o.id)
        status = str(getattr(o.status, "value", o.status))
        if status in ("canceled", "expired", "rejected"):
            raise RuntimeError("entry order %s: %s" % (sym, status))
        filled = status == "filled" and o.filled_avg_price
        pos = L.open_position(state, sym, int(qty), p0, T_et, T_utc, plan, extra,
                              real_fill=float(o.filled_avg_price) if filled else p0)
        pos["order_id"] = str(o.id)
        if not filled:
            pos["pending"] = True        # reconciled on the next check
        return pos

    # ---- reconcile / flatten
    def manage(self, state, now_utc, now_et, close_et, flat_for):
        """Reconcile every ledger position with the account. flat_for(pos) -> flat-by datetime (ET)."""
        from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
        from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
        for pos in list(state["positions"]):
            o = self.client.get_order_by_id(pos["order_id"])
            status = str(getattr(o.status, "value", o.status))
            if pos.get("pending"):
                if status == "filled" and o.filled_avg_price:
                    pos.update(avg_price=float(o.filled_avg_price), pending=False)
                elif status in ("canceled", "expired", "rejected"):
                    state["positions"].remove(pos)
                continue
            held = self._held(pos["symbol"])
            if held is None:                                   # a bracket leg filled (or it was closed by hand)
                leg = next((l for l in (o.legs or []) if l.filled_qty and float(l.filled_qty) > 0), None)
                if leg is not None:
                    ref = float(leg.filled_avg_price)
                    kind = str(getattr(leg.order_type, "value", leg.order_type))
                    reason = "target" if kind == "limit" else "stop"
                    at = leg.filled_at
                else:
                    ref, reason, at = pos.get("price", pos["avg_price"]), "closed outside the pipeline", None
                self._record_close(state, pos, ref, reason, at, now_utc)
                continue
            pos["price"] = float(held.current_price)
            due = pos["date"] < now_et.date().isoformat() or now_et >= flat_for(pos)
            if due and now_et < close_et:
                for od in self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[pos["symbol"]])):
                    try:
                        self.client.cancel_order_by_id(od.id)
                    except Exception:  # noqa: BLE001 - already filled/cancelled
                        pass
                held = self._held(pos["symbol"])
                if held is None:
                    continue                                   # a leg filled while cancelling: next check records it
                s = self.client.submit_order(MarketOrderRequest(symbol=pos["symbol"], qty=int(float(held.qty)),
                                                                side=OrderSide.SELL, time_in_force=TimeInForce.DAY))
                s = self._filled(s.id)
                if s.filled_avg_price:
                    self._record_close(state, pos, float(s.filled_avg_price), "flat by %s" % pos["flat_by"], s.filled_at, now_utc)
            elif due:
                pos["note"] = "flat-by missed: the market is closed; flattened at the next run"

    def _record_close(self, state, pos, ref, reason, at, now_utc):
        from dts_core import scan_m
        utc = at.astimezone(dt.timezone.utc).replace(tzinfo=None) if at is not None else now_utc
        et = scan_m().to_eastern(utc)
        L.close_position(state, pos, ref, et, utc, reason, real_fill=True)

    # ---- snapshot from the account
    def finalize(self, state, now_utc):
        import tempfile
        from pathlib import Path
        import json
        import alpaca_paper
        with tempfile.TemporaryDirectory() as d:
            snap = alpaca_paper.snapshot(self.client, decisions={}, path=Path(d) / "snap.json", behind={})
        for k in ("equity", "cash", "last_equity", "orders", "calendar", "history", "balance_history", "start_equity"):
            if k in snap:
                state[k] = snap[k]
        live = {p["symbol"]: p for p in snap["positions"]}
        for p in state["positions"]:
            lp = live.get(p["symbol"])
            if lp:
                p.update({k: lp[k] for k in ("price", "value", "pl", "pl_pct")})
        state["generated"] = L._utc_iso(now_utc)
        state["source"] = "alpaca-paper"
        json.dumps(state)                                      # fail loudly here, not while saving


def select_broker(force_shadow=False):
    """AlpacaPaper when the System B keys exist, else the shadow ledger."""
    if force_shadow:
        return Shadow()
    try:
        return AlpacaPaper()
    except NoKeys:
        return Shadow()
