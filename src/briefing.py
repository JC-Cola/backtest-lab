"""Two short Telegram briefings sent from the GitHub Actions jobs (the Mac may be off).

    python src/briefing.py morning|evening|balance [--dry-run] [--text] [--force]

morning: ~09:15 New York, before the open. Both paper accounts, open positions, orders waiting for the open, risk
         flags (distance to the agreed limits), new candidates. Two crons (EDT and EST) trigger it; the script only
         sends between 09:00 and 09:40 New York on a trading day, so exactly one of them does. --force skips that.
balance: just the balance-history picture (also sent as a second photo right after the evening card).
evening: after the nightly run. Today's trades, best/worst trade, day P/L, "Matches the backtest?" (live N-day
         return against the backtest's rolling N-day returns on the test years) and what the agents tested.
By default each briefing is ONE picture card (briefing_cards.py) plus a 2-3 line caption; the text message is the
fallback when rendering fails, or with --text. --dry-run writes the PNG to DRY_DIR and prints the caption (or the
text) instead of sending. morning_data()/evening_data() turn plain inputs into one plain dict that both the text
builders and the card renderer use, so they are testable; gather_*() fetch the inputs. All times are New York time.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import numpy as np

import report as R
import telegram
from agent import proposer_health
from briefing_inputs import intraday_backtest, longterm_backtest, research_line, research_today, safe_bt
from report import ACCOUNTS, MEM, NY, ROOT, START

LABEL = {"long-term": "Long-term", "intraday": "Intraday"}
KILL = {"long-term": 0.20, "intraday": 0.12}   # agreed kill switches: drawdown from the account's peak
DAILY_LIMIT = {"intraday": 0.03}               # agreed daily loss limit (intraday account only)
FTMO_DAILY, FTMO_TOTAL = 0.05, 0.10            # FTMO reference limits
GATE = (time(9, 0), time(9, 40))
MIN_DAYS = 5                                   # fewer live days than this: too early to tell
DRY_DIR = Path("/private/tmp/claude-503/-Users-jccol-Desktop-Claude/e2fd9bd8-a8fa-4dda-8d04-be93a90edfa5/scratchpad")
MAX_TRADES = 6                                 # trade lines shown per account before "+k more"
e = html.escape


def b(s):
    return f"<b>{e(str(s))}</b>"


def qty(x):
    return f"{x:,.4f}".rstrip("0").rstrip(".")


# ---------- Alpaca extras: history, calendar ----------
def _client(kenv, senv):
    from alpaca.trading.client import TradingClient
    key, secret = os.environ.get(kenv), os.environ.get(senv)
    return TradingClient(key, secret, paper=True) if key and secret else None


def get_history(kenv, senv):
    """Non-zero daily equity points (oldest first) of the account's portfolio history; [] on any failure.
    Alpaca's date labels are ambiguous (the newest point was the previous close, not the live value, when checked),
    so only the order of the points is used."""
    try:
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        c = _client(kenv, senv)
        h = c.get_portfolio_history(GetPortfolioHistoryRequest(period="1A", timeframe="1D"))
        return [float(q) for q in h.equity if q]
    except Exception:
        return []


def trading_days(start, end):
    """Trading dates in [start, end] from the Alpaca calendar (long-term keys); None when unavailable."""
    try:
        from alpaca.trading.requests import GetCalendarRequest
        c = _client("ALPACA_API_KEY", "ALPACA_SECRET_KEY")
        return [d.date for d in c.get_calendar(GetCalendarRequest(start=start, end=end))]
    except Exception:
        return None


def is_trading_day(day):
    """Calendar answer, or weekdays only when Alpaca is not reachable."""
    cal = trading_days(day, day)
    return day.weekday() < 5 if cal is None else day in cal


def previous_trading_day(day):
    cal = trading_days(day - timedelta(days=10), day - timedelta(days=1))
    if cal:
        return max(cal)
    d = day - timedelta(days=1)
    while d.weekday() > 4:
        d -= timedelta(days=1)
    return d


def morning_window(now):
    """True between 09:00 and 09:40 New York (the two crons: only one of them lands here)."""
    t = now.astimezone(NY).time()
    return GATE[0] <= t <= GATE[1]


# ---------- pure helpers ----------
def last_session(hist, last_equity):
    """(change $, change fraction) of the last completed session: Alpaca's last_equity is that session's close,
    the base is the close before it (the newest history point is dropped when it is that same close)."""
    if not last_equity:
        return None
    pts = list(hist)
    if pts and abs(pts[-1] - last_equity) < 0.01:
        pts.pop()
    base = pts[-1] if pts else START
    return last_equity - base, last_equity / base - 1


def drawdown(hist, equity):
    """Fraction below the account's peak (<= 0); the $25,000 start counts as a peak."""
    peak = max([START, equity] + list(hist))
    return equity / peak - 1


