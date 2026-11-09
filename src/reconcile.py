"""Live-vs-backtest reconciliation for the intraday noise-band strategy (alert only; never trades).

    python src/reconcile.py [YYYY-MM-DD ...] [--no-alert] [--dry-run]     (default: today, New York)

For each day and for SPY and QQQ it recomputes the BACKTEST's six decisions from full-market SIP minute bars with the
backtest's own code (intraday.Days + intraday.noise_decisions, lookback 14, check 60: the 09:59, 10:59 ... 14:59 bars;
fills assumed at the next minute's open, the final exit at the session close) and compares them with what the live job
logged (agent_memory/noise_live.csv, IEX data) and with the account's real fills (Alpaca FILL activities).
Per check: match, price/VWAP/band differences. Per fill: slippage in bp against the backtest's assumed fill, positive =
worse than the backtest. Per day: decisions matched, mean slippage, live P&L (from the fills) vs model P&L (same 50%
equity sizing, 1 bp per side). Appended to agent_memory/reconcile.json with a running summary.
Known, deliberate difference: live acts at ~:01:30 on the bar that just completed (the :00 bar), the backtest on the :59
bar one minute earlier. When the two disagree, `why` says whether SIP data at the live bar would have given the live
decision (timing) or not (IEX vs SIP data).
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

import intraday as I

ROOT = Path(__file__).resolve().parent.parent
LIVE_CSV = ROOT / "agent_memory" / "noise_live.csv"
OUT = ROOT / "agent_memory" / "reconcile.json"
NY = ZoneInfo("America/New_York")
SYMBOLS, LOOKBACK, CHECK, FRACTION = ["SPY", "QQQ"], 14, 60, 0.5
START_EQUITY = 25_000.0
SIDE = {1.0: "long", -1.0: "short", 0.0: "flat"}
CLOSE_EVENTS = ("flatten", "manual close", "safety", "risk flatten")


def label(m):
    return f"{(570 + m + 1) // 60:02d}:{(570 + m + 1) % 60:02d}"   # decision bar m is decided at the close = this clock minute


# ---------------------------------------------------------------- inputs
def fetch_frame(sym, day, now=None):
    """Full-market SIP minute bars (split/dividend adjusted, regular hours) for the ~45 days up to `day`, like intraday_data."""
    import alpaca_keys
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    k, s = alpaca_keys.data_keys()
    if not (k and s):
        raise RuntimeError("no Alpaca data keys")
    now = now or datetime.now(timezone.utc)
    start = datetime.combine(day - timedelta(days=45), time.min, NY)
    end = min(datetime.combine(day + timedelta(days=1), time.min, NY), now - timedelta(minutes=20))   # free plan: SIP is 15 min delayed
    df = StockHistoricalDataClient(k, s).get_stock_bars(StockBarsRequest(
        symbol_or_symbols=sym, timeframe=TimeFrame.Minute, start=start, end=end, feed=DataFeed.SIP,
        adjustment=Adjustment.ALL)).df.droplevel(0)
    df.index = df.index.tz_convert(NY)
    t = df.index.hour * 60 + df.index.minute
    return df[(t >= 570) & (t < 960)][["open", "high", "low", "close", "volume"]]


def read_live(path=LIVE_CSV):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def fetch_account_fills():
    from alpaca.trading.client import TradingClient
    import alpaca_paper
    k, s = os.environ.get("ALPACA_INTRADAY_API_KEY"), os.environ.get("ALPACA_INTRADAY_SECRET_KEY")
    if not (k and s):
        raise RuntimeError("ALPACA_INTRADAY_API_KEY / SECRET_KEY not set")
    return alpaca_paper.fetch_fills(TradingClient(k, s, paper=True))


# ---------------------------------------------------------------- pure pieces
def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def live_rows_for(rows, day, sym):
    """({check index 0..5: first logged check row with a decision}, {order_id: ('check', idx) | ('close', None)}) for one day and symbol."""
    checks, orders = {}, {}
    for r in rows:
        if r["symbol"] != sym or not r["time_ny"].startswith(str(day)):
            continue
        ts = datetime.fromisoformat(r["time_ny"])
        ev = r.get("event", "")
        if ev == "check" and r.get("decision") in ("long", "short", "flat") and 10 <= ts.hour <= 15:
            checks.setdefault(ts.hour - 10, r)
            if r.get("order_id"):
                orders[r["order_id"]] = ("check", ts.hour - 10)
        elif ev.startswith(CLOSE_EVENTS) and r.get("order_id"):
            orders[r["order_id"]] = ("close", None)
    return checks, orders


def compare_checks(D, d, sym, live):
    """One dict per backtest check: backtest vs live decision, matched, number differences and, on a mismatch, why."""
    bt = I.noise_decisions(D, d, LOOKBACK, CHECK)
    out = []
    for i, (m, side, p, vw, up, dn) in enumerate(bt):
        row = live.get(i)
        rec = {"symbol": sym, "check": f"{10 + i}:00", "bar": label(m - 1), "bt_decision": SIDE[side], "bt_price": round(float(p), 2),
               "bt_vwap": round(float(vw), 2), "bt_upper": round(float(up), 2), "bt_lower": round(float(dn), 2),
               "bt_fill": round(float(D.P[d, m + 1]), 2), "live_decision": None, "matched": False}
        if row is None:
            rec["why"] = "no live decision logged"
            out.append(rec)
            continue
        lp, lv, lu, ll = (_f(row.get(k)) for k in ("price", "vwap", "upper", "lower"))
        rec.update(live_decision=row["decision"], live_price=lp, live_vwap=lv, live_upper=lu, live_lower=ll,
                   matched=row["decision"] == rec["bt_decision"])
        if None not in (lp, lv, lu, ll):
            rec.update(price_diff=round(lp - rec["bt_price"], 2), vwap_diff=round(lv - rec["bt_vwap"], 2),
                       upper_diff=round(lu - rec["bt_upper"], 2), lower_diff=round(ll - rec["bt_lower"], 2))
        if not rec["matched"]:
            ts = datetime.fromisoformat(row["time_ny"])
            ml = ts.hour * 60 + ts.minute - 570 - 1   # the bar index the live code used for sigma and the price
            alt = I.noise_decisions(D, d, LOOKBACK, CHECK, minutes=[ml])
            if alt and SIDE[alt[0][1]] == row["decision"]:
                rec["why"] = f"timing: live decided on the {ts.hour:02d}:{(ts.minute - 1):02d} bar, SIP data at that bar gives the live decision"
            else:
                rec["why"] = "data: IEX vs SIP differ (SIP at the live bar does not give the live decision either)"
        out.append(rec)
    return out


def slippage_bp(side, fill, assumed):
    """Positive = worse than the backtest's assumed price (paid more on a buy, received less on a sell)."""
    return (1 if side == "buy" else -1) * (fill / assumed - 1) * 1e4


