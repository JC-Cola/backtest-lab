"""Live SHADOW test of news rules: hypothetical trades only, NO orders are ever placed (not traded).

    python news_shadow.py [--dry-run] [--asof YYYY-MM-DD] [--no-llm] [--max-reviews N] [--report]

Run nightly after the close over the whole universe (S&P 500 + Nasdaq-100, ~500 liquid shortable stocks; universe.py).
A "news shock" = |close-to-close return| >= 2 x the trailing 60-day daily volatility AND at least one news article for
the symbol between 16:00 ET the previous session and 16:00 ET today (market-wide news fetched once, mapped to symbols).
Each shock records day-trader-skill style extras (relative volume, gap, SPY move, beta, abnormal move); shocks are
ranked by |abnormal move| x relative volume and only the top CAP per night are kept. Each kept shock gets a
TradingAgents-style LLM review (news_review.py). Each shock opens hypothetical trades: momentum (follow the move) and
reversal (fade it), plus an "llm" rule that follows the risk manager (continue = momentum side, reverse = reversal side,
unclear = no trade), each held 1 and 5 trading days; entry at the next session's open, exit at the open h sessions after
entry, 0.05% cost per side. State: agent_memory/news_shadow.json. Each night it also fills entries/exits from daily
bars, so re-running is idempotent. Telegram: one short message per night.
"""
from __future__ import annotations

import argparse
import html
import json
import statistics
import sys
import time as _time
from datetime import date, datetime, time, timedelta, timezone

import news_feed
import news_scan
import telegram
from report import MEM, NY

PATH = MEM / "news_shadow.json"
MARKET = news_scan.MARKET   # SPY: used for the market move and beta, never scanned as a shock itself
CAP = 15                    # keep at most this many shocks per night (best by |abnormal move| x relative volume)
MIN_PRICE = 5.0             # day-trader-skill's penny-stock floor
LLM = "llm"
VOL_DAYS = 60
MIN_RETURNS = 40
MULT = 2.0
HOLDS = (1, 5)
RULES = ("momentum", "reversal")
ALL_RULES = RULES + (LLM,)
COST = 0.0005          # per side
TAG = "shadow / not traded"
MINUS = "−"


# ---------- prices ----------
fetch_bars = news_scan.fetch_bars   # batched multi-symbol daily bars: {sym: [(date, open, close, volume)]}


# ---------- detection ----------
def detect_shock(bars, day, vol_days=VOL_DAYS, mult=MULT):
    """bars: [(date, open, close)] oldest first. Shock on `day` -> {'ret','vol','mult','prev'} else None.
    vol = sample std of the `vol_days` daily returns BEFORE `day`."""
    dates = [b[0] for b in bars]
    if day not in dates:
        return None
    i = dates.index(day)
    closes = [b[2] for b in bars]
    rets = [closes[k] / closes[k - 1] - 1 for k in range(1, i + 1)]   # rets[-1] is day's own return
    hist = rets[:-1][-vol_days:]
    if i < 1 or len(hist) < MIN_RETURNS:
        return None
    vol = statistics.stdev(hist)
    if vol <= 0:
        return None
    r = rets[-1]
    if abs(r) < mult * vol:
        return None
    return {"ret": r, "vol": vol, "mult": abs(r) / vol, "prev": dates[i - 1]}


def news_window(prev_day, day):
    """16:00 ET of the previous session -> 16:00 ET of `day`, as UTC datetimes."""
    def at(d):
        return datetime.combine(date.fromisoformat(d), time(16, 0), NY).astimezone(timezone.utc)
    return at(prev_day), at(day)


