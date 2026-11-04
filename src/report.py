"""Daily trading report (markdown) for both Alpaca paper accounts: reports/YYYY-MM-DD.md plus reports/README.md.

    python src/report.py [YYYY-MM-DD]     # default: today, New York date

Pure functions build the text from plain data (so they are testable); thin fetchers call Alpaca with each account's
keys and fall back to the saved snapshot json when keys are missing or a call fails. All times are New York time.
Orders are fetched from 4 days back, because a long-term order sent last evening fills at today's open; the
trades list uses each order's FILL time, the problems list uses its submit time.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
MEM = ROOT / "agent_memory"
OUT = ROOT / "reports"
NY = ZoneInfo("America/New_York")
START = 25_000.0
CHECK_HOURS = [10, 11, 12, 13, 14, 15]
OPEN_STATUSES = {"new", "accepted", "pending_new", "held", "partially_filled", "pending_replace", "accepted_for_bidding", "calculated"}
BAD_STATUSES = {"rejected", "canceled", "expired", "suspended"}
ACCOUNTS = [("long-term", "ALPACA_API_KEY", "ALPACA_SECRET_KEY", MEM / "alpaca_account.json"),
            ("intraday", "ALPACA_INTRADAY_API_KEY", "ALPACA_INTRADAY_SECRET_KEY", MEM / "alpaca_intraday.json")]


# ---------- formatting ----------
def usd(x, sign=False):
    if x is None:
        return "n/a"
    return f"{'+' if sign and x > 0 else ''}{'-' if x < 0 else ''}${abs(x):,.2f}"


def pct(x):
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def hhmm(dt):
    return dt.astimezone(NY).strftime("%H:%M") if dt else "?"


def num(x, d=2):
    return "" if x in (None, "") else f"{float(x):,.{d}f}"


def table(head, rows):
    if not rows:
        return ""
    esc = lambda c: str(c).replace("|", "/")
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] +
                     ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]) + "\n"


def norm(sym):
    return (sym or "").replace("/", "")


# ---------- input shaping ----------
def read_csv(path):
    return list(csv.DictReader(Path(path).open())) if Path(path).exists() else []


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def acct_from_snapshot(snap, why):
    """Plain account dict from a snapshot json (or an 'unavailable' stub when there is none)."""
    if not snap:
        return {"ok": False, "equity": None, "cash": None, "last_equity": None, "positions": [], "orders": [],
                "note": f"{why}; no snapshot file either, so no numbers"}
    orders = []
    for o in snap.get("orders", []):
        try:
            t = datetime.fromisoformat(o["date"])
            t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            t = None
        orders.append({"id": "", "symbol": o["symbol"], "side": o["side"], "qty": o.get("qty"), "filled_qty": o.get("qty") if o.get("status") == "filled" else 0,
                       "price": o.get("price"), "status": o.get("status", ""), "submitted": t, "filled": t if o.get("status") == "filled" else None})
    return {"ok": False, "equity": snap.get("equity"), "cash": snap.get("cash"), "last_equity": snap.get("last_equity"),
            "positions": snap.get("positions", []), "orders": orders,
            "note": f"{why}; using the snapshot saved {snap.get('generated', '?')} UTC (last 15 orders only, fill times are submit times)"}


def fetch_account(key, secret, day):
    """Live account dict from Alpaca (raises on any failure; the caller falls back to the snapshot)."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    c = TradingClient(key, secret, paper=True)
    a = c.get_account()
    pos = [{"symbol": p.symbol, "qty": float(p.qty), "avg_price": float(p.avg_entry_price), "price": float(p.current_price),
            "pl": float(p.unrealized_pl)} for p in c.get_all_positions()]
    start = datetime.combine(day - timedelta(days=4), time.min, NY)
    orders = []
    for o in c.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, after=start, limit=500)):
        orders.append({"id": str(o.id), "symbol": o.symbol, "side": o.side.value, "qty": float(o.qty or 0),
                       "filled_qty": float(o.filled_qty or 0), "price": float(o.filled_avg_price) if o.filled_avg_price else None,
                       "status": o.status.value, "submitted": o.submitted_at, "filled": o.filled_at})
    return {"ok": True, "equity": float(a.equity), "cash": float(a.cash), "last_equity": float(a.last_equity),
            "positions": pos, "orders": orders, "note": ""}


def get_account(name, kenv, senv, snap_path, day):
    key, secret = os.environ.get(kenv), os.environ.get(senv)
    snap = read_json(snap_path)
    if not (key and secret):
        return acct_from_snapshot(snap, f"{kenv} / {senv} not set")
    try:
        return fetch_account(key, secret, day)
    except Exception as e:
        return acct_from_snapshot(snap, f"Alpaca fetch failed ({type(e).__name__}: {str(e)[:100]})")


# ---------- pure builders ----------
def day_pl(acct):
    e, le = acct.get("equity"), acct.get("last_equity")
    return None if e is None or not le else e - le