def room(limit, loss):
    """Percentage points left before `limit` (positive numbers) given today's/total return `loss` (a fraction)."""
    return max(0.0, limit - max(0.0, -loss)) * 100


def risk_line(name, equity, hist, last_equity):
    if equity is None:
        return f"{LABEL[name]}: no numbers"
    dd = drawdown(hist, equity)
    s = last_session(hist, last_equity)
    parts = [f"DD {dd * 100:.1f}% from peak, {room(KILL[name], dd):.1f} pts to kill (-{KILL[name] * 100:.0f}%)"]
    if name in DAILY_LIMIT and s:
        parts.append(f"last day {s[1] * 100:+.1f}%, {room(DAILY_LIMIT[name], s[1]):.1f} pts to -{DAILY_LIMIT[name] * 100:.0f}%")
    ft = f"{room(FTMO_TOTAL, equity / START - 1):.1f}"
    ft = f"{room(FTMO_DAILY, s[1]):.1f} / {ft}" if s else f"n/a / {ft}"
    parts.append(f"FTMO ref (5% day / 10% total): {ft} pts left")
    return f"{LABEL[name]}: " + "; ".join(parts)


def missed_checks(day, noise_rows):
    """Plain strings for the intraday checks that were not logged on `day` (reuses report.problems)."""
    out, by_sym = [], {}
    for p in R.problems(day, {}, noise_rows):
        m = re.search(r"no (\d+):00 check logged for (\w+)", p)
        if m:
            by_sym.setdefault(m.group(2), []).append(f"{int(m.group(1))}:00")
        elif "no log rows" in p:
            out.append("no intraday log rows at all")
    out += [f"{s} {' '.join(h)}" for s, h in sorted(by_sym.items())]
    return out


def waiting_orders(acct):
    return [f"{o['side'].upper()} {qty(o['qty'])} {o['symbol']}" for o in acct.get("orders", [])
            if o.get("status") in R.OPEN_STATUSES]


def pos_text(acct):
    pos = acct.get("positions", [])
    return ", ".join(f"{p['symbol']} {qty(p['qty'])} ({R.usd(p['pl'], True)})" for p in pos) or "none"


def acct_head(name, d, chg_label, chg):
    if d.get("equity") is None:
        return f"{b(LABEL[name])}: no numbers ({e(d.get('note', ''))})"
    ds = "n/a" if chg is None else f"{R.usd(chg[0], True)} ({R.pct(chg[1])})"
    return f"{b(LABEL[name])} {R.usd(d['equity'])} | {chg_label} {ds} | since start {R.pct(d['since'])}"


def waiting_candidates(cands):
    return [c for c in (cands or {}).get("candidates", []) if c.get("status") == "awaiting approval"]


# ---------- morning ----------
def morning_flags(it, accts, prev_day, noise_rows, prev_report_exists):
    flags = [f"Intraday still holds {qty(p['qty'])} {p['symbol']} (should be flat overnight)" for p in it.get("positions", [])]
    if prev_day:
        mc = missed_checks(prev_day, noise_rows)
        if mc:
            flags.append(f"Missed intraday checks on {prev_day}: " + "; ".join(mc))
        if not prev_report_exists:
            flags.append(f"No nightly report found for {prev_day} (nightly run may have failed)")
    return flags + [f"{LABEL[n]}: {accts[n]['note'][:90]}" for n in ("long-term", "intraday") if accts[n].get("note")]