def fill_records(fills, day, sym, D, d, orders):
    """Slippage of every real fill of `sym` on `day` against the backtest's assumed fill."""
    end = int(D.last[d]) + 1
    out = []
    for f in fills:
        t = datetime.fromisoformat(f["time"].replace("Z", "+00:00")).astimezone(NY)
        if f["symbol"] != sym or t.date() != day:
            continue
        kind, idx = orders.get(f.get("order_id"), (None, None))
        mnow = t.hour * 60 + t.minute - 570
        if not 0 <= mnow <= end:   # outside the regular session: no minute bar to compare with
            out.append({"symbol": sym, "time": t.strftime("%H:%M:%S"), "side": f["side"], "qty": float(f["qty"]),
                        "price": float(f["price"]), "kind": "offhours", "assumed": None, "assumed_ref": None, "slippage_bp": None})
            continue
        if kind is None:
            kind = "close" if t.hour * 60 + t.minute >= 15 * 60 + 50 else "other"
        if kind == "check":
            assumed, ref = float(D.P[d, 29 + 60 * idx + 1]), f"open of the {label(29 + 60 * idx + 1)} bar"
        elif kind == "close":
            assumed, ref = float(D.P[d, end]), "session close"
        else:
            assumed, ref = float(D.P[d, min(max(mnow, 0), end)]), "open of the fill minute"
        px = float(f["price"])
        out.append({"symbol": sym, "time": t.strftime("%H:%M:%S"), "side": f["side"], "qty": float(f["qty"]), "price": px,
                    "kind": kind, "assumed": round(assumed, 2), "assumed_ref": ref,
                    "slippage_bp": round(slippage_bp(f["side"], px, assumed), 2)})
    return out


