"""Live SHADOW test of news rules: hypothetical trades only, NO orders are ever placed (not traded).

    python news_shadow.py [--dry-run] [--asof YYYY-MM-DD]

Run nightly after the close. For a watchlist it looks for today's "news shock": |close-to-close return| >= 2 x the
trailing 60-day daily volatility AND at least one news article for that symbol between 16:00 ET the previous
session and 16:00 ET today. Each shock opens 4 hypothetical trades: momentum (follow the move) and reversal
(fade it), each held 1 and 5 trading days; entry at the next session's open, exit at the open h sessions after
entry, 0.05% cost per side. State: agent_memory/news_shadow.json. Each night it also fills entries/exits from daily
bars (Alpaca stock bars), so re-running is idempotent. Telegram: one message per new shock.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone

import news_feed
import telegram
from report import MEM, NY

PATH = MEM / "news_shadow.json"
WATCH = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO", "SPY", "QQQ"]
VOL_DAYS = 60
MIN_RETURNS = 40
MULT = 2.0
HOLDS = (1, 5)
RULES = ("momentum", "reversal")
COST = 0.0005          # per side
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
TAG = "shadow / not traded"
MINUS = "−"


# ---------- prices ----------
def fetch_bars(symbols, start, end, key=None, secret=None, urlopen=urllib.request.urlopen):
    """{symbol: [(date 'YYYY-MM-DD', open, close), ...]} daily bars (split-adjusted), oldest first. Tries SIP, then IEX."""
    key, secret = key or os.environ.get("ALPACA_API_KEY"), secret or os.environ.get("ALPACA_SECRET_KEY")
    if not (key and secret):
        raise RuntimeError("no Alpaca keys")
    last = None
    for feed in ("sip", "iex"):
        try:
            out, token = {}, None
            while True:
                q = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": str(start), "end": str(end + timedelta(days=1)),
                     "limit": 10000, "adjustment": "split", "feed": feed}
                if token:
                    q["page_token"] = token
                req = urllib.request.Request(f"{BARS_URL}?{urllib.parse.urlencode(q)}",
                                             headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
                with urlopen(req, timeout=30) as r:
                    j = json.load(r)
                for sym, rows in (j.get("bars") or {}).items():
                    out.setdefault(sym, []).extend((b["t"][:10], float(b["o"]), float(b["c"])) for b in rows)
                token = j.get("next_page_token")
                if not token:
                    return {k: sorted(set(v)) for k, v in out.items()}
        except Exception as ex:
            last = ex
    raise last


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


def new_trades(shock):
    return [{"id": trade_id(shock["symbol"], shock["date"], rule, h), "symbol": shock["symbol"], "signal_date": shock["date"],
             "rule": rule, "hold": h, "side": side_for(rule, shock["ret"]), "status": "pending",
             "entry_date": None, "entry_px": None, "exit_date": None, "exit_px": None, "pnl_pct": None}
            for rule in RULES for h in HOLDS]


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


def run(state, bars, day, news_fn):
    """One nightly pass. Records new shocks of `day` (+ their 4 hypothetical trades), then settles all trades.
    news_fn(sym, prev_day, day) -> [headline]. Returns the list of NEW shocks (for the Telegram message)."""
    new = []
    have = {s["id"] for s in state["shocks"]}
    for sym in WATCH:
        sh = detect_shock(bars.get(sym, []), day)
        if not sh:
            continue
        sid = f"{sym}|{day}"
        if sid in have:
            continue
        try:
            heads = news_fn(sym, sh["prev"], day)
        except Exception as ex:
            print(f"{sym}: news lookup failed ({type(ex).__name__}); not counted as a news shock", file=sys.stderr)
            continue
        if not heads:
            continue
        rec = {"id": sid, "symbol": sym, "date": day, "ret": round(sh["ret"], 6), "vol": round(sh["vol"], 6),
               "mult": round(sh["mult"], 3), "headlines": heads}
        state["shocks"].append(rec)
        new.append(rec)
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


def summary_line(state, day=None):
    """'Shadow news rules (since <date>, not traded): momentum 1d +0.8% avg over 4 trades, ...' or None.
    With `day`, only when some trade closed that day."""
    closed = [t for t in state["trades"] if t["status"] == "closed"]
    if not closed or (day and not any(t["exit_date"] == str(day) for t in closed)):
        return None
    parts = []
    for rule in RULES:
        for h in HOLDS:
            xs = [t["pnl_pct"] for t in closed if t["rule"] == rule and t["hold"] == h]
            if xs:
                parts.append(f"{rule} {h}d {fmt_pct(sum(xs) / len(xs))} avg over {len(xs)} trade{'' if len(xs) == 1 else 's'}")
    return f"Shadow news rules (since {state['started']}, {TAG}): " + " · ".join(parts)


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print what would be recorded; write and send nothing")
    ap.add_argument("--asof", help="treat this date as 'today' (YYYY-MM-DD)")
    a = ap.parse_args(argv)
    today = date.fromisoformat(a.asof) if a.asof else datetime.now(NY).date()
    try:
        bars = fetch_bars(WATCH, today - timedelta(days=130), today)
    except Exception as ex:
        print(f"news_shadow: price bars unavailable ({type(ex).__name__}: {str(ex)[:80]})", file=sys.stderr)
        return 0
    day = last_session(today, bars)
    if day != str(today):
        print(f"{today} has no daily bar yet/not a session; using last session {day} for detection")
    state = load_state()
    new = run(state, bars, day, shock_headlines) if day else []
    print(f"{TAG}: {len(new)} new shock(s) for {day}; trades: " +
          ", ".join(f"{k} {sum(t['status'] == k for t in state['trades'])}" for k in ("pending", "open", "closed")))
    for sh in new:
        print(shock_message(sh))
        for t in (t for t in state["trades"] if t["id"].startswith(sh["id"] + "|")):
            print(f"   {t['rule']} {t['hold']}d {t['side']}")
    line = summary_line(state)
    if line:
        print(line)
    if a.dry_run:
        return 0
    save_state(state)
    if new:  # one message per night, however many shocks (a busy day can have 7+)
        telegram.send("\n\n".join(shock_message(sh) for sh in new)[:3900])
    return 0


if __name__ == "__main__":
    sys.exit(main())