def morning_data(today, accts, hists, noise_rows, cands, prev_day=None, prev_report_exists=True):
    """Everything the morning text and card show, as plain data."""
    out = {}
    for name in ("long-term", "intraday"):
        a, h = accts[name], hists.get(name, [])
        eq = a.get("equity")
        s = last_session(h, a.get("last_equity"))
        out[name] = {"equity": eq, "note": a.get("note", ""), "last": s, "positions": a.get("positions", []),
                     "since": None if eq is None else (eq - START) / START, "waiting": waiting_orders(a) if name == "long-term" else [],
                     "dd": None if eq is None else drawdown(h, eq), "kill": KILL[name],
                     "day_limit": DAILY_LIMIT.get(name), "risk": risk_line(name, eq, h, a.get("last_equity"))}
    flags = morning_flags(accts["intraday"], accts, prev_day, noise_rows, prev_report_exists)
    return {"kind": "morning", "day": today, "accounts": out, "flags": flags, "waiting_candidates": waiting_candidates(cands)}


def text_morning(d):
    L = [f"{b('Morning ' + str(d['day']))} (before the open)"]
    for name in ("long-term", "intraday"):
        a = d["accounts"][name]
        L.append(acct_head(name, a, "last session", a["last"]))
        if a["equity"] is not None:
            L.append(f"  Positions: {e(pos_text(a))}")
        if name == "long-term":
            L.append("  Waiting for the open: " + (e(", ".join(a["waiting"])) if a["waiting"] else "nothing"))
    L += ["", b("Risk")] + ["  " + e(d["accounts"][n]["risk"]) for n in ("long-term", "intraday")]
    L += [f"  ⚠️ {e(f)}" for f in d["flags"]] or ["  No flags."]
    wc = d["waiting_candidates"]
    L += ["", b("Candidates") + (": none awaiting approval" if not wc else f": {len(wc)} awaiting approval")]
    L += [f"  {e(c['account'])}: {e(c['description'][:80])}" for c in wc[:3]]
    return "\n".join(L)


def build_morning(*args, **kw):
    return text_morning(morning_data(*args, **kw))


# ---------- evening: trades, best/worst ----------
def round_trips(fills):
    """[(symbol, realised P/L)] for each position that went back to flat, by average cost (handles shorts)."""
    return [(s, v) for s, v, _ in round_trips_at(fills)]


def round_trips_at(fills):
    """Like round_trips, as [(symbol, P/L, time of the closing fill)]."""
    pos, cost, pl, out = {}, {}, {}, []
    for o in fills:
        if o.get("price") is None:
            continue
        s, px = o["symbol"], o["price"]
        q = o["filled_qty"] * (1 if o["side"] == "buy" else -1)
        p = pos.get(s, 0.0)
        if p == 0 or (p > 0) == (q > 0):
            cost[s] = (cost.get(s, 0.0) * abs(p) + px * abs(q)) / (abs(p) + abs(q))
            pos[s] = p + q
            continue
        close = min(abs(q), abs(p))
        pl[s] = pl.get(s, 0.0) + (px - cost[s]) * close * (1 if p > 0 else -1)
        pos[s] = p + q
        if abs(pos[s]) < 1e-9:
            pos[s] = 0.0
            out.append((s, pl.pop(s), o.get("filled")))
        elif (p > 0) != (pos[s] > 0):        # flipped through zero
            out.append((s, pl.pop(s), o.get("filled")))
            cost[s] = px
    return out


def best_worst(items):
    """'Best X +$1 | Worst Y -$2' from [(label, pl)] (None when there is nothing to rank)."""
    items = [(s, v) for s, v in items if v is not None]
    if not items:
        return None
    hi, lo = max(items, key=lambda x: x[1]), min(items, key=lambda x: x[1])
    return f"Best {hi[0]} {R.usd(hi[1], True)}" + ("" if hi is lo else f" | Worst {lo[0]} {R.usd(lo[1], True)}")


def short_why(o, name, order_rows, day_noise, ins):
    if name == "intraday":
        return R.intraday_why(o, day_noise).split(":")[0]
    w = R.longterm_why(o, order_rows, ins)
    m = re.search(r"strategies in: (.*)", w)
    return m.group(1)[:30] if m else "no strategy in"


