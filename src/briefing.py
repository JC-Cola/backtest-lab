"""Two short Telegram briefings sent from the GitHub Actions jobs (the Mac may be off).

    python src/briefing.py morning|evening [--dry-run] [--force]

morning: ~09:15 New York, before the open. Both paper accounts, open positions, orders waiting for the open, risk
         flags (distance to the agreed limits), new candidates. Two crons (EDT and EST) trigger it; the script only
         sends between 09:00 and 09:40 New York on a trading day, so exactly one of them does. --force skips that.
evening: after the nightly run. Today's trades, best/worst trade, day P/L, "Matches the backtest?" (live N-day
         return against the backtest's rolling N-day returns on the test years) and what the agents tested.
--dry-run prints the text instead of sending it. Pure build_* functions take plain data, so they are testable;
the morning()/evening() wrappers fetch it. All times are New York time.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta, timezone

import numpy as np

import report as R
import telegram
from report import ACCOUNTS, MEM, NY, ROOT, START

LABEL = {"long-term": "Long-term", "intraday": "Intraday"}
KILL = {"long-term": 0.20, "intraday": 0.12}   # agreed kill switches: drawdown from the account's peak
DAILY_LIMIT = {"intraday": 0.03}               # agreed daily loss limit (intraday account only)
FTMO_DAILY, FTMO_TOTAL = 0.05, 0.10            # FTMO reference limits
GATE = (time(9, 0), time(9, 40))
MIN_DAYS = 5                                   # fewer live days than this: too early to tell
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


def acct_head(name, a, chg_label, chg):
    if a.get("equity") is None:
        return f"{b(LABEL[name])}: no numbers ({e(a.get('note', ''))})"
    ds = "n/a" if chg is None else f"{R.usd(chg[0], True)} ({R.pct(chg[1])})"
    since = (a["equity"] - START) / START
    return f"{b(LABEL[name])} {R.usd(a['equity'])} | {chg_label} {ds} | since start {R.pct(since)}"


def waiting_candidates(cands):
    return [c for c in (cands or {}).get("candidates", []) if c.get("status") == "awaiting approval"]


# ---------- morning ----------
def build_morning(today, accts, hists, noise_rows, cands, prev_day=None, prev_report_exists=True):
    lt, it = accts["long-term"], accts["intraday"]
    L = [f"{b('Morning ' + str(today))} (before the open)"]
    for name in ("long-term", "intraday"):
        a = accts[name]
        L.append(acct_head(name, a, "last session", last_session(hists.get(name, []), a.get("last_equity"))))
        if a.get("equity") is not None:
            L.append(f"  Positions: {e(pos_text(a))}")
        if name == "long-term":
            w = waiting_orders(a)
            L.append("  Waiting for the open: " + (e(", ".join(w)) if w else "nothing"))
    L += ["", b("Risk")]
    for name in ("long-term", "intraday"):
        L.append("  " + e(risk_line(name, accts[name].get("equity"), hists.get(name, []), accts[name].get("last_equity"))))
    flags = []
    for p in it.get("positions", []):
        flags.append(f"Intraday still holds {qty(p['qty'])} {p['symbol']} (should be flat overnight)")
    if prev_day:
        mc = missed_checks(prev_day, noise_rows)
        if mc:
            flags.append(f"Missed intraday checks on {prev_day}: " + "; ".join(mc))
        if not prev_report_exists:
            flags.append(f"No nightly report found for {prev_day} (nightly run may have failed)")
    for name in ("long-term", "intraday"):
        if accts[name].get("note"):
            flags.append(f"{LABEL[name]}: {accts[name]['note'][:90]}")
    L += [f"  ⚠️ {e(f)}" for f in flags] or ["  No flags."]
    wc = waiting_candidates(cands)
    L += ["", b("Candidates") + (": none awaiting approval" if not wc else f": {len(wc)} awaiting approval")]
    L += [f"  {e(c['account'])}: {e(c['description'][:80])}" for c in wc[:3]]
    return "\n".join(L)


# ---------- evening: trades, best/worst ----------
def round_trips(fills):
    """[(symbol, realised P/L)] for each position that went back to flat, by average cost (handles shorts)."""
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
            out.append((s, pl.pop(s)))
        elif (p > 0) != (pos[s] > 0):        # flipped through zero
            out.append((s, pl.pop(s)))
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


def trade_lines(name, a, day, order_rows, day_noise, ins):
    fl = R.filled_on(a.get("orders", []), day)
    lines = [f"  {R.hhmm(o['filled'])} {o['side'].upper()} {qty(o['filled_qty'])} {o['symbol']} @ {R.usd(o['price'])}"
             f" ({e(short_why(o, name, order_rows, day_noise, ins))})" for o in fl[:MAX_TRADES]]
    if len(fl) > MAX_TRADES:
        lines.append(f"  +{len(fl) - MAX_TRADES} more")
    if not fl:
        lines.append("  no trades")
    if name == "intraday":
        bw = best_worst([(s, v) for s, v in round_trips(fl)])
    else:
        bw = best_worst([(p["symbol"], p.get("day_pl")) for p in a.get("positions", [])])
    if bw:
        lines.append("  " + e(bw))
    return lines


# ---------- evening: backtest comparison ----------
def live_stats(hist, equity):
    """(days, return since the $25,000 start): history closes plus the current equity; leading days still at
    exactly $25,000 are not counted."""
    pts = list(hist)
    if equity is not None and not (pts and abs(pts[-1] - equity) < 0.005):
        pts.append(equity)
    while pts and abs(pts[0] - START) < 1e-6:
        pts.pop(0)
    return (len(pts), pts[-1] / START - 1) if pts else (0, 0.0)


def window_returns(r, n):
    """Compounded returns of every run of n consecutive days in the daily-return array r."""
    r = np.asarray(r, float)
    if n < 1 or n > len(r):
        return np.array([])
    c = np.r_[1.0, np.cumprod(1 + r)]
    return c[n:] / c[:-n] - 1


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
    v = "below normal" if p < 5 else "above normal" if p > 95 else "in line"
    return f"{lab}: {n} days {R.pct(ret)} vs backtest {R.pct(lo)} to {R.pct(hi)} (5th-95th), at {p:.0f}th pct: {v}"


# ---------- evening: research ----------
def research_today(path, day):
    """{'tested','scored','best','holdout'} for journal records stamped with New York date `day`."""
    out = {"tested": 0, "scored": 0, "best": None, "holdout": 0}
    try:
        lines = open(path).read().splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            r = json.loads(ln)
            ts = datetime.fromisoformat(r["ts"])
        except (ValueError, KeyError):
            continue
        if ts.astimezone(NY).date() != day:
            continue
        out["tested"] += 1
        if r.get("status") in ("scored", "promoted"):
            out["scored"] += 1
            out["best"] = r["reward"] if out["best"] is None else max(out["best"], r["reward"])
        out["holdout"] += bool(r.get("holdout"))
    return out


def research_line(label, s):
    if not s["tested"]:
        return f"{label}: no ideas tested today"
    best = "n/a" if s["best"] is None else f"{s['best']:.3f}"
    return f"{label}: {s['tested']} tested, {s['scored']} scored, best score {best}, {s['holdout']} held-out shots"


def build_evening(day, accts, hists, noise_rows, order_rows, paper_rows, cands, bt, research):
    ins = R.latest_in(paper_rows)
    day_noise = [r for r in noise_rows if r["time_ny"].startswith(str(day))]
    L = [f"{b('Evening ' + str(day))}"]
    for name in ("long-term", "intraday"):
        a = accts[name]
        d = R.day_pl(a)
        L.append(acct_head(name, a, "today", None if d is None else (d, d / a["last_equity"])))
        if a.get("note"):
            L.append(f"  ⚠️ {e(a['note'][:90])}")
        L += trade_lines(name, a, day, order_rows, day_noise, ins)
    L += ["", b("Matches the backtest?")]
    for name in ("long-term", "intraday"):
        hist, eq = hists.get(name, []), accts[name].get("equity")
        if eq is None:
            L.append(f"  {e(LABEL[name])}: live numbers unavailable")
            continue
        n, ret = live_stats(hist, eq)
        L.append("  " + e(backtest_line(name, n, ret, bt.get(name))))
    L += ["", b("Research")]
    L += ["  " + e(research_line("Daily agent", research["daily"])), "  " + e(research_line("Intraday agent", research["intraday"]))]
    wc = waiting_candidates(cands)
    L.append(f"  Candidates awaiting approval: {len(wc) if wc else 'none'}")
    return "\n".join(L)


# ---------- backtest series (test years, daily returns) ----------
def longterm_backtest():
    """Equal-weight mix of the 5 daily strategies (build_lab.BOOK in LAB_DAILY, chosen params), daily returns 2021+."""
    import pandas as pd
    import build_lab as B
    import data
    import ftmo_lab as FL
    import intraday as I
    from strategies import positions
    results = json.loads((ROOT / "results" / "results.json").read_text())["results"]
    cp = ROOT / "results" / "confirmation.json"
    conf = json.loads(cp.read_text()) if cp.exists() else None
    ctx_raw, cache, parts, cal = data.load_context(), {}, [], None
    for bk in B.BOOK:
        if bk["code"] not in B.LAB_DAILY:
            continue
        st = B.BY_ID[bk["id"]]
        df, ctx, cfg = B.build_market(bk["market"], ctx_raw, cache)
        v = B.verdict_for(bk["id"], bk["market"], results, conf)
        p = next(g for g in st["grid"] if json.loads(json.dumps(g)) == v["params"])
        pos = np.clip(np.nan_to_num(positions(st, df, ctx, p).values.astype(float)), 0, 1)
        fin, low = FL.daily_paths(df, pos, cfg["cost_bps"])
        parts.append((df.index.tz_localize(None) if df.index.tz is not None else df.index, fin, low))
    spy = B.build_market("SPY", ctx_raw, cache)[0].index
    spy = spy.tz_localize(None) if spy.tz is not None else spy
    cal = spy[spy >= pd.Timestamp(I.OOS_START)]
    return sum(FL._onto(d, f, lo, cal)[0] for d, f, lo in parts) / len(parts)


def intraday_backtest():
    """Noise-band breakout, SPY 50% + QQQ 50% (params from results/intraday.json), daily returns 2021+."""
    import ftmo
    import intraday as I
    rows = json.loads((ROOT / "results" / "intraday.json").read_text())["results"]
    out = []
    for sym in ("SPY", "QQQ"):
        D = I.Days(sym)
        p = next(r["params"] for r in rows if r["strategy"] == "id_noise_breakout" and r["asset"] == sym)
        f, _ = ftmo.day_paths(D, I.noise_breakout(D, **p))
        out.append(f[int(D.dates.searchsorted(np.datetime64(I.OOS_START))):])
    return 0.5 * out[0] + 0.5 * out[1]


def safe_bt(fn):
    try:
        return fn()
    except Exception as ex:
        print(f"backtest series failed: {type(ex).__name__}: {str(ex)[:100]}", file=sys.stderr)
        return None


# ---------- live wrappers ----------
def _gather(day):
    accts = {n: R.get_account(n, k, s, p, day) for n, k, s, p in ACCOUNTS}
    hists = {n: get_history(k, s) for n, k, s, p in ACCOUNTS}
    return accts, hists


def morning(now=None):
    today = (now or datetime.now(NY)).astimezone(NY).date()
    prev = previous_trading_day(today)
    accts, hists = _gather(today)
    return build_morning(today, accts, hists, R.read_csv(MEM / "noise_live.csv"), R.read_json(MEM / "candidates.json"),
                         prev, (ROOT / "reports" / f"{prev}.md").exists())


def evening(now=None):
    day = (now or datetime.now(NY)).astimezone(NY).date()
    accts, hists = _gather(day)
    bt = {"long-term": safe_bt(longterm_backtest), "intraday": safe_bt(intraday_backtest)}
    res = {"daily": research_today(MEM / "journal.jsonl", day), "intraday": research_today(MEM / "intraday" / "journal.jsonl", day)}
    return build_evening(day, accts, hists, R.read_csv(MEM / "noise_live.csv"), R.read_csv(MEM / "alpaca_orders.csv"),
                         R.read_csv(MEM / "paper_log.csv"), R.read_json(MEM / "candidates.json"), bt, res)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["morning", "evening"])
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending")
    ap.add_argument("--force", action="store_true", help="morning: skip the 09:00-09:40 New York / trading-day gate")
    a = ap.parse_args(argv)
    now = datetime.now(NY)
    if a.which == "morning" and not (a.dry_run or a.force):
        if not morning_window(now) or not is_trading_day(now.date()):
            print(f"morning briefing skipped: {now:%Y-%m-%d %H:%M} New York is outside 09:00-09:40 or not a trading day")
            return 0
    try:
        text = morning(now) if a.which == "morning" else evening(now)
    except Exception as ex:
        text = f"⚠️ {b(a.which.capitalize() + ' briefing failed')}: {e(type(ex).__name__)}: {e(str(ex)[:150])}"
    if a.dry_run:
        print(text)
        return 0
    ok = telegram.send(text)
    print("sent" if ok else "not sent (missing TELEGRAM_* env or Telegram error)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
