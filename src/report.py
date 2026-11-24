"""Daily trading report (markdown) for EVERY system: Noise band, qlib, TradingAgents (live Alpaca paper accounts), day-trader-skill and Pelosi (shadow).
Writes reports/YYYY-MM-DD.md, reports/latest.md (copy of the newest) and reports/README.md.

    python src/report.py [YYYY-MM-DD]     # default: today, New York date

Pure functions build the text from plain data (so they are testable); thin fetchers call Alpaca with each account's
keys and fall back to the saved snapshot json when keys are missing or a call fails. All times are New York time.
Orders are fetched from 4 days back; the trades list uses each order's FILL time, the problems list uses its submit time.
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

import adjustments

ROOT = Path(__file__).resolve().parent.parent
MEM = ROOT / "agent_memory"
OUT = ROOT / "reports"
NY = ZoneInfo("America/New_York")
START = 25_000.0
CHECK_HOURS = [10, 11, 12, 13, 14, 15]
OPEN_STATUSES = {"new", "accepted", "pending_new", "held", "partially_filled", "pending_replace", "accepted_for_bidding", "calculated"}
BAD_STATUSES = {"rejected", "canceled", "expired", "suspended"}
ACCOUNTS = [("intraday", "ALPACA_INTRADAY_API_KEY", "ALPACA_INTRADAY_SECRET_KEY", MEM / "alpaca_intraday.json")]   # what briefing.py reads
# Report order: (key, title, live Alpaca key env, secret env, snapshot/state file, report_cards.json key). No env = shadow (simulated) system.
SYSTEMS = [("intraday", "Noise band", "ALPACA_INTRADAY_API_KEY", "ALPACA_INTRADAY_SECRET_KEY", MEM / "alpaca_intraday.json", "intraday"),
           ("qlib", "qlib", "ALPACA_DTS_API_KEY", "ALPACA_DTS_SECRET_KEY", MEM / "system_qlib.json", "qlib"),
           ("ta", "TradingAgents", "ALPACA_TA_API_KEY", "ALPACA_TA_SECRET_KEY", MEM / "system_ta.json", "ta")]   # day-trader-skill and Pelosi were retired 2026-11-23: not reported


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
            "pl": float(p.unrealized_pl),
            "day_pl": float(p.unrealized_intraday_pl) if p.unrealized_intraday_pl is not None else None} for p in c.get_all_positions()]
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


def qty_text(x):
    return num(x, 4).rstrip("0").rstrip(".") if x not in (None, "") else ""


def summary(title, acct):
    """Value now, change today, since start, cash and the open-positions table for one account."""
    e, le = acct.get("equity"), acct.get("last_equity")
    d = day_pl(acct)
    t = ""
    if acct.get("note"):
        t += f"_{acct['note']}_\n\n"
    if e is None:
        return t + "no data\n"
    t += (f"- Value now: **{usd(e)}**\n- Change today: {usd(d, True)} ({pct(d / le if le else None)})\n"
          f"- Since the {usd(START)} start: {usd(e - START, True)} ({pct((e - START) / START)})\n")
    if acct.get("cash") is not None:
        t += f"- Cash: {usd(acct.get('cash'))}\n"
    pos = acct.get("positions", [])
    if not pos:
        return t + "- Open positions: none\n"
    rows = [[p.get("symbol", "?"), qty_text(p.get("qty")), usd(p.get("avg_price", p.get("entry_price"))), usd(p.get("price")), usd(p.get("pl"), True)] for p in pos]
    return t + "\n" + table(["Symbol", "Qty", "Entry", "Now", "P/L"], rows)


def card_line(cards, key):
    """One line from report_cards.json for an account."""
    c = ((cards or {}).get("accounts") or {}).get(key)
    if not c:
        return "- Report card: not available yet\n"
    m = c.get("metrics")
    if not (c.get("ok") and m):
        return f"- Report card: {c.get('note') or 'not available yet'}\n"
    f = lambda k, fmt: "n/a" if m.get(k) is None else fmt(m[k])
    p1 = lambda v: f"{v * 100:+.1f}%"
    return (f"- Report card ({c.get('days')} days to {c.get('end')}): CAGR {f('cagr', p1)}, Sharpe {f('sharpe', lambda v: f'{v:.2f}')}, "
            f"Sortino {f('sortino', lambda v: f'{v:.2f}')}, max drawdown {f('max_dd', p1)}, win days {f('win_days', lambda v: f'{v * 100:.0f}%')}, "
            f"vol {f('vol', lambda v: f'{v * 100:.1f}%')}, beta {f('beta', lambda v: f'{v:.2f}')}\n")


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


def research(intra_text, cands):
    p = last_session(intra_text)
    t = f"- **Intraday agent, last session:** {p or 'no notes yet'}\n"
    m = re.search(r"Claude CLI cost[^.$]*\$[\d.]+", p)
    t += f"- **Claude CLI cost:** {m.group(0) if m else 'not reported in these notes'}\n"
    wait = [c for c in (cands or {}).get("candidates", []) if c.get("status") == "awaiting approval" and c.get("account") == "intraday"]
    t += "- **Candidates awaiting approval:** " + ("None yet.\n" if not wait else "\n" + "".join(
        f"  - {c['account']}: {c['description']} (held-out Sharpe at 2x costs {c['sharpe_2x_cost']})\n" for c in wait))
    return t


def first_line(text, n=140):
    t = re.split(r"(?<=[.!?])\s", (text or "").strip())[0]
    return t if len(t) <= n else t[:n - 1].rstrip() + "..."


def trade_rows(acct, day, why):
    fl = filled_on(acct.get("orders", []), day)
    return table(["Time (NY)", "Symbol", "Side", "Qty", "Avg fill", "Why"],
                 [[hhmm(o["filled"]), o["symbol"], o["side"], qty_text(o["filled_qty"]), usd(o["price"]), why(o)] for o in fl]) if fl else "None.\n"


def qlib_extra(state, day, acct):
    state = state or {}
    reb = state.get("rebalances") or []
    t = f"- Holding {len(state.get('picks') or [])} picks"
    t += (f"; last rebalance on {reb[-1].get('data_date')} data ({len(reb[-1].get('orders') or [])} orders queued)\n" if reb else "; no rebalance yet\n")
    return t + "\n### Trades today\n\n" + trade_rows(acct, day, lambda o: "rebalance: in" if o["side"] == "buy" else "rebalance: out")


def ta_extra(state, plans, day, acct):
    state = state or {}
    dec = [d for d in state.get("decisions", []) if d.get("date") == str(day)]
    latest = {d.get("symbol"): d for d in state.get("decisions", []) if str(d.get("date")) <= str(day)}
    why = lambda o: (lambda d: f"{d.get('rating')}: {first_line(d.get('rationale'))}" if d else "no matching decision")(latest.get(o["symbol"]))
    t = "\n### Decisions today\n\n" + (table(["Symbol", "Rating", "Rationale"], [[d.get("symbol"), d.get("rating"), first_line(d.get("rationale"))] for d in dec]) or "None.\n")
    t += "\n### Trades today\n\n" + trade_rows(acct, day, why)
    pl = (plans or {}).get("plans") or {}
    events = [f"{s}: {str(l)[12:]}" for s, p in sorted(pl.items()) for l in p.get("log", []) if str(l).startswith(str(day))]
    live = []
    for s, p in sorted(pl.items()):
        pp = p.get("plan") or {}
        if p.get("status") == "active" and p.get("opened"):
            live.append([s, pp.get("side", ""), usd(pp["stop"]) if pp.get("stop") else "none", usd(pp["tp"]) if pp.get("tp") else "none",
                         ", ".join(f"{x.get('size_x')}x {x.get('condition')} {usd(x.get('price'))}" for x in p.get("scale", []) if x.get("state") == "pending") or "none"])
    t += "\n### Plans, stops and scale-ins\n\n" + ("".join(f"- {e}\n" for e in events) or "- No stop, target or scale-in events today.\n")
    return t + ("\n" + table(["Symbol", "Side", "Stop", "Target", "Pending scale-ins"], live) if live else "")


def all_table(rows):
    out = []
    for name, e, d, le in rows:
        if e is None:
            out.append([name, "no data", "no data", "no data"])
        else:
            out.append([name, usd(e), f"{usd(d, True)} ({pct(d / le if le else None)})" if d is not None else "n/a",
                        f"{usd(e - START, True)} ({pct((e - START) / START)})"])
    have = [r for r in rows if r[1] is not None]
    if len(have) > 1:
        e = sum(r[1] for r in have)
        out.append(["**Total**", f"**{usd(e)}**", usd(sum(r[2] or 0 for r in have), True), usd(e - START * len(have), True)])
    return table(["Account", "Value now", "Today", "Since start"], out)


def news_section(state, day, n):
    """Shadow news-rule test: shocks flagged today, closed shadow trades by rule (n, average % per trade)."""
    t = f"\n## {n}. News shadow test\n\n"
    if not state:
        return t + "no data\n"
    try:
        shocks = [x for x in state.get("shocks", []) if x.get("date") == str(day)]
        t += f"- Shocks today: {len(shocks)}" + (": " + ", ".join(f"{x['symbol']} {x['ret'] * 100:+.1f}%" for x in shocks[:10]) if shocks else "") + "\n"
        by = {}
        for x in state.get("trades", []):
            if x.get("status") == "closed" and x.get("pnl_pct") is not None:
                by.setdefault(x.get("rule", "?"), []).append(x["pnl_pct"])
        if not by:
            return t + "- Closed shadow trades: none yet\n"
        return t + "\n" + table(["Rule", "Closed trades", "Avg % per trade"], [[r, len(v), f"{sum(v) / len(v):+.2f}%"] for r, v in sorted(by.items())])
    except Exception as e:
        return t + f"_could not read the news shadow data ({type(e).__name__})_\n"


def pl_header(vals):
    return "<!-- pl: " + " ".join(f"{k}={'' if v is None else round(v, 2)}" for k, v in vals.items()) + " -->\n"


ADJ_KEY = {"qlib": "dts"}   # report key -> Alpaca account key used in config/adjustments.json


def adjusted(key, a, day):
    """Copy of the account with manual corrections applied: equity += cumulative to day, day P/L += that day's corrections."""
    ak = ADJ_KEY.get(key, key)
    cum, prev = adjustments.cum_on(ak, str(day)), adjustments.cum_on(ak, str(day - timedelta(days=1)))
    if a.get("equity") is None or not (cum or prev):
        return a, []
    b = dict(a, equity=a["equity"] + cum)
    if b.get("last_equity"):
        b["last_equity"] = a["last_equity"] + prev
    return b, [x for x in adjustments.for_account(ak) if str(x.get("date")) <= str(day)]