def trade_data(name, a, day, order_rows, day_noise, ins):
    """{'trades': every fill of the day, 'trips': closed round trips (intraday), 'best_worst': text or None}."""
    fl = R.filled_on(a.get("orders", []), day)
    trades = [{"t": o["filled"], "time": R.hhmm(o["filled"]), "side": o["side"], "qty": o["filled_qty"], "symbol": o["symbol"],
               "price": o["price"], "why": short_why(o, name, order_rows, day_noise, ins)} for o in fl]
    trips = round_trips_at(fl) if name == "intraday" else []
    if name == "intraday":
        bw = best_worst([(s, v) for s, v, _ in trips])
    else:
        bw = best_worst([(p["symbol"], p.get("day_pl")) for p in a.get("positions", [])])
    return {"trades": trades, "trips": trips, "best_worst": bw}


def trade_lines(d):
    lines = [f"  {t['time']} {t['side'].upper()} {qty(t['qty'])} {t['symbol']} @ {R.usd(t['price'])} ({e(t['why'])})"
             for t in d["trades"][:MAX_TRADES]]
    if len(d["trades"]) > MAX_TRADES:
        lines.append(f"  +{len(d['trades']) - MAX_TRADES} more")
    if not d["trades"]:
        lines.append("  no trades")
    if d["best_worst"]:
        lines.append("  " + e(d["best_worst"]))
    return lines


# ---------- evening: backtest comparison ----------
def live_path(hist, equity):
    """Cumulative return since the $25,000 start after each live day (history closes plus the current equity);
    leading days still at exactly $25,000 are not counted."""
    pts = list(hist)
    if equity is not None and not (pts and abs(pts[-1] - equity) < 0.005):
        pts.append(equity)
    while pts and abs(pts[0] - START) < 1e-6:
        pts.pop(0)
    return [q / START - 1 for q in pts]


def live_stats(hist, equity):
    """(days, return since the $25,000 start)."""
    path = live_path(hist, equity)
    return (len(path), path[-1]) if path else (0, 0.0)


def window_returns(r, n):
    """Compounded returns of every run of n consecutive days in the daily-return array r."""
    r = np.asarray(r, float)
    if n < 1 or n > len(r):
        return np.array([])
    c = np.r_[1.0, np.cumprod(1 + r)]
    return c[n:] / c[:-n] - 1


def fan(bt, n):
    """Backtest 5th/50th/95th percentile of the cumulative return after k days, k = 0..H, for the fan chart.
    None when the backtest is missing or too short."""
    if bt is None or len(bt) < 40:
        return None
    h = min(max(n + 3, 20), len(bt) - 19)
    rows = [np.percentile(window_returns(bt, k), [5, 50, 95]) for k in range(1, h + 1)]
    cols = np.vstack([np.zeros(3), rows]).T
    return {"days": list(range(h + 1)), "lo": cols[0].tolist(), "med": cols[1].tolist(), "hi": cols[2].tolist()}


def verdict(n, ret, bt):
    """'early', 'unavailable', 'in line', 'below normal' or 'above normal' (same rule as backtest_line)."""
    if n < MIN_DAYS:
        return "early"
    w = window_returns(bt, n) if bt is not None else []
    if len(w) < 20:
        return "unavailable"
    p = float((w <= ret).mean() * 100)
    return "below normal" if p < 5 else "above normal" if p > 95 else "in line"


def backtest_line(name, n, ret, bt):
    """One line: live N-day return vs the backtest's N-day windows, with the verdict."""
    lab = LABEL[name]
    if n < MIN_DAYS:
        return f"{lab}: too early to tell ({n} day{"" if n == 1 else "s"})"
    if bt is None:
        return f"{lab}: {n} days {R.pct(ret)}; backtest unavailable"
    w = window_returns(bt, n)
    if len(w) < 20:
        return f"{lab}: {n} days {R.pct(ret)}; backtest too short for {n}-day windows"
    p = float((w <= ret).mean() * 100)
    lo, hi = np.percentile(w, [5, 95])
    v = verdict(n, ret, bt)
    return f"{lab}: {n} days {R.pct(ret)} vs backtest {R.pct(lo)} to {R.pct(hi)} (5th-95th), at {p:.0f}th pct: {v}"