def summary(name, acct):
    e, le = acct.get("equity"), acct.get("last_equity")
    d = day_pl(acct)
    t = f"### {name.capitalize()} account\n\n"
    if acct.get("note"):
        t += f"_{acct['note']}_\n\n"
    if e is None:
        return t + "No account numbers available.\n"
    t += (f"- Value now: **{usd(e)}**\n- Change today: {usd(d, True)} ({pct(d / le if le else None)})\n"
          f"- Since the {usd(START)} start: {usd(e - START, True)} ({pct((e - START) / START)})\n- Cash: {usd(acct.get('cash'))}\n")
    pos = acct.get("positions", [])
    if not pos:
        return t + "- Open positions: none\n"
    rows = [[p["symbol"], num(p["qty"], 4).rstrip("0").rstrip("."), usd(p["avg_price"]), usd(p["price"]), usd(p["pl"], True)] for p in pos]
    return t + "\n" + table(["Symbol", "Qty", "Avg price", "Now", "P/L"], rows)


def filled_on(orders, day):
    """Orders filled (fully or partly) on that New York day, oldest first."""
    out = [o for o in orders if (o.get("filled_qty") or 0) > 0 and o.get("filled") and o["filled"].astimezone(NY).date() == day]
    return sorted(out, key=lambda o: o["filled"])


def intraday_why(o, rows):
    r = next((r for r in rows if o.get("id") and r.get("order_id") == o["id"]), None)
    if not r:
        return "no matching log row"
    ev = r.get("event", "")
    if ev == "check":
        return (f"{r['decision']}: price {r['price']} vs upper {r['upper']}, lower {r['lower']}, VWAP {r['vwap']}"
                if r.get("decision") else "check")
    if "safety" in ev:
        return "safety flatten (left over from an earlier day)"
    return "close-out at the close" if "flatten" in ev else ev


def latest_in(paper_rows):
    """symbol (no slash) -> names of strategies whose latest logged decision is 'in' (agent ideas are not traded)."""
    last = {}
    for r in paper_rows:
        if r["key"].startswith("AGENT-"):
            continue
        if r["key"] not in last or r["decision_date"] > last[r["key"]]["decision_date"]:
            last[r["key"]] = r
    out = {}
    for r in last.values():
        if float(r["position"]) > 0:
            out.setdefault(norm(r["market"]), []).append(r["name"])
    return out


def longterm_why(o, order_rows, ins):
    bits = []
    r = next((r for r in order_rows if o.get("id") and r.get("order_id") == o["id"]), None)
    if r:
        bits.append(f"sent {r['sent_at_utc'][:16].replace('T', ' ')} UTC as {r['tif']}, target ${num(r['target_usd'], 0)}")
    names = ins.get(norm(o["symbol"]))
    bits.append("strategies in: " + ", ".join(names) if names else "no strategy currently in this symbol")
    return "; ".join(bits)


def trades(acct, day, why):
    fl = filled_on(acct.get("orders", []), day)
    if not fl:
        return "None.\n"
    return table(["Time (NY)", "Symbol", "Side", "Qty", "Avg fill", "Why"],
                 [[hhmm(o["filled"]), o["symbol"], o["side"], num(o["filled_qty"], 4).rstrip("0").rstrip("."), usd(o["price"]), why(o)] for o in fl])


def checks_table(rows):
    if not rows:
        return "None.\n"
    return table(["Time (NY)", "Symbol", "Decision", "Price", "Upper", "Lower", "VWAP"],
                 [[r["time_ny"][11:16], r["symbol"], r.get("decision") or r.get("event", ""), r.get("price", ""), r.get("upper", ""),
                   r.get("lower", ""), r.get("vwap", "")] for r in rows])


def waiting(acct, order_rows, day):
    t = ""
    op = [o for o in acct.get("orders", []) if o.get("status") in OPEN_STATUSES]
    if op:
        t += "Open at Alpaca:\n\n" + table(["Symbol", "Side", "Qty", "Status"], [[o["symbol"], o["side"], num(o["qty"], 4).rstrip("0").rstrip("."), o["status"]] for o in op]) + "\n"
    tonight = []
    for r in order_rows:
        try:
            sent = datetime.fromisoformat(r["sent_at_utc"]).astimezone(NY)
        except (KeyError, ValueError):
            continue
        if sent.date() == day:
            tonight.append([sent.strftime("%H:%M"), r["symbol"], r["side"], r["qty"], r["tif"], r["status"].replace("OrderStatus.", "")])
    if tonight:
        t += "Sent tonight (alpaca_orders.csv):\n\n" + table(["Time (NY)", "Symbol", "Side", "Qty", "Type", "Status when sent"], tonight)
    return t or "None.\n"


