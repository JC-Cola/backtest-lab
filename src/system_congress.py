"""Congress shadow account: copies the disclosed stock trades of followed US House members. SHADOW ONLY - no broker, no orders.

    python system_congress.py run                   # nightly (after the close): fetch new filings, replay, save, Telegram
    python system_congress.py run --today 2026-11-16 --no-telegram

Rules (all in CFG):
  * Data: House Clerk PTRs through the vendored trader-pelosi tool (congress_data.py). House only; the Senate site needs a
    person to accept an agreement, so it is not automated. Unreadable filings are recorded as "unparsed" with their link.
  * Timing: a filing becomes public on its FILING date, so the shadow acts at the first market OPEN strictly after the
    filing date (or after the day we first saw it, if later). The trade date (up to 45 days earlier) and the lag are recorded.
  * Equities only: purchase, sale, partial sale. Options / exercises, exchanges and non-stock assets are recorded, never traded.
  * Size: a buy's weight = CAP * midpoint / REF_MID of the disclosed range (REF_MID = the $1M-$5M band midpoint, so that band is
    a full 20% slot), clamped to CAP; at most CAP (20%) of equity per name, long only, limited by cash. "Sale" closes the copied
    position, "Sale (partial)" sells half of it; a sale of something the shadow never held is ignored.
  * Costs: 0.05% per side, as a worse fill price. Start equity $25,000. Fractional shares.
  * The ledger is a pure function of the stored filings plus daily prices (replay), so every run is idempotent and a backfill
    is just the same replay over older filings (rows whose exec date is on or before the first live run are "backfilled").
State: agent_memory/system_congress.json (Alpaca-snapshot shape + "filings", "docs", "mode": "shadow").
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import os
import sys
from pathlib import Path

import congress_data as cd

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "agent_memory" / "system_congress.json"
LABEL = "Pelosi (shadow)"
CFG = {"follow": ["Pelosi"],            # add more members here: last name, "First Last" or "Last, First"
       "start_equity": 25000.0, "cap": 0.20, "ref_mid": 3_000_000.0, "cost": 0.0005,
       "backfill_from": "2026-01-01", "benchmark": "SPY", "orders_keep": 300}


# ---------------------------------------------------------------- the replay (pure)

def _next_idx(dates, after):
    """Index of the first date strictly after `after` (ISO strings sort correctly)."""
    return bisect.bisect_right(dates, after)


def replay(records, bars, cfg=CFG, live_since=None):
    """Replay copyable records over daily `bars` ({sym: {date: {"o","c"}}}). Mutates each record's status/exec fields and
    returns {"positions","trades","orders","cash","curve":[(date, equity)],"opens"}. Deterministic."""
    cost, cap = cfg["cost"], cfg["cap"]
    cal = sorted(bars.get(cfg["benchmark"], {}))
    if not cal:
        cal = sorted({d for b in bars.values() for d in b})
    work = []
    for r in records:
        if r["kind"] not in ("buy", "sell_full", "sell_partial"):
            continue
        sd = sorted(bars.get(r["ticker"], {}))
        if not sd:
            r.update(status="no_price", note="no daily bars for %s" % r["ticker"])
            continue
        after = max(r["filed"], r.get("first_seen") or r["filed"])
        i = _next_idx(sd, after)
        if i >= len(sd):
            if cal and cal[-1] > sd[-1]:                      # the market moved on and this ticker stopped trading
                r.update(status="no_price", note="%s has no bars after %s (delisted or renamed)" % (r["ticker"], sd[-1]))
            else:
                r.update(status="pending", exec_date=None)
            continue
        r["exec_date"] = sd[i]
        work.append(r)
    work.sort(key=lambda r: (r["exec_date"], r["filed"], r["key"]))
    by_day: dict = {}
    for r in work:
        by_day.setdefault(r["exec_date"], []).append(r)

    cash, pos, trades, orders, curve, invested = cfg["start_equity"], {}, [], [], [], []
    last_px: dict = {}
    first = min(by_day) if by_day else None
    days = [d for d in cal if first and d >= first]
    for day in days:
        eq0 = cash + sum(p["qty"] * last_px.get(s, p["avg_price"]) for s, p in pos.items())
        for r in by_day.get(day, []):
            sym, b = r["ticker"], bars[r["ticker"]][day]
            if r["kind"] == "buy":
                if not r.get("mid"):
                    r.update(status="skipped", note="no amount on the filing")
                    continue
                want = cap * min(1.0, r["mid"] / cfg["ref_mid"]) * eq0
                held = pos[sym]["qty"] * last_px.get(sym, b["o"]) if sym in pos else 0.0
                notional = min(want, cap * eq0 - held, cash / (1 + cost))
                if notional < 25.0:
                    r.update(status="skipped", note="at the %d%% cap or out of cash" % round(cap * 100))
                    continue
                fill = b["o"] * (1 + cost)
                qty = round(notional / fill, 4)
                cash -= qty * fill
                p = pos.setdefault(sym, {"symbol": sym, "qty": 0.0, "avg_price": 0.0, "entry_date": day, "member": r["member"],
                                         "doc_id": r["doc_id"], "link": r["link"]})
                p["avg_price"] = (p["qty"] * p["avg_price"] + qty * fill) / (p["qty"] + qty)
                p["qty"] = round(p["qty"] + qty, 4)
                last_px[sym] = b["o"]
                orders.append({"date": day, "symbol": sym, "side": "buy", "qty": qty, "status": "filled", "price": round(fill, 4),
                               "member": r["member"], "doc_id": r["doc_id"]})
                r.update(status="copied", fill=round(fill, 4), qty=qty, weight=round(qty * fill / eq0, 4))
            else:
                if sym not in pos:
                    r.update(status="ignored", note="sale of a name the shadow never held")
                    continue
                p = pos[sym]
                qty = p["qty"] if r["kind"] == "sell_full" else round(p["qty"] / 2, 4)
                fill = b["o"] * (1 - cost)
                cash += qty * fill
                pnl = round(qty * (fill - p["avg_price"]), 2)
                trades.append({"id": "%s-%s" % (day, sym), "symbol": sym, "side": "long", "qty": qty, "entry_time": p["entry_date"],
                               "entry_price": round(p["avg_price"], 4), "exit_time": day, "exit_price": round(fill, 4),
                               "pnl": pnl, "pnl_pct": round(100.0 * (fill / p["avg_price"] - 1), 3), "date": day,
                               "reason": "%s (%s %s)" % (r["type"].lower(), r["member"], r["filed"]), "member": r["member"],
                               "doc_id": r["doc_id"], "fees": round(qty * (p["avg_price"] / (1 + cost) * cost + fill / (1 - cost) * cost), 2)})
                orders.append({"date": day, "symbol": sym, "side": "sell", "qty": qty, "status": "filled", "price": round(fill, 4),
                               "member": r["member"], "doc_id": r["doc_id"]})
                p["qty"] = round(p["qty"] - qty, 4)
                if p["qty"] <= 0.0001:
                    del pos[sym]
                last_px[sym] = b["o"]
                r.update(status="copied", fill=round(fill, 4), qty=qty)
            r["backfilled"] = bool(live_since and day <= live_since)
        for sym in list(pos):
            b = bars.get(sym, {}).get(day)
            if b:
                last_px[sym] = b["c"]
        inv = sum(p["qty"] * last_px.get(s, p["avg_price"]) for s, p in pos.items())
        curve.append((day, round(cash + inv, 2)))
        invested.append(round(inv, 2))
    out = []
    for s, p in pos.items():
        px = last_px.get(s, p["avg_price"])
        out.append({**p, "price": round(px, 4), "value": round(p["qty"] * px, 2), "pl": round(p["qty"] * (px - p["avg_price"]), 2),
                    "pl_pct": round(px / p["avg_price"] - 1, 5), "avg_price": round(p["avg_price"], 4), "date": p["entry_date"]})
    return {"positions": out, "trades": trades, "orders": orders, "cash": round(cash, 2), "curve": curve,
            "invested": invested}


# ---------------------------------------------------------------- snapshot (same shape as the other systems)

def _ts(day, hh=21):
    return int((dt.datetime.fromisoformat(day + "T%02d:00:00" % hh) - dt.datetime(1970, 1, 1)).total_seconds())


def snapshot(state, rp, now, cfg=CFG):
    start = cfg["start_equity"]
    curve = rp["curve"]
    eq = curve[-1][1] if curve else start
    prev = curve[-2][1] if len(curve) > 1 else start
    cal, before = [], start
    for d, e in curve:
        cal.append({"date": d, "pl": round(e - before, 2), "pct": round(100.0 * (e - before) / before, 2), "equity": e})
        before = e
    rows, bal = [], start
    for o in rp["orders"]:
        tr = next((t for t in rp["trades"] if o["side"] == "sell" and t["date"] == o["date"] and t["symbol"] == o["symbol"]), None)
        pnl = tr["pnl"] if tr else 0.0
        after = round(bal + pnl, 2)
        rows.append({"time": o["date"] + " 13:30:00", "balance_before": round(bal, 2), "balance_after": after, "realized_pnl": pnl,
                     "action": "%s long position for symbol %s at price %.2f for %s units (%s filing %s)"
                               % ("Open" if o["side"] == "buy" else "Close", o["symbol"], o["price"], o["qty"], o["member"], o["doc_id"])})
        bal = after
    unreal = round(sum(p["pl"] for p in rp["positions"]), 2)
    day = curve[-1][0] if curve else now.date().isoformat()
    state.update({
        "system": LABEL, "label": LABEL, "mode": "shadow", "follow": list(cfg["follow"]), "start_equity": start,
        "cash": rp["cash"], "equity": eq, "last_equity": prev, "positions": rp["positions"], "trades": rp["trades"],
        "orders": rp["orders"][-cfg["orders_keep"]:], "calendar": cal, "generated": now.strftime("%Y-%m-%dT%H:%M+00:00"),
        "history": {"1D": [[_ts(day, 14), prev], [_ts(day, 21), eq]] if curve else [], "ALL": [[_ts(d), e] for d, e in curve]},
        "balance_history": {"rows": rows[-400:], "realized": round(sum(t["pnl"] for t in rp["trades"]), 2), "unrealized": unreal,
                            "equity": eq, "available": rp["cash"], "margin": 0.0, "start_balance": start},
        "source": "shadow-ledger"})
    f = state["filings"]
    lags = [r["lag_days"] for r in f if r.get("lag_days") is not None]
    state["stats"] = {"filings": len({r["doc_id"] for r in f}), "transactions": len([r for r in f if r["kind"] != "unparsed"]),
                      "copied": len([r for r in f if r["status"] == "copied"]),
                      "options_recorded": len([r for r in f if r["kind"] == "option"]),
                      "unparsed": len([r for r in f if r["kind"] == "unparsed"]),
                      "avg_lag_days": round(sum(lags) / len(lags), 1) if lags else None}
    return state


# ---------------------------------------------------------------- Telegram text

def _md(day):
    d = dt.date.fromisoformat(day)
    return "%s %d" % (d.strftime("%b"), d.day)


def _next_open_phrase(today):
    nxt = today + dt.timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += dt.timedelta(days=1)
    return "tomorrow's open" if (nxt - today).days == 1 else "%s's open" % nxt.strftime("%A")


def message(r, today):
    who = "%s filed" % r["member"]
    if r["kind"] == "unparsed":
        return "🏛️ %s a periodic report I could not read (%s): %s" % (who, r["note"], r["link"])
    head = "🏛️ %s (traded %s, filed %s): " % (who, _md(r["traded"]) if r["traded"] else "?", _md(r["filed"]))
    what = "%s %s %s" % ({"buy": "BUY", "sell_full": "SELL", "sell_partial": "SELL (partial)"}.get(r["kind"], r["type"] or "?").upper(),
                         r["ticker"] or r["asset"] or "?", r["amount_range"])
    if r["kind"] == "buy":
        tail = "shadow buys at %s" % _next_open_phrase(today)
    elif r["kind"] == "sell_full":
        tail = "shadow closes it at %s if held" % _next_open_phrase(today)
    elif r["kind"] == "sell_partial":
        tail = "shadow sells half at %s if held" % _next_open_phrase(today)
    else:
        tail = "recorded only (%s)" % r["note"]
    return head + what + " → " + tail


# ---------------------------------------------------------------- the nightly run

def load(path=STATE_PATH):
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (ValueError, OSError):
            pass
    return {"filings": [], "docs": {}, "notified": [], "live_since": None}


def save(state, path=STATE_PATH):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n")
    os.replace(tmp, p)


def ingest(state, index_rows, today, cfg=CFG, text_of=None, cache_dir=None):
    """Read every new (or still-unread) filing in `index_rows` into state["filings"]. Returns the doc_ids touched."""
    touched = []
    live = state.get("live_since") is not None
    for row in index_rows:
        doc = state["docs"].get(row["doc_id"])
        if doc and doc["status"] == "ok":
            continue
        member = cd.member_key(row, cfg["follow"])
        seen = today.isoformat() if live else None           # first run is the backfill: judged on the filing date alone
        kw = {"text_of": text_of} if text_of else {}
        if cache_dir:
            kw["cache_dir"] = cache_dir
        rows, why = cd.read_filing(row, **kw)
        old = [r for r in state["filings"] if r["doc_id"] == row["doc_id"]]
        if doc and why and len(rows) == doc.get("rows", 0) and old:
            continue                                         # still unreadable the same way; keep what we had
        first = (doc or {}).get("first_seen", seen)
        recs = cd.to_records(member, row, rows, first)
        if why:
            recs.append(cd.unparsed_record(member, row, why, first))
        state["filings"] = [r for r in state["filings"] if r["doc_id"] != row["doc_id"]] + recs
        state["docs"][row["doc_id"]] = {"member": member, "filed": row["filed"], "first_seen": first, "rows": len(rows),
                                        "status": "ok" if not why else "unread", "link": row["url"], "reason": why}
        touched.append(row["doc_id"])
    state["filings"].sort(key=lambda r: (r["filed"], r["key"]))
    return touched


def run(today=None, path=STATE_PATH, cfg=CFG, index_fn=None, bars_fn=None, notify=None, now=None, text_of=None, cache_dir=None):
    today = today or dt.date.today()
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    state = load(path)
    first_run = state.get("live_since") is None
    years = list(range(int(cfg["backfill_from"][:4]), today.year + 1))
    index_fn = index_fn or (lambda: cd.index_filings(cfg["follow"], years, since=cfg["backfill_from"]))
    touched = ingest(state, index_fn(), today, cfg, text_of, cache_dir)
    if first_run:
        state["live_since"] = today.isoformat()
    syms = {r["ticker"] for r in state["filings"] if r["kind"] in ("buy", "sell_full", "sell_partial")} | {cfg["benchmark"]}
    start = (dt.date.fromisoformat(min([r["filed"] for r in state["filings"]] + [today.isoformat()])) - dt.timedelta(days=7)).isoformat()
    bars = (bars_fn or cd.daily_bars)(sorted(syms), start, today.isoformat())
    for r in state["filings"]:                               # a fresh replay decides every status again
        if r["kind"] in ("buy", "sell_full", "sell_partial"):
            r.update(status="pending")
            for k in ("exec_date", "fill", "qty", "weight", "backfilled"):
                r.pop(k, None)
    rp = replay(state["filings"], bars, cfg, live_since=state["live_since"])
    snapshot(state, rp, now, cfg)
    sent = []
    if not first_run:
        todo: dict = {}
        for r in state["filings"]:
            if r["key"] not in state["notified"]:
                todo.setdefault(r["doc_id"], []).append(r)
        for rs in todo.values():                              # one message per filing
            lines = [message(r, today) for r in rs[:8]] + (["… +%d more rows in the filing" % (len(rs) - 8)] if len(rs) > 8 else [])
            text = "\n".join(lines)
            if notify is None or notify(text):
                sent.append(text)
                state["notified"] += [r["key"] for r in rs]
    else:
        state["notified"] = [r["key"] for r in state["filings"]]     # the backfill never pings
    save(state, path)
    return state, sent


def summarize(state) -> str:
    s = state["stats"]
    return ("%s: equity $%.2f (start $%.0f), %d positions, %d filings / %d transactions, %d copied, %d options recorded, "
            "%d unparsed, avg lag %s days" % (LABEL, state["equity"], state["start_equity"], len(state["positions"]), s["filings"],
                                                s["transactions"], s["copied"], s["options_recorded"], s["unparsed"], s["avg_lag_days"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("--today")
    p.add_argument("--state", default=str(STATE_PATH))
    p.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args(argv)
    notify = None
    if not a.no_telegram:
        import telegram
        notify = telegram.send
    today = dt.date.fromisoformat(a.today) if a.today else None
    state, sent = run(today, a.state, notify=notify)
    print(summarize(state))
    for t in sent:
        print("telegram:", t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