def evening_data(day, accts, hists, noise_rows, order_rows, paper_rows, cands, bt, research):
    """Everything the evening text and card show, as plain data."""
    ins = R.latest_in(paper_rows)
    day_noise = [r for r in noise_rows if r["time_ny"].startswith(str(day))]
    out = {}
    for name in ("long-term", "intraday"):
        a, eq = accts[name], accts[name].get("equity")
        pl = R.day_pl(a)
        n, ret = live_stats(hists.get(name, []), eq) if eq is not None else (0, 0.0)
        out[name] = {"equity": eq, "note": a.get("note", ""), "since": None if eq is None else (eq - START) / START,
                     "today": None if pl is None else (pl, pl / a["last_equity"]),
                     **trade_data(name, a, day, order_rows, day_noise, ins),
                     "days": n, "ret": ret, "path": live_path(hists.get(name, []), eq) if eq is not None else [],
                     "fan": fan(bt.get(name), n), "verdict": verdict(n, ret, bt.get(name)) if eq is not None else None,
                     "bt_line": None if eq is None else backtest_line(name, n, ret, bt.get(name))}
    return {"kind": "evening", "day": day, "accounts": out, "research": research, "waiting_candidates": waiting_candidates(cands)}


def text_evening(d):
    L = [f"{b('Evening ' + str(d['day']))}"]
    for name in ("long-term", "intraday"):
        a = d["accounts"][name]
        L.append(acct_head(name, a, "today", a["today"]))
        if a["note"]:
            L.append(f"  ⚠️ {e(a['note'][:90])}")
        L += trade_lines(a)
    L += ["", b("Matches the backtest?")]
    for name in ("long-term", "intraday"):
        a = d["accounts"][name]
        L.append("  " + e(a["bt_line"] if a["bt_line"] else f"{LABEL[name]}: live numbers unavailable"))
    L += ["", b("Research")]
    L += ["  " + e(research_line("Daily agent", d["research"]["daily"])), "  " + e(research_line("Intraday agent", d["research"]["intraday"]))]
    wc = d["waiting_candidates"]
    L.append(f"  Candidates awaiting approval: {len(wc) if wc else 'none'}")
    return "\n".join(L)


def build_evening(*args, **kw):
    return text_evening(evening_data(*args, **kw))


# ---------- live wrappers ----------
def _gather(day):
    accts = {n: R.get_account(n, k, s, p, day) for n, k, s, p in ACCOUNTS}
    hists = {n: get_history(k, s) for n, k, s, p in ACCOUNTS}
    return accts, hists


def gather_morning(now=None):
    today = (now or datetime.now(NY)).astimezone(NY).date()
    prev = previous_trading_day(today)
    accts, hists = _gather(today)
    d = morning_data(today, accts, hists, R.read_csv(MEM / "noise_live.csv"), R.read_json(MEM / "candidates.json"),
                     prev, (ROOT / "reports" / f"{prev}.md").exists())
    w = proposer_health.briefing_warning()   # research agents' Claude login refused (401)
    if w:
        d["flags"].append(w)
    return d


def gather_evening(now=None):
    day = (now or datetime.now(NY)).astimezone(NY).date()
    accts, hists = _gather(day)
    bt = {"long-term": safe_bt(longterm_backtest), "intraday": safe_bt(intraday_backtest)}
    res = {"daily": research_today(MEM / "journal.jsonl", day), "intraday": research_today(MEM / "intraday" / "journal.jsonl", day)}
    return evening_data(day, accts, hists, R.read_csv(MEM / "noise_live.csv"), R.read_csv(MEM / "alpaca_orders.csv"),
                        R.read_csv(MEM / "paper_log.csv"), R.read_json(MEM / "candidates.json"), bt, res)


def balance_data(day, per):
    """Plain data for the balance-history picture. per[name] = {equity, last_equity, history ({1D, ALL, ...} or None)}."""
    out = {}
    for name in ("long-term", "intraday"):
        p = per.get(name) or {}
        eq, le, h = p.get("equity"), p.get("last_equity"), p.get("history") or {}
        out[name] = {"equity": eq, "today": None if eq is None or not le else (eq - le, eq / le - 1),
                     "today_base": le or None, "since": None if eq is None else (eq - START) / START,
                     "h1d": h.get("1D") or [], "hall": h.get("ALL") or []}
    return {"kind": "balance", "day": day, "accounts": out}