def build(day, accts, noise_rows, intra_lessons, cands, extras=None):
    """accts: {key: account dict} (a missing key reads 'no data'); extras: {'cards': report_cards.json, 'plans': system_ta_plans.json, 'states': {key: state json}}."""
    ex = extras or {}
    states, cards = ex.get("states", {}), ex.get("cards")
    day_noise = [r for r in noise_rows if r["time_ny"].startswith(str(day))]
    nodata = {"equity": None, "last_equity": None, "positions": [], "orders": [], "note": ""}
    body, rows, pls = "", [], {}
    for n, (key, title, _ke, _se, _p, card) in enumerate(SYSTEMS, 1):
        a, adjs = adjusted(key, accts.get(key) or nodata, day)
        d = day_pl(a)
        rows.append((title, a.get("equity"), d, a.get("last_equity")))
        pls[key] = d
        sec = f"\n## {n}. {title}\n\n"
        try:
            sec += summary(title, a)
            for x in adjs:
                sec += f"- Includes split correction {usd(x['usd'], True)} ({x.get('symbol', '?')}, Alpaca paper bug)\n"
            if a.get("equity") is not None or key == "intraday":
                sec += card_line(cards, card)
                if key == "intraday":
                    sec += ("\n### Trades today\n\n" + trades(a, day, lambda o: intraday_why(o, day_noise)) +
                            "\n### Intraday checks today\n\n" + checks_table(day_noise))
                elif key == "qlib":
                    sec += qlib_extra(states.get(key), day, a)
                else:
                    sec += ta_extra(states.get(key), ex.get("plans"), day, a)
        except Exception as e:   # one broken section must never sink the report
            sec += f"_could not build the detail for this account ({type(e).__name__}: {str(e)[:80]})_\n"
        body += sec
    prob = problems(day, accts, noise_rows)
    return (pl_header(pls) + f"# Trading report {day} ({day:%A})\n\nAll times are New York time. Paper money only. "
            "Live paper accounts: Noise band, qlib, TradingAgents; the news shadow test is simulated (no orders). "
            "The long-term account was retired 2026-11-09; day-trader-skill and Pelosi were retired 2026-11-23.\n\n## All accounts\n\n" + all_table(rows) + body + news_section(ex.get("news"), day, len(SYSTEMS) + 1) +
            "\n## Problems\n\n" + ("".join(f"- {p}\n" for p in prob) if prob else "None.\n") +
            "\n## Research\n\n" + research(intra_lessons, cands))