def shock_headlines(sym, prev_day, day, fetcher=news_feed.fetch, n=3):
    """Headlines for `sym` inside the news window (most specific first, then newest); [] when none."""
    start, end = news_window(prev_day, day)
    arts = fetcher([sym], start, end=end)
    ok = []
    for a in arts:
        try:
            t = datetime.fromisoformat(str(a.get("created_at", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start <= t <= end and str(a.get("headline", "")).strip():
            ok.append((len(a.get("symbols") or []), -t.timestamp(), " ".join(a["headline"].split())))
    ok.sort()
    out = []
    for _, _, h in ok:
        if h not in out:
            out.append(h)
    return out[:n]


# ---------- trades ----------
def side_for(rule, ret):
    up = ret > 0
    return "long" if (up if rule == "momentum" else not up) else "short"


def trade_id(sym, day, rule, hold):
    return f"{sym}|{day}|{rule}|{hold}d"


def llm_rule(shock):
    """The side the LLM risk manager would take: 'momentum' (continue), 'reversal' (reverse) or None (unclear / no review)."""
    r = shock.get("review") or {}
    return {"continue": "momentum", "reverse": "reversal"}.get(r.get("direction")) if r.get("status") == "ok" else None


def new_trades(shock):
    def mk(rule, h, side):
        return {"id": trade_id(shock["symbol"], shock["date"], rule, h), "symbol": shock["symbol"], "signal_date": shock["date"],
                "rule": rule, "hold": h, "side": side, "status": "pending",
                "entry_date": None, "entry_px": None, "exit_date": None, "exit_px": None, "pnl_pct": None}
    out = [mk(rule, h, side_for(rule, shock["ret"])) for rule in RULES for h in HOLDS]
    follow = llm_rule(shock)
    if follow:
        out += [mk(LLM, h, side_for(follow, shock["ret"])) for h in HOLDS]
    return out


def pnl_pct(side, entry, exit_, cost=COST):
    """Percent P&L after `cost` per side. Short = -(move)."""
    move = exit_ / entry - 1
    return ((move if side == "long" else -move) - 2 * cost) * 100


def settle(trade, bars):
    """Fill entry (open of the first session after the signal) and exit (open `hold` sessions after entry) from bars.
    Pure function of the bars, so re-running changes nothing. Mutates and returns the trade."""
    rows = bars.get(trade["symbol"], [])
    dates = [r[0] for r in rows]
    after = [k for k, d in enumerate(dates) if d > trade["signal_date"]]
    if not after:
        return trade
    e = after[0]
    trade.update(status="open", entry_date=dates[e], entry_px=round(rows[e][1], 4))
    if e + trade["hold"] < len(rows):
        x = e + trade["hold"]
        trade.update(status="closed", exit_date=dates[x], exit_px=round(rows[x][1], 4),
                     pnl_pct=round(pnl_pct(trade["side"], rows[e][1], rows[x][1]), 4))
    return trade


def load_state(path=PATH):
    try:
        s = json.loads(open(path).read())
        if isinstance(s, dict) and "trades" in s and "shocks" in s:
            return s
    except (OSError, ValueError):
        pass
    return {"label": TAG, "started": None, "shocks": [], "trades": []}


def save_state(state, path=PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")


def candidates(bars, day, news_fn, symbols):
    """Every shock of `day` over `symbols` that also has news, as full records (with extras), best first.
    news_fn(sym, prev_day, day) -> [headline]; a lookup that raises skips that symbol."""
    mkt = bars.get(MARKET)
    out = []
    for sym in symbols:
        rows = bars.get(sym, [])
        sh = detect_shock(rows, day)
        if not sh or rows[[r[0] for r in rows].index(day)][2] < MIN_PRICE:
            continue
        try:
            heads = news_fn(sym, sh["prev"], day)
        except Exception as ex:
            print(f"{sym}: news lookup failed ({type(ex).__name__}); not counted as a news shock", file=sys.stderr)
            continue
        if not heads:
            continue
        rec = {"id": f"{sym}|{day}", "symbol": sym, "date": day, "ret": round(sh["ret"], 6), "vol": round(sh["vol"], 6),
               "mult": round(sh["mult"], 3), "headlines": heads, **news_scan.features(rows, day, sh["ret"], mkt)}
        rec["abn_mult"] = round(abs(rec["abn"]) / sh["vol"], 2)
        rec["score"] = round(news_scan.rank_score(rec), 5)
        out.append(rec)
    out.sort(key=lambda r: (-r["score"], r["symbol"]))
    return out


def run(state, bars, day, news_fn, review_fn=None, symbols=None, cap=CAP):
    """One nightly pass. Records new shocks of `day` (top `cap` by score; shocks already recorded for the day count
    against the cap, so re-runs add nothing), reviews them, opens their hypothetical trades, then settles all trades.
    news_fn(sym, prev_day, day) -> [headline], or None to skip detection (settle only).
    review_fn(new_records_best_first) -> {id: review dict} (optional). Returns the list of NEW shocks, best first."""
    new = []
    have = {s["id"] for s in state["shocks"]}
    if news_fn is not None:
        symbols = symbols or [s for s in bars if s != MARKET]
        room = cap - sum(s["date"] == day for s in state["shocks"])
        new = [r for r in candidates(bars, day, news_fn, symbols) if r["id"] not in have][:max(0, room)]
        if new and review_fn:
            reviews = review_fn(new) or {}
            for r in new:
                r["review"] = reviews.get(r["id"]) or {"status": "no review"}
        state["shocks"].extend(new)
        if new:
            state["started"] = state["started"] or day
    ids = {t["id"] for t in state["trades"]}
    for sh in state["shocks"]:   # also heals a shock whose trades were lost mid-run
        for t in new_trades(sh):
            if t["id"] not in ids:
                state["trades"].append(t)
                ids.add(t["id"])
    for t in state["trades"]:
        if t["status"] != "closed":
            settle(t, bars)
    return new


# ---------- messages ----------
def fmt_pct(x, d=1):
    return f"{x:+.{d}f}%".replace("-", MINUS)


def shock_message(sh):
    rules = {}
    for r in RULES:
        rules[r] = side_for(r, sh["ret"])
    head = sh["headlines"][0]
    if len(head) > 140:
        head = head[:139].rstrip() + "…"
    return (f"📰 News shock · {sh['symbol']} {fmt_pct(sh['ret'] * 100)} ({sh['mult']:.1f}× normal) — '{head}' · "
            f"{TAG}: momentum ({'buy' if rules['momentum'] == 'long' else 'short'}) & reversal "
            f"({'buy' if rules['reversal'] == 'long' else 'short'}), 1 and 5 days")


def shock_line(sh):
    """One short Telegram line: 'NVDA −6.2% (abn −5.1%) · 3.4× vol · earnings · LLM reverse 0.70'."""
    parts = [f"{sh['symbol']} {fmt_pct(sh['ret'] * 100)}"]
    if sh.get("abn") is not None and sh.get("spy_ret") is not None:
        parts[0] += f" (abn {fmt_pct(sh['abn'] * 100)})"
    if sh.get("relvol"):
        parts.append(f"{sh['relvol']:.1f}× vol")
    r = sh.get("review") or {}
    if r.get("status") == "ok":
        parts += [r["catalyst"].replace("_", " "), f"LLM {r['direction']} {r['confidence']:.2f}"]
    elif "review" in sh:
        parts.append("no review")
    return html.escape(" · ".join(parts))


def nightly_message(new, day, n_universe=None, max_lines=8):
    """The one message per night: a header and the top shocks (<= max_lines lines)."""
    top = new[:max_lines]
    head = f"📰 News shocks {day} · {len(new)} kept" + (f" of ~{n_universe} stocks" if n_universe else "") + f" · {TAG}"
    more = [f"+{len(new) - len(top)} more"] if len(new) > len(top) else []
    return "\n".join([head] + [shock_line(s) for s in top] + more)


def _avg(xs):
    return sum(xs) / len(xs)


def summary_line(state, day=None):
    """'Shadow news rules (since <date>, not traded): momentum 1d +0.8% avg over 4 trades, ...' or None.
    With `day`, only when some trade closed that day."""
    closed = [t for t in state["trades"] if t["status"] == "closed"]
    if not closed or (day and not any(t["exit_date"] == str(day) for t in closed)):
        return None
    parts = []
    for rule in ALL_RULES:
        for h in HOLDS:
            xs = [t["pnl_pct"] for t in closed if t["rule"] == rule and t["hold"] == h]
            if xs:
                parts.append(f"{rule} {h}d {fmt_pct(_avg(xs))} avg over {len(xs)} trade{'' if len(xs) == 1 else 's'}")
    return f"Shadow news rules (since {state['started']}, {TAG}): " + " · ".join(parts)


def conf_bucket(review):
    if not review or review.get("status") != "ok":
        return "no review"
    c = review["confidence"]
    return "conf <0.5" if c < 0.5 else "conf 0.5-0.7" if c < 0.7 else "conf >=0.7"


def breakdowns(state):
    """Closed-trade results grouped three ways, each {group: {'rule Nd': (n, avg pnl %)}}:
    'rule' (one group, all shocks), 'catalyst' (LLM-classified type) and 'confidence' (LLM confidence bucket of the
    shock; the plain rules are listed next to the llm rule for the same shocks, so the comparison is like for like)."""
    shocks = {s["id"]: s for s in state["shocks"]}
    groups = {"rule": {}, "catalyst": {}, "confidence": {}}
    for t in state["trades"]:
        if t["status"] != "closed":
            continue
        sh = shocks.get(f"{t['symbol']}|{t['signal_date']}", {})
        rv = sh.get("review") or {}
        label = f"{t['rule']} {t['hold']}d"
        keys = [("rule", "all shocks")]
        if rv.get("status") == "ok":
            keys.append(("catalyst", rv["catalyst"]))
        keys.append(("confidence", conf_bucket(rv)))
        for kind, g in keys:
            groups[kind].setdefault(g, {}).setdefault(label, []).append(t["pnl_pct"])
    return {k: {g: {lab: (len(v), _avg(v)) for lab, v in sorted(d.items())} for g, d in sorted(gs.items())}
            for k, gs in groups.items()}


def breakdown_text(state):
    b = breakdowns(state)
    if not any(b.values()):
        return "No closed shadow trades yet."
    L = []
    for kind in ("rule", "catalyst", "confidence"):
        L.append(f"By {kind}:")
        for g, d in b[kind].items():
            L.append(f"  {g}: " + " · ".join(f"{lab} {fmt_pct(avg)} (n={n})" for lab, (n, avg) in d.items()))
    return "\n".join(L)


def evening_line(day=None, path=PATH):
    """Line for the evening briefing; None when nothing closed today or the file is missing. Never raises."""
    try:
        return summary_line(load_state(path), day)
    except Exception:
        return None


# ---------- CLI ----------
def last_session(today, bars):
    ds = sorted({b[0] for rows in bars.values() for b in rows if b[0] <= str(today)})
    return ds[-1] if ds else None


def main(argv=None):
    import news_review
    import universe
    from agent import claude_cli
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print what would be recorded; write and send nothing")
    ap.add_argument("--asof", help="treat this date as 'today' (YYYY-MM-DD)")
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM review")
    ap.add_argument("--max-reviews", type=int, default=news_review.MAX_CALLS, help="cap on Claude calls tonight")
    ap.add_argument("--report", action="store_true", help="print the aggregate results by rule / catalyst / confidence and exit")
    a = ap.parse_args(argv)
    if a.report:
        print(breakdown_text(load_state()))
        return 0
    now = datetime.now(NY)
    today = date.fromisoformat(a.asof) if a.asof else now.date()
    if not a.asof and now.time() < time(16, 15):
        today -= timedelta(days=1)   # today's session is not finished: never score a partial bar
    t0 = _time.monotonic()
    syms, usrc = universe.load(today, refresh=True, write=not a.dry_run)
    print(f"universe: {len(syms)} symbols ({usrc})")
    try:
        bars = fetch_bars(sorted({*syms, MARKET}), today - timedelta(days=130), today)
    except Exception as ex:
        print(f"news_shadow: price bars unavailable ({type(ex).__name__}: {str(ex)[:80]})", file=sys.stderr)
        return 0
    t1 = _time.monotonic()
    day = last_session(today, bars)
    if day != str(today):
        print(f"{today} has no daily bar yet/not a session; using last session {day} for detection")
    state = load_state()
    news_fn, nart = None, 0
    if day:
        dates = sorted({b[0] for b in bars.get(MARKET, []) if b[0] < day})
        if dates:
            start, end = news_window(dates[-1], day)
            try:
                arts = news_scan.fetch_news_window(start, end)
                nmap = news_scan.news_by_symbol(arts, syms, start, end)
                nart, news_fn = len(arts), (lambda sym, prev, d: nmap.get(sym, []))
            except Exception as ex:
                print(f"news_shadow: market news unavailable ({type(ex).__name__}: {str(ex)[:80]}); no new shocks tonight", file=sys.stderr)
    t2 = _time.monotonic()
    review_fn = None if a.no_llm else (lambda recs: news_review.review_all(recs, max_calls=a.max_reviews))
    new = run(state, bars, day, news_fn, review_fn, syms) if day else []
    t3 = _time.monotonic()
    print(f"timing: bars {t1 - t0:.0f}s ({len(bars)} symbols), news {t2 - t1:.0f}s ({nart} articles), detect+review {t3 - t2:.0f}s"
          + ("" if a.no_llm else f"; Claude reported cost ${claude_cli.spent():.2f}"))
    print(f"{TAG}: {len(new)} new shock(s) for {day}; trades: " +
          ", ".join(f"{k} {sum(t['status'] == k for t in state['trades'])}" for k in ("pending", "open", "closed")))
    for sh in new:
        print(shock_line(sh), "|", sh["headlines"][0][:90])
        r = sh.get("review") or {}
        if r.get("status") == "ok":
            print(f"     bull: {r['bull']}\n     bear: {r['bear']}\n     risk mgr: {r['direction']} ({r['confidence']}) {r['rationale']}")
        for t in (t for t in state["trades"] if t["id"].startswith(sh["id"] + "|")):
            print(f"   {t['rule']} {t['hold']}d {t['side']}")
    line = summary_line(state)
    if line:
        print(line)
    if a.dry_run:
        return 0
    save_state(state)
    if new:  # one message per night, however many shocks
        telegram.send(nightly_message(new, day, len(syms)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