def gather_balance(now=None):
    """Fresh from Alpaca when the account's keys are set (history + equity), else from the snapshot json files."""
    import alpaca_paper
    t = now or datetime.now(NY)
    day, per = t.astimezone(NY).date(), {}
    for name, kenv, senv, path in ACCOUNTS:
        snap, p = R.read_json(path) or {}, {}
        try:
            c = _client(kenv, senv)
            if c:
                a = c.get_account()
                p = {"equity": float(a.equity), "last_equity": float(a.last_equity), "history": alpaca_paper.account_history(c)}
        except Exception as ex:
            print(f"{name}: live balance fetch failed ({type(ex).__name__}), using the snapshot", file=sys.stderr)
            p = {}
        if not p.get("history"):
            p = {**p, "history": snap.get("history")}
        for k in ("equity", "last_equity"):
            if p.get(k) is None:
                p[k] = snap.get(k)
        hall, now_ts = (p.get("history") or {}).get("ALL"), int(t.timestamp())
        if hall and p.get("equity") and hall[-1][0] < now_ts - 3600:   # daily bars lag: end the line at the live balance
            p["history"] = {**p["history"], "ALL": hall + [[now_ts, round(p["equity"], 2)]]}
        h1d = (p.get("history") or {}).get("1D")
        if h1d and p.get("equity") and h1d[-1][0] < now_ts - 600:   # the day line ends at the live balance too
            p["history"] = {**p["history"], "1D": h1d + [[now_ts, round(p["equity"], 2)]]}
        per[name] = p
    return balance_data(day, per)


def send_balance(data=None):
    """Render and send the balance-history picture; True when Telegram accepted it. Never raises."""
    try:
        import briefing_cards as C
        data = data or gather_balance()
        return bool(telegram.send_photo(C.render_balance(data), C.balance_caption(data)))
    except Exception as ex:
        print(f"balance image failed: {type(ex).__name__}: {str(ex)[:100]}", file=sys.stderr)
        return False


def make_card(data):
    """(png bytes, caption) for the briefing data, or None when rendering fails (the caller falls back to text)."""
    try:
        import briefing_cards as C
        return C.render(data), C.caption(data)
    except Exception as ex:
        print(f"card rendering failed: {type(ex).__name__}: {str(ex)[:100]}", file=sys.stderr)
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["morning", "evening", "balance"])
    ap.add_argument("--dry-run", action="store_true", help="write the PNG to DRY_DIR and print the caption, send nothing")
    ap.add_argument("--text", action="store_true", help="send the old text message instead of the picture card")
    ap.add_argument("--force", action="store_true", help="morning: skip the 09:00-09:40 New York / trading-day gate")
    a = ap.parse_args(argv)
    now = datetime.now(NY)
    if a.which == "morning" and not (a.dry_run or a.force):
        if not morning_window(now) or not is_trading_day(now.date()):
            print(f"morning briefing skipped: {now:%Y-%m-%d %H:%M} New York is outside 09:00-09:40 or not a trading day")
            return 0
    if a.which == "balance":
        return main_balance(a.dry_run)
    text_fn = text_morning if a.which == "morning" else text_evening
    card = None
    try:
        data = gather_morning(now) if a.which == "morning" else gather_evening(now)
        text = text_fn(data)
        card = None if a.text else make_card(data)
    except Exception as ex:
        text = f"⚠️ {b(a.which.capitalize() + ' briefing failed')}: {e(type(ex).__name__)}: {e(str(ex)[:150])}"
    if a.dry_run:
        if card:
            DRY_DIR.mkdir(parents=True, exist_ok=True)
            out = DRY_DIR / f"briefing-{a.which}.png"
            out.write_bytes(card[0])
            print(f"{out}\n{card[1]}")
        else:
            print(text)
        return 0
    ok = bool(card) and telegram.send_photo(*card)
    if not ok:
        ok = telegram.send(text)
    print("sent" if ok else "not sent (missing TELEGRAM_* env or Telegram error)")
    if a.which == "evening" and card and ok:
        print("balance image " + ("sent" if send_balance() else "not sent"))
    return 0


def main_balance(dry_run):
    try:
        import briefing_cards as C
        data = gather_balance()
        if dry_run:
            DRY_DIR.mkdir(parents=True, exist_ok=True)
            out = DRY_DIR / "briefing-balance.png"
            out.write_bytes(C.render_balance(data))
            print(f"{out}\n{C.balance_caption(data)}")
            return 0
    except Exception as ex:
        print(f"balance image failed: {type(ex).__name__}: {str(ex)[:100]}", file=sys.stderr)
        return 1
    print("sent" if send_balance(data) else "not sent (missing TELEGRAM_* env, Telegram error or render error)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
