"""Telegram message for every FILL on both Alpaca paper accounts (open / add / reduce / close / flip).

    python fill_alerts.py [--dry-run] [--init]
      --dry-run  print the messages; send nothing and save nothing
      --init     only mark the current fills as seen (no messages)

Keys: ALPACA_API_KEY / ALPACA_SECRET_KEY (long-term), ALPACA_INTRADAY_API_KEY / ALPACA_INTRADAY_SECRET_KEY (intraday),
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID. State: agent_memory/fill_alerts.json {account: {"id", "time"}} = last fill seen.
An account with no entry yet is initialised silently (old fills are never sent). A message that fails to send is
not marked seen, so the next run retries it. The job never fails: errors are printed without secrets, exit code 0.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alpaca_paper import START_EQUITY, fetch_fills, ledger_from_fills

STATE = Path(__file__).resolve().parent.parent / "agent_memory" / "fill_alerts.json"
MAX_MESSAGES = 20
NY = ZoneInfo("America/New_York")
ACCOUNTS = [("longterm", "Long-term", "ALPACA_API_KEY", "ALPACA_SECRET_KEY"),
            ("intraday", "Intraday", "ALPACA_INTRADAY_API_KEY", "ALPACA_INTRADAY_SECRET_KEY")]
EPS = 1e-9


def _key(f):
    return (f["time"], str(f.get("id", "")))


def _dt(t):
    return datetime.fromisoformat(t.replace("Z", "+00:00")) if isinstance(t, str) else t


def symbol_name(s):
    """BTCUSD -> BTC/USD."""
    return f"{s[:-3]}/USD" if "/" not in s and len(s) > 3 and s.endswith("USD") else s


def num(x):
    """Quantity: whole numbers plain, fractions up to 6 decimals without trailing zeros."""
    x = float(x)
    return f"{x:,.0f}" if abs(x - round(x)) < 1e-9 else f"{x:,.6f}".rstrip("0").rstrip(".")


def usd(x, sign=False):
    x = float(x)
    s = f"{abs(x):,.2f}"
    return (("+" if x >= 0 else "-") if sign else ("-" if x < 0 else "")) + "$" + s


def clock(t):
    d = _dt(t).astimezone(NY)
    return f"{d:%H:%M:%S} New York" if d.date() == datetime.now(NY).date() else f"{d:%m-%d %H:%M:%S} New York"


def group_fills(fills):
    """Merge consecutive partial fills of one order (same order_id, symbol, side) into one group (list)."""
    groups = []
    for f in fills:
        g = groups[-1] if groups else None
        if (g and f.get("order_id") and g[-1].get("order_id") == f["order_id"]
                and g[-1]["symbol"] == f["symbol"] and g[-1]["side"] == f["side"]):
            g.append(f)
        else:
            groups.append([f])
    return groups


def build_messages(label, all_fills, new_ids, start_balance=START_EQUITY):
    """[(last_fill, text)] oldest first for the fills whose id is in `new_ids`; all_fills = the whole history."""
    fills = sorted(all_fills, key=_key)
    pos, out, i = {}, [], 0
    for g in group_fills(fills):
        start, i = i, i + len(g)
        q, avg = pos.get(g[0]["symbol"], (0.0, 0.0))
        sym, side = g[0]["symbol"], g[0]["side"]
        qty = sum(float(f["qty"]) for f in g)
        px = sum(float(f["qty"]) * float(f["price"]) for f in g) / qty
        signed = qty if side == "buy" else -qty
        # position bookkeeping (same rules as ledger_from_fills)
        if abs(q) < EPS or (q > 0) == (signed > 0):
            nq = q + signed
            pos[sym] = (nq, (abs(q) * avg + abs(signed) * px) / abs(nq))
        else:
            nq = q + signed
            flip = abs(signed) > abs(q) + EPS
            pos[sym] = (nq, px) if flip else ((nq, avg) if abs(nq) > EPS else (0.0, 0.0))
        if not any(str(f["id"]) in new_ids for f in g):
            continue
        name, direction = symbol_name(sym), ("LONG" if signed > 0 else "SHORT")
        when = clock(g[-1]["time"] if abs(q) >= EPS and (q > 0) != (signed > 0) else g[0]["time"])
        head, line2 = f"{label} · ", f"{num(qty)} @ {usd(px)}"
        if abs(q) < EPS or (q > 0) == (signed > 0):
            kind = "OPEN" if abs(q) < EPS else "ADD"
            icon = "🟢" if signed > 0 else "🔴"
            text = f"{icon} {head}{kind} {direction} {name}\n{line2} = {usd(qty * px)}"
            if kind == "ADD":
                text += f"\nPosition now {num(abs(nq))} {'long' if nq > 0 else 'short'} (avg {usd(pos[sym][1])})"
        else:
            before = ledger_from_fills([f for f in fills[:start]], start_balance)["rows"]
            after = ledger_from_fills([f for f in fills[:i]], start_balance)["rows"]
            rows = after[:len(after) - len(before)]
            pnl = sum(r["realized_pnl"] for r in rows)
            b0, b1 = rows[-1]["balance_before"], rows[0]["balance_after"]
            held = "SHORT" if q < 0 else "LONG"
            flip = abs(signed) > abs(q) + EPS
            kind = "CLOSE" if abs(nq) < EPS else ("FLIP" if flip else "REDUCE")
            icon = "✅" if pnl >= 0 else "❌"
            if kind == "FLIP":
                title = f"{kind} {held}→{direction} {name}"
            else:
                title = f"{kind} {held} {name}"
            closed = min(abs(signed), abs(q))
            text = (f"{icon} {head}{title}  {usd(pnl, True)}\n{num(qty)} @ {usd(px)} (avg was {usd(avg)})"
                    if kind != "FLIP" else
                    f"{icon} {head}{title}  {usd(pnl, True)}\n{num(closed)} closed, {num(qty - closed)} opened "
                    f"@ {usd(px)} (avg was {usd(avg)})")
            text += f"\nBalance {usd(b0)} → {usd(b1)}"
        out.append((g[-1], f"{text}\n{when}"))
    return out


def load_state(path=STATE):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def save_state(state, path=STATE):
    Path(path).write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def new_fills(fills, mark):
    """Fills after the last-seen mark ({"id", "time"}) in (time, id) order."""
    fills = sorted(fills, key=_key)
    if not mark or not mark.get("id"):
        return fills
    return [f for f in fills if _key(f) > (mark["time"], str(mark["id"]))]


def mark_of(f):
    return {"id": str(f["id"]), "time": f["time"]}


def process(accounts, state, send, init=False, dry=False, cap=MAX_MESSAGES):
    """accounts = [(name, label, fills|None)]. Returns (messages printed/sent, new state)."""
    state, sent, budget, shown = {k: dict(v) if v else v for k, v in state.items()}, [], cap, []
    for name, label, fills in accounts:
        if fills is None:
            continue   # fetch failed: keep the old state, retry next run
        fills = sorted(fills, key=_key)
        if init or (name not in state and not dry):
            state[name] = mark_of(fills[-1]) if fills else {"id": None, "time": None}
            continue
        if name in state:
            fresh = new_fills(fills, state[name])
        else:   # dry run with no state: preview the latest trading day's fills
            day = _dt(fills[-1]["time"]).astimezone(NY).date() if fills else None
            fresh = [f for f in fills if _dt(f["time"]).astimezone(NY).date() == day]
        if not fresh:
            continue
        msgs = build_messages(label, fills, {str(f["id"]) for f in fresh})
        for i, (last, text) in enumerate(msgs):
            if budget == 0:
                rest = len(msgs) - i
                shown.append((name, f"+{rest} more fills ({label})"))
                if dry or send(f"+{rest} more fills ({label}), see the Alpaca account"):
                    state[name] = mark_of(fills[-1])
                break
            shown.append((name, text))
            if not dry and not send(text):
                print(f"{label}: send failed, will retry next run")
                break
            state[name] = mark_of(last)
            budget -= 1
    return [t for _, t in shown], state


def fetch_all():
    from alpaca.trading.client import TradingClient
    out = []
    for name, label, kenv, senv in ACCOUNTS:
        k, s = os.environ.get(kenv), os.environ.get(senv)
        if not (k and s):
            print(f"{label}: keys not set, skipped")
            continue
        try:
            out.append((name, label, fetch_fills(TradingClient(k, s, paper=True))))
        except Exception as e:
            print(f"{label}: could not fetch fills ({type(e).__name__})")
            out.append((name, label, None))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--init", action="store_true")
    a = ap.parse_args(argv)
    try:
        import telegram
        accounts = fetch_all()
        old = load_state()
        msgs, state = process(accounts, old, telegram.send, init=a.init, dry=a.dry_run)
        if a.dry_run:
            for m in msgs:
                print(m + "\n")
            print(f"{len(msgs)} message(s) (dry run: nothing sent or saved)")
            return 0
        if state != old:
            save_state(state)
        print(f"{len(msgs)} message(s) sent" if not a.init else "state initialised")
    except Exception as e:
        print(f"fill_alerts error: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