def live_pnl(fills, day, closes):
    """Cash P&L of the day's fills, any open shares marked at the session close. closes = {symbol: close}."""
    cash, pos = 0.0, {}
    for f in fills:
        t = datetime.fromisoformat(f["time"].replace("Z", "+00:00")).astimezone(NY)
        if t.date() != day or f["symbol"] not in closes:
            continue
        q, p = float(f["qty"]), float(f["price"])
        sgn = 1 if f["side"] == "buy" else -1
        cash -= sgn * q * p
        pos[f["symbol"]] = pos.get(f["symbol"], 0) + sgn * q
    return cash + sum(q * closes[s] for s, q in pos.items())


def realized_before(fills, day):
    """Cash result of all fills before `day` plus any shares still held, marked at their last fill price (a position can
    be left overnight), to get the day's starting equity."""
    cash, pos, last = 0.0, {}, {}
    for f in fills:
        if datetime.fromisoformat(f["time"].replace("Z", "+00:00")).astimezone(NY).date() < day:
            q, p = float(f["qty"]), float(f["price"])
            sgn = 1 if f["side"] == "buy" else -1   # sides: buy, sell, sell_short
            cash -= sgn * q * p
            pos[f["symbol"]], last[f["symbol"]] = pos.get(f["symbol"], 0) + sgn * q, p
    return cash + sum(q * last[sym] for sym, q in pos.items())


def model_pnl(D, d, equity, fraction=FRACTION):
    """Backtest P&L of one symbol-day in dollars: shares = floor(equity x fraction / price) at each check, filled at the
    next minute's open, flat at the session close, 1 bp per side (intraday.COST)."""
    qty, cash, cost = 0, 0.0, 0.0
    for m, side, p, *_ in I.noise_decisions(D, d, LOOKBACK, CHECK):
        tgt = int(side) * math.floor(equity * fraction / p)
        f = float(D.P[d, m + 1])
        cash -= (tgt - qty) * f
        cost += I.COST * abs(tgt - qty) * f
        qty = tgt
    c = float(D.P[d, int(D.last[d]) + 1])
    return cash + qty * c - cost - I.COST * abs(qty) * c


def reconcile_day(day, frames, live_rows, fills):
    """The day's record from plain inputs: frames {symbol: minute DataFrame}, live csv rows, account fills. None when
    there is no SIP session for the day."""
    checks, fl, closes, model = [], [], {}, 0.0
    eq = START_EQUITY + realized_before(fills, day)
    for sym in SYMBOLS:
        D = I.Days.from_frame(frames[sym])
        hit = np.flatnonzero(D.dates == np.datetime64(day))
        if not len(hit) or hit[0] < LOOKBACK:
            return None
        d = int(hit[0])
        live, orders = live_rows_for(live_rows, day, sym)
        checks += compare_checks(D, d, sym, live)
        fl += fill_records(fills, day, sym, D, d, orders)
        closes[sym] = float(D.P[d, int(D.last[d]) + 1])
        model += model_pnl(D, d, eq)
    n, matched = len(checks), sum(c["matched"] for c in checks)
    missing = sum(c["live_decision"] is None for c in checks)
    slips = [f["slippage_bp"] for f in fl if f["slippage_bp"] is not None]
    by = lambda k: [f["slippage_bp"] for f in fl if f["kind"] == k]
    mean = lambda x: round(float(np.mean(x)), 2) if x else None
    return {"date": str(day), "checks": checks, "fills": fl,
            "summary": {"decisions": n, "matched": matched, "missing": missing, "fills": len(slips),
                        "mean_slippage_bp": mean(slips), "mean_slippage_checks_bp": mean(by("check")),
                        "mean_slippage_close_bp": mean(by("close")),
                        "live_pnl": round(live_pnl(fills, day, closes), 2), "model_pnl": round(model, 2),
                        "start_equity": round(eq, 2)}}


