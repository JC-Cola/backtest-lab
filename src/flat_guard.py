"""Alert (Telegram) when the INTRADAY paper account is not flat after the close. Alert only: always exits 0.

    python src/flat_guard.py        # keys ALPACA_INTRADAY_API_KEY / ALPACA_INTRADAY_SECRET_KEY, TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
"""
from __future__ import annotations

import os


def leftovers(trading):
    """(positions {symbol: qty}, open orders [text]) of the account."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    pos = {p.symbol: int(float(p.qty)) for p in trading.get_all_positions() if float(p.qty)}
    orders = [f"{str(o.side).split('.')[-1].lower()} {o.qty} {o.symbol}"
              for o in trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))]
    return pos, orders


def message(pos, orders):
    """None when flat, else the alert text."""
    if not pos and not orders:
        return None
    parts = []
    if pos:
        parts.append("Intraday account is NOT flat after the close: "
                     + ", ".join(f"{s} {q}" for s, q in sorted(pos.items())) + ". They will be closed at the 9:31 check.")
    if orders:
        parts.append("Open orders: " + ", ".join(sorted(orders)) + ".")
    return "⚠️ " + " ".join(parts)


def main(trading=None, send=None):
    try:
        if send is None:
            import telegram
            send = telegram.send
        if trading is None:
            from alpaca.trading.client import TradingClient
            k, s = os.environ.get("ALPACA_INTRADAY_API_KEY"), os.environ.get("ALPACA_INTRADAY_SECRET_KEY")
            if not (k and s):
                print("intraday keys not set; skipping the flat check")
                return None
            trading = TradingClient(k, s, paper=True)
        text = message(*leftovers(trading))
        print(text or "intraday account is flat")
        if text:
            send(text)
        return text
    except Exception as e:  # an alert script must never fail the nightly job
        print(f"flat check failed: {type(e).__name__}")
        return None


if __name__ == "__main__":
    main()
