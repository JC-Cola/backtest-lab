"""System C shadow ledger: TradingAgents' five-tier ratings -> hypothetical positions in a $25,000 shadow account.
NO real orders are ever placed. Pure functions, no network.

Rating -> target position (fraction of the shadow account's equity, per ticker; fills at the NEXT session's open):
    Buy          +20%  (long, full slot)
    Overweight   +10%  (long, half slot)
    Hold          keep the current position unchanged (flat stays flat)
    Underweight  -10%  (short, half slot)
    Sell         -20%  (short, full slot)
A ticker that drops off the watchlist gets a target of 0 (exit at the next open). Re-deciding to a different target
closes the old position and opens the new one (flip / resize / exit), each side paying 0.02% slippage + 0.01% fees.
The ledger is a deterministic replay of the decision log against daily bars, so re-running changes nothing.
"""
from __future__ import annotations

import html

START_EQUITY = 25_000.0
SLOT = 0.20
SLIPPAGE = 0.0002
FEES = 0.0001
LABEL = "System C · TradingAgents · shadow / not traded"
TARGETS = {"Buy": SLOT, "Overweight": SLOT / 2, "Hold": None, "Underweight": -SLOT / 2, "Sell": -SLOT}
MINUS = "−"


def target_for(rating):
    """Target fraction for a rating (None = hold the current position). KeyError-free: unknown -> None."""
    return TARGETS.get(str(rating).strip().title())


def decision_id(day, symbol):
    return f"{day}|{symbol}"


def make_decision(day, symbol, rating, rationale="", horizon=None, source="ta"):
    return {"id": decision_id(day, symbol), "date": str(day), "symbol": symbol, "rating": rating,
            "target": target_for(rating) if source == "ta" else 0.0, "rationale": rationale, "horizon": horizon,
            "source": source}


def _px(bars, sym, day, field):
    """Bar value on `day`; field 1 = open, 2 = close. None when the symbol has no bar that day."""
    return bars.get(sym, {}).get(day, (None, None, None))[field]


def _last_close(bars, sym, day):
    rows = bars.get(sym, {})
    ds = [d for d in rows if d <= day]
    return rows[max(ds)][2] if ds else None


def _fmt_num(x):
    return f"{x:.8f}".rstrip("0").rstrip(".") or "0"


def replay(decisions, bars, start_equity=START_EQUITY):
    """decisions: [{id,date,symbol,target,rating,rationale,...}]; bars: {sym: {date: (date, open, close)}}.
    Returns the derived ledger: equity, cash, positions, pending, trades, calendar, balance_history."""
    decs = sorted({d["id"]: d for d in decisions}.values(), key=lambda d: (d["date"], d["symbol"]))
    out = {"start_equity": start_equity, "equity": start_equity, "cash": start_equity, "positions": [], "pending": [],
           "trades": [], "calendar": [], "balance_history": {"rows": [], "realized": 0.0, "start_balance": start_equity}}
    if not decs:
        return out
    first = decs[0]["date"]
    cal = bars.get("SPY") or max(bars.values(), key=len, default={})
    days = sorted(d for d in {*cal, *(d for r in bars.values() for d in r)} if d > first)
    cash, pos, applied, prev_eq, bal, rows, trades, calendar = start_equity, {}, set(), start_equity, start_equity, [], [], []
    started = False

    def close_pos(sym, day, px, why, dec):
        nonlocal cash, bal
        p = pos.pop(sym)
        q = p["qty"]
        fill = px * (1 - SLIPPAGE) if q > 0 else px * (1 + SLIPPAGE)
        fee = FEES * abs(q) * fill
        cash += q * fill - fee
        pnl = (fill - p["entry_px"]) * q - p["fee"] - fee
        before, bal = bal, bal + pnl
        notional = abs(q) * p["entry_px"]
        trades.append({"symbol": sym, "side": "long" if q > 0 else "short", "qty": abs(q), "entry_date": p["entry_date"],
                       "entry_px": round(p["entry_px"], 4), "exit_date": day, "exit_px": round(fill, 4),
                       "pnl": round(pnl, 2), "pnl_pct": round(pnl / notional * 100, 3) if notional else 0.0,
                       "rating": p["rating"], "rationale": p["rationale"], "exit_reason": why,
                       "exit_rationale": (dec or {}).get("rationale", "")})
        rows.append({"time": f"{day} 09:30:00", "balance_before": round(before, 2), "balance_after": round(bal, 2),
                     "realized_pnl": round(pnl, 2),
                     "action": f"Close {'long' if q > 0 else 'short'} position for symbol {sym} at price {_fmt_num(round(fill, 4))} "
                               f"for {_fmt_num(abs(q))} units. Position AVG Price was {_fmt_num(round(p['entry_px'], 4))}"})

    for day in days:
        basis = prev_eq
        latest = {}
        for d in decs:   # newest decision per symbol made before today that is not applied yet
            if d["date"] < day and d["id"] not in applied:
                latest.setdefault(d["symbol"], []).append(d)
        for sym, ds in sorted(latest.items()):
            op = _px(bars, sym, day, 1)
            if op is None:
                continue   # no bar for this ticker today: the decision waits for the next session
            for d in ds:
                applied.add(d["id"])
            d = ds[-1]
            tgt = d["target"]
            if tgt is None:
                continue   # Hold: keep whatever is open
            cur = pos.get(sym)
            tier = round(tgt, 6)
            if cur and cur["tier"] == tier:
                continue
            if cur:
                close_pos(sym, day, op, "re-decided" if tgt else "exit", d)
            if tgt:
                fill = op * (1 + SLIPPAGE) if tgt > 0 else op * (1 - SLIPPAGE)
                qty = round(abs(tgt) * basis / op, 4)
                if qty <= 0:
                    continue
                qty = qty if tgt > 0 else -qty
                fee = FEES * abs(qty) * fill
                cash -= qty * fill + fee
                pos[sym] = {"qty": qty, "entry_px": fill, "entry_date": day, "fee": fee, "tier": tier,
                            "rating": d["rating"], "rationale": d["rationale"]}
                started = True
        value = sum(p["qty"] * (_px(bars, s, day, 2) or _last_close(bars, s, day) or p["entry_px"]) for s, p in pos.items())
        eq = cash + value
        if started:
            calendar.append({"date": day, "pl": round(eq - prev_eq, 2), "pct": round((eq - prev_eq) / prev_eq * 100, 3),
                             "equity": round(eq, 2)})
        prev_eq = eq
    last = days[-1] if days else first
    plist = []
    for s, p in sorted(pos.items()):
        px = _last_close(bars, s, last) or p["entry_px"]
        pl = (px - p["entry_px"]) * p["qty"]
        plist.append({"symbol": s, "side": "long" if p["qty"] > 0 else "short", "qty": p["qty"],
                      "avg_price": round(p["entry_px"], 4), "price": round(px, 4), "value": round(p["qty"] * px, 2),
                      "pl": round(pl, 2), "pl_pct": round(pl / (abs(p["qty"]) * p["entry_px"]), 5), "entry_date": p["entry_date"],
                      "rating": p["rating"], "rationale": p["rationale"]})
    pend = [{"symbol": d["symbol"], "signal_date": d["date"], "rating": d["rating"], "target": d["target"]}
            for d in decs if d["id"] not in applied and d["target"] is not None]
    unreal = round(sum(p["pl"] for p in plist), 2)
    out.update(equity=round(prev_eq, 2), cash=round(cash, 2), positions=plist, pending=pend, trades=trades,
               calendar=calendar, balance_history={"rows": rows[::-1], "realized": round(bal - start_equity, 2),
                                                   "unrealized": unreal, "equity": round(prev_eq, 2), "start_balance": start_equity})
    return out