def problems(day, accts, noise_rows):
    """Plain-English list of things that went wrong (empty list = none)."""
    out = []
    for name, a in accts.items():
        if a.get("note"):
            out.append(f"{name}: {a['note']}")
        for o in a.get("orders", []):
            sub = o.get("submitted")
            if o.get("status") in BAD_STATUSES and sub and sub.astimezone(NY).date() == day:
                out.append(f"{name}: {o['side']} {o['qty']:g} {o['symbol']} order was {o['status']} (sent {hhmm(sub)} NY)")
    rows = [r for r in noise_rows if r["time_ny"].startswith(str(day)) and r.get("event") == "check" and r.get("decision")]
    if day.weekday() < 5:
        if not noise_rows or not any(r["time_ny"].startswith(str(day)) for r in noise_rows):
            out.append("intraday: no log rows today (market holiday, or the job did not run)")
        else:
            for sym in sorted({r["symbol"] for r in noise_rows if r["time_ny"].startswith(str(day))} | {"SPY", "QQQ"}):
                for h in CHECK_HOURS:
                    if not any(r["symbol"] == sym and h * 60 <= int(r["time_ny"][11:13]) * 60 + int(r["time_ny"][14:16]) < (h + 1) * 60 - (14 if h == 15 else 0)
                               for r in rows):
                        out.append(f"intraday: no {h}:00 check logged for {sym}")
    for p in accts.get("intraday", {}).get("positions", []):
        out.append(f"intraday: still holding {p['qty']:g} {p['symbol']} at report time (should be flat after the close)")
    return out


def last_session(text):
    """The paragraph under '## Last session' in a lessons.md ('' if none)."""
    m = re.search(r"^## Last session\s*\n+(.*?)(?=^## |\Z)", text or "", re.S | re.M)
    return m.group(1).strip() if m else ""


def research(daily_text, intra_text, cands):
    t = ""
    for label, txt in (("Daily agent", daily_text), ("Intraday agent", intra_text)):
        p = last_session(txt)
        t += f"- **{label}, last session:** {p or 'no notes yet'}\n"
    cost = [m.group(0) for m in (re.search(r"Claude CLI cost[^.$]*\$[\d.]+", x or "") for x in (last_session(daily_text), last_session(intra_text))) if m]
    t += f"- **Claude CLI cost:** {'; '.join(cost) if cost else 'not reported in these notes'}\n"
    wait = [c for c in (cands or {}).get("candidates", []) if c.get("status") == "awaiting approval"]
    t += "- **Candidates awaiting approval:** " + ("None yet.\n" if not wait else "\n" + "".join(
        f"  - {c['account']}: {c['description']} (held-out Sharpe at 2x costs {c['sharpe_2x_cost']})\n" for c in wait))
    return t


def build(day, accts, noise_rows, order_rows, paper_rows, daily_lessons, intra_lessons, cands):
    lt, it = accts["long-term"], accts["intraday"]
    day_noise = [r for r in noise_rows if r["time_ny"].startswith(str(day))]
    ins = latest_in(paper_rows)
    pl = lambda a: day_pl(a)
    head = f"<!-- pl: long-term={'' if pl(lt) is None else round(pl(lt), 2)} intraday={'' if pl(it) is None else round(pl(it), 2)} -->\n"
    prob = problems(day, accts, noise_rows)
    return (head + f"# Trading report {day} ({day:%A})\n\nAll times are New York time. Paper money only.\n\n"
            "## 1. Summary\n\n" + summary("long-term", lt) + "\n" + summary("intraday", it) +
            "\n## 2. Trades today\n\n### Long-term account\n\n" + trades(lt, day, lambda o: longterm_why(o, order_rows, ins)) +
            "\n### Intraday account\n\n" + trades(it, day, lambda o: intraday_why(o, day_noise)) +
            "\n## 3. Intraday checks today\n\n" + checks_table(day_noise) +
            "\n## 4. Orders waiting for tomorrow's open (long-term)\n\n" + waiting(lt, order_rows, day) +
            "\n## 5. Problems\n\n" + ("".join(f"- {p}\n" for p in prob) if prob else "None.\n") +
            "\n## 6. Research\n\n" + research(daily_lessons, intra_lessons, cands))


def index(out_dir):
    """Rewrite README.md: one line per report, newest first, with both accounts' day P/L."""
    lines = []
    for f in sorted(Path(out_dir).glob("????-??-??.md"), reverse=True):
        m = re.search(r"<!-- pl: long-term=(\S*) intraday=(\S*) -->", f.read_text())
        s = lambda v: "n/a" if not v else usd(float(v), True)
        lines.append(f"- [{f.stem}]({f.name}): long-term {s(m and m.group(1))}, intraday {s(m and m.group(2))}")
    (Path(out_dir) / "README.md").write_text("# Daily trading reports\n\nNewest first. Day P/L per paper account.\n\n" + "\n".join(lines) + "\n")


def main(date_str=None):
    day = date.fromisoformat(date_str) if date_str else datetime.now(NY).date()
    accts = {n: get_account(n, k, s, p, day) for n, k, s, p in ACCOUNTS}
    text = build(day, accts, read_csv(MEM / "noise_live.csv"), read_csv(MEM / "alpaca_orders.csv"), read_csv(MEM / "paper_log.csv"),
                 (MEM / "lessons.md").read_text() if (MEM / "lessons.md").exists() else "",
                 (MEM / "intraday" / "lessons.md").read_text() if (MEM / "intraday" / "lessons.md").exists() else "",
                 read_json(MEM / "candidates.json"))
    OUT.mkdir(exist_ok=True)
    (OUT / f"{day}.md").write_text(text)
    index(OUT)
    print(f"wrote reports/{day}.md")
    return text


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