def summarize(days):
    """Running totals over every stored day."""
    s = [x["summary"] for x in days]
    fills = [(x["summary"]["fills"], x["summary"]["mean_slippage_bp"]) for x in days if x["summary"]["mean_slippage_bp"] is not None]
    nf = sum(a for a, _ in fills)
    dec, mat = sum(x["decisions"] for x in s), sum(x["matched"] for x in s)
    return {"days": len(days), "decisions": dec, "matched": mat, "match_rate": round(mat / dec, 4) if dec else None,
            "fills": nf, "mean_slippage_bp": round(sum(a * b for a, b in fills) / nf, 2) if nf else None,
            "live_pnl": round(sum(x["live_pnl"] for x in s), 2), "model_pnl": round(sum(x["model_pnl"] for x in s), 2),
            "live_minus_model": round(sum(x["live_pnl"] - x["model_pnl"] for x in s), 2)}


def store(rec, path=OUT):
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    days = [x for x in data.get("days", []) if x["date"] != rec["date"]] + [rec]
    days.sort(key=lambda x: x["date"])
    Path(path).write_text(json.dumps({"summary": summarize(days), "days": days}, indent=1) + "\n")


def money(x):
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


def line(rec):
    """The one evening-briefing line."""
    s = rec["summary"]
    sl = "n/a" if s["mean_slippage_bp"] is None else f"{s['mean_slippage_bp']:+.1f} bp"
    miss = f" ({s['missing']} not logged)" if s["missing"] else ""
    return (f"Live vs backtest today: {s['matched']}/{s['decisions']} decisions matched{miss} · avg slippage {sl} · "
            f"P&L live {money(s['live_pnl'])} vs model {money(s['model_pnl'])}")


def briefing_line(day, path=OUT):
    """The line for `day` from reconcile.json, or None (missing file / day) so the briefing skips it silently."""
    try:
        for x in json.loads(Path(path).read_text())["days"]:
            if x["date"] == str(day):
                return line(x)
    except Exception:
        pass
    return None


def alert_text(rec):
    """Telegram text for the decision mismatches (both sides logged), or None."""
    bad = [c for c in rec["checks"] if c["live_decision"] is not None and not c["matched"]]
    if not bad:
        return None
    L = [f"⚠️ Live vs backtest {rec['date']}: {len(bad)} decision mismatch{'es' if len(bad) > 1 else ''}"]
    for c in bad:
        L.append(f"{c['symbol']} {c['check']}: live {c['live_decision']} (price {c.get('live_price')}, vwap {c.get('live_vwap')}, "
                 f"bands {c.get('live_lower')}-{c.get('live_upper')}) vs model {c['bt_decision']} (price {c['bt_price']}, "
                 f"vwap {c['bt_vwap']}, bands {c['bt_lower']}-{c['bt_upper']}). {c['why']}")
    return "\n".join(L)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    alert, dry = "--no-alert" not in args, "--dry-run" in args
    days = [datetime.strptime(a, "%Y-%m-%d").date() for a in args if not a.startswith("--")] or [datetime.now(NY).date()]
    rows = read_live()
    fills = fetch_account_fills()
    frames = {}
    for day in sorted(days):
        if day.weekday() > 4:
            print(f"{day}: weekend, skipped")
            continue
        if datetime.now(NY) < datetime.combine(day, time(16, 20), NY):   # free-plan SIP data lags ~15 min: the day is not complete
            print(f"{day}: SIP data for the full session is not available before 16:20 New York, skipped")
            continue
        for sym in SYMBOLS:
            frames[sym] = fetch_frame(sym, day)
        rec = reconcile_day(day, frames, rows, fills)
        if rec is None:
            print(f"{day}: no SIP session / not enough history, skipped")
            continue
        print(line(rec))
        for c in rec["checks"]:
            if not c["matched"]:
                print(f"  {c['symbol']} {c['check']}: live {c['live_decision']} vs model {c['bt_decision']} - {c['why']}")
        if not dry:
            store(rec)
        text = alert_text(rec)
        if text and alert and not dry:
            import telegram
            telegram.send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