# ---------- messages ----------
def one_line(text, n=160):
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def stance(rating):
    t = target_for(rating)
    if t is None:
        return "keep position"
    return f"{'long' if t > 0 else 'short'} {abs(t) * 100:.0f}%"


def nightly_message(day, results, equity=None, total_pl_pct=None):
    """results: [{symbol, rating|None, rationale, horizon, note}]. One header line + one rationale line per ticker."""
    heads = [f"{r['symbol']} {str(r['rating']).upper()}" if r.get("rating") else f"{r['symbol']} no decision" for r in results]
    head = f"🅲 TradingAgents {day} · " + " · ".join(heads)
    if equity is not None:
        head += f" · shadow ${equity:,.0f}" + (f" ({total_pl_pct:+.1f}%)".replace("-", MINUS) if total_pl_pct is not None else "")
    lines = [html.escape(head)]
    for r in results:
        if r.get("rating"):
            hz = f" · horizon {r['horizon']}" if r.get("horizon") else ""
            lines.append(html.escape(f"{r['symbol']} {r['rating']} → {stance(r['rating'])}{hz}: {one_line(r.get('rationale'))}"))
        else:
            lines.append(html.escape(f"{r['symbol']}: no decision ({r.get('note') or 'failed'})"))
    lines.append("shadow / not traded")
    return "\n".join(lines)


# ---------- TradingAgents' decision text, results statistics ----------
def parse_decision(text):
    """(executive summary's first paragraph, time horizon) from the Portfolio Manager's markdown, whichever way the model
    wrote the headings ('**Executive Summary**: ...' or '### Executive Summary'); '' / None when absent."""
    import re
    t = str(text or "")
    m = re.search(r"(?:^|\n)[ \t]*(?:#+[ \t]*|\*\*)Executive Summary\**:?[ \t]*\n?(.*)", t, re.S | re.I)
    paras = [p.strip() for p in (m.group(1) if m else "").split("\n\n") if p.strip()]
    summary = " ".join(paras[0].split()) if paras else ""
    summary = re.sub(r"^\*\*:?\s*", "", summary)
    h = re.search(r"Time Horizon\**:?[ \t]*\n?[ \t]*([^\n]+)", t, re.I)
    horizon = h.group(1).strip(" *") if h else None
    return summary, (None if horizon in (None, "", "not provided") else horizon)


def stats(ledger):
    """Total return %, max drawdown %, annualised Sharpe (daily, from the calendar), win rate of closed trades."""
    start = ledger["start_equity"]
    eq = [start] + [c["equity"] for c in ledger["calendar"]]
    rets = [c["pct"] / 100 for c in ledger["calendar"]]
    peak, dd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        dd = min(dd, e / peak - 1)
    sharpe = None
    if len(rets) > 2:
        m = sum(rets) / len(rets)
        sd = (sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
        sharpe = round(m / sd * 252 ** 0.5, 3) if sd > 0 else None
    tr = ledger["trades"]
    return {"total_pct": round((ledger["equity"] / start - 1) * 100, 3), "max_dd_pct": round(dd * 100, 3), "sharpe": sharpe,
            "trades": len(tr), "win_rate": round(sum(t["pnl"] > 0 for t in tr) / len(tr), 3) if tr else None,
            "days": len(rets), "equity": ledger["equity"]}