def index(out_dir):
    """Rewrite README.md: one line per report, newest first, each account's day P/L (old '<!-- pl: long-term=.. intraday=.. -->' headers still read)."""
    keys = [k for k, *_ in SYSTEMS]
    lines = []
    for f in sorted(Path(out_dir).glob("????-??-??.md"), reverse=True):
        m = re.search(r"<!-- pl: (.*?) -->", f.read_text())
        kv = dict(re.findall(r"([\w-]+)=(\S*)", m.group(1))) if m else {}
        s = lambda v: "n/a" if not v else usd(float(v), True)
        lines.append(f"- [{f.stem}]({f.name}): " + ", ".join(f"{k} {s(kv.get(k))}" for k in keys if k == "intraday" or k in kv))
    (Path(out_dir) / "README.md").write_text("# Daily trading reports\n\nNewest first. Day P/L of each paper account (intraday = Noise band). The newest report is also latest.md.\n\n" + "\n".join(lines) + "\n")


def get_system_account(key, kenv, senv, path, day):
    """Live Alpaca account for a traded system (snapshot fallback). Never raises."""
    try:
        return get_account(key, kenv, senv, path, day)
    except Exception as e:
        return {"ok": False, "equity": None, "cash": None, "last_equity": None, "positions": [], "orders": [],
                "note": f"{key}: could not read ({type(e).__name__})"}


def main(date_str=None):
    day = date.fromisoformat(date_str) if date_str else datetime.now(NY).date()
    accts = {k: get_system_account(k, ke, se, p, day) for k, _t, ke, se, p, _c in SYSTEMS}
    extras = {"cards": read_json(MEM / "report_cards.json"), "plans": read_json(MEM / "system_ta_plans.json"), "news": read_json(MEM / "news_shadow.json"),
              "states": {k: read_json(p) for k, _t, _ke, _se, p, _c in SYSTEMS}}
    text = build(day, accts, read_csv(MEM / "noise_live.csv"),
                 (MEM / "intraday" / "lessons.md").read_text() if (MEM / "intraday" / "lessons.md").exists() else "",
                 read_json(MEM / "candidates.json"), extras)
    OUT.mkdir(exist_ok=True)
    (OUT / f"{day}.md").write_text(text)
    (OUT / "latest.md").write_text(text)
    index(OUT)
    print(f"wrote reports/{day}.md (and reports/latest.md)")
    return text


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
