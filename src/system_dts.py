"""System B: Jake Schincariol's day-trader-skill pipeline, run as an automated SHADOW paper strategy.

Upstream tool code is vendored unmodified in third_party/day_trader_skill (see UPSTREAM.md there). This file only
wires it: scanner -> news -> entry timer -> exit planner -> time machine -> budget -> risk, then manages open
positions bar by bar. The upstream "human yes" is replaced by the gates below because nothing here can place an
order: fills are simulated in agent_memory/system_dts.json (see dts_ledger.py for the cost model).

  python system_dts.py run                          # one scheduled check (live clock); Telegram on
  python system_dts.py run --now "2026-11-06 10:15" # pin the New York clock
  python system_dts.py replay --date 2026-11-06     # dry run of a past session, no Telegram, own state file

GATES (all must pass; a rejected setup is logged with the gate and the reason):
  scan     scanner.scan_orb at ~10:15 NY: 15-min opening range, price >= $5, >= 50k shares by the cutoff
  news     no halt/risk-tagged headline and no distress 8-K (1.03 1.05 2.04 2.06 3.01 4.02 5.01) in the last 18h
  entry    entry timer says TRIGGERED (first 5-min close above the range high)
  exit     stop = opening-range midpoint, target 2R, flat by 15:55 (planned at the next bar's open price)
  odds     time machine (10,000 replays, seed 19), STRICT (default; the backtest's best): its verdict must be "leans toward
           the target" or better (target-first share of the replays that hit a side beats the 33% break-even for 2R by
           >= 5 points), replay average R > 0, and with >= 10 historical triggers history average R >= 0.
           --loose-gate: break-even verdict is enough (tm_min_lean 0). --no-odds-gate / CFG["odds_gate"] False: off.
  budget   size to a max loss of 0.5% of equity (stop distance decides), <= 25% of equity per position
  risk     trader-risk check: <= 4 trades a day, daily loss limit, reward:risk >= 1, entry window, no margin
Slots: at most 4 concurrent positions. One scan a day; positions are flat by 15:55 NY.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import dts_ledger as L
from dts_core import (CFG, ROOT, SEC_UA, STATE_PATH, UNIVERSE_PATH, bars_of, floor5, hhmm, news_m, scan_m, tm_m, vend)  # noqa: F401
from dts_gates import news_gate, odds_gate, plan_trade, size_and_risk


# ---------------------------------------------------------------- data (Yahoo, cached per run; tests pass a fake)

class Yahoo:
    """Free Yahoo data through the scanner's own fetchers. Cached for the life of the object."""

    def __init__(self, pause: float = 0.3):
        self.pause, self.cache = pause, {}

    def today(self) -> dt.date:
        s = scan_m()
        return s.to_eastern(s.parse_now(None)).date()

    def spark(self, symbols, rng, interval):
        return scan_m().fetch_spark(symbols, rng, interval, self.pause)

    def chart(self, sym, rng, interval) -> str:
        k = (sym, rng, interval)
        if k not in self.cache:
            s = scan_m()
            time.sleep(self.pause)
            self.cache[k] = s.yahoo_get(s.chart_path(sym, rng, interval))
        return self.cache[k]

    def universe_texts(self) -> dict:
        s = scan_m()
        return {f: s.http_get(s.NASDAQ_URL + f, timeout=60) for f in s.NASDAQ_FILES}


def ensure_universe(state, data, today: dt.date) -> list:
    """Symbols from the scanner's own Nasdaq Trader builder, refreshed weekly into agent_memory/dts_universe.txt."""
    s = scan_m()
    last = state.get("universe_refreshed")
    stale = not last or (today - dt.date.fromisoformat(last)).days >= CFG["universe_days"]
    if (stale or not UNIVERSE_PATH.exists()):
        try:
            kept, _, _ = s.build_universe(data.universe_texts())
            if kept:
                UNIVERSE_PATH.write_text("".join("%s|%s\n" % (a, b) for a, b in kept))
                state["universe_refreshed"] = today.isoformat()
        except Exception as e:  # noqa: BLE001 - keep the old list if Nasdaq Trader is down
            print("universe refresh failed (%s): %s" % (type(e).__name__, e))
    if not UNIVERSE_PATH.exists():
        raise s.ScanError("no universe: Nasdaq Trader was unreachable and there is no cached list")
    return s.symbols_from_file(str(UNIVERSE_PATH))



# ---------------------------------------------------------------- scan and enter

def say(state, day, notify, text):
    d = state["days"][day]
    if notify and d.get("telegram", 0) < CFG["telegram_cap"] and notify(text):
        d["telegram"] = d.get("telegram", 0) + 1


def consider(state, dec, sym, day, T_et, T_utc, data, notify, path, sec_state, fetcher, broker) -> bool:
    """One ranked setup through news -> entry -> exit -> time machine -> budget -> risk. True when taken."""
    s = scan_m()
    ng = news_gate(sym, T_utc, sec_state, fetcher, [])
    dec["news"] = {k: ng[k] for k in ("reasons", "flags", "items")}
    if ng["block"]:
        dec.update(status="skipped", gate="news", reason="; ".join(ng["reasons"]))
        return False
    # entry timer + exit planner, planned at the next bar's open: the price we can actually get now
    text60 = data.chart(sym, "60d", "5m")
    ser = s.series_from_result(json.loads(text60)["chart"]["result"][0])
    bar = next((b for b in bars_of(ser, day, 5, s.CLOSE) if b["start"] == T_et), None)
    if bar is None:
        dec.update(status="skipped", gate="entry", reason="no bar at %s to fill on" % T_et.strftime("%H:%M"))
        return False
    p0 = round(bar["o"], 2)
    plan, res = plan_trade(sym, text60, T_et, T_utc, day, p0)
    if plan is None:
        dec.update(status="skipped", gate="entry/exit", reason=res)
        return False
    dec["plan"] = {k: plan[k] for k in ("entry", "stop", "target", "risk_per_share", "reward_risk", "flat_by")}
    odds, why = odds_gate(text60, plan, T_utc)
    dec["odds"] = odds
    if why:
        dec.update(status="skipped", gate="odds", reason=why)
        return False
    broker.sync(state)
    qty, note = size_and_risk(sym, plan, p0, state, T_et, T_utc, broker.equity(state))
    if qty <= 0:
        dec.update(status="skipped", gate=note.split(":")[0], reason=note)
        return False
    trig = res["trigger"]
    pos = broker.open(state, sym, qty, p0, T_et, T_utc, plan,
                      {"odds": odds, "gates": {"news": dec["news"], "risk": note},
                           "trigger": {"time": trig["close_time"], "price": trig["price"], "pct_over": trig["pct_over"],
                                       "or_high": res["opening_range"]["high"], "or_low": res["opening_range"]["low"]}})
    dec.update(status="taken", qty=qty, fill=pos["avg_price"], reason=note)
    say(state, day.isoformat(), notify, "🅱️ DTS shadow · OPEN LONG %s %d @ %.2f · stop %.2f · target %.2f · odds %.0f%%"
        % (sym, qty, pos["avg_price"], plan["stop"], plan["target"], 100 * odds["p_green"]))
    L.save(state, path)
    return True


def scan_and_enter(state, day, T_et, T_utc, data, notify, path, broker):
    s, tm = scan_m(), tm_m()
    rec = state["days"][day.isoformat()]
    t0 = time.time()
    symbols = ensure_universe(state, data, day)
    try:
        _, cutoff = s.orb_window(T_utc, None, CFG["or_minutes"], dt.time(15, 30))
    except s.ScanError as e:
        rec["scan"] = {"status": "skipped", "why": str(e)}
        return
    rng = "1d" if day == data.today() else "5d"
    stage1, failed = data.spark(symbols, rng, "5m")

    def confirm(sym):
        return s.parse_chart(data.chart(sym, rng, "5m"), sym)
    hits, counts, failed2 = s.scan_orb(stage1, symbols, day, cutoff, CFG["or_minutes"], CFG["min_price"],
                                       CFG["min_volume"], confirm, CFG["max_confirm"])
    dv = {}
    for h in hits:
        # Yahoo's spark (stage 1) has closes only, no volume -> rank by the confirmed v8 chart (cached, has volume)
        series = stage1[h["symbol"]]
        try:
            series = confirm(h["symbol"])
        except Exception:  # noqa: BLE001 - fall back to stage 1 (volume 0) rather than drop the hit
            pass
        bars = s.session_bars(series, day, cutoff)
        dv[h["symbol"]] = sum((b.get("v") or 0) * (b.get("c") or 0) for b in bars)
    ranked = sorted(hits, key=lambda h: -dv[h["symbol"]])      # most traded first, like the scanner's own daily list
    rec["scan"] = {"status": "done", "at": cutoff.strftime("%H:%M"), "universe": len(symbols), "counts": counts,
                   "failed": len(failed) + len(failed2), "hits": [h["symbol"] for h in ranked][:60],
                   "seconds": round(time.time() - t0, 1)}
    rec["decisions"] = []
    n = news_m()
    ua = n.sec_user_agent(os.environ.get("SEC_USER_AGENT") or SEC_UA, "")
    fetcher, sec_state = n.Fetcher(ua), {}
    taken = 0
    for h in ranked[:CFG["max_eval"]]:
        if len(state["positions"]) >= CFG["max_concurrent"]:
            break
        sym = h["symbol"]
        dec = {"symbol": sym, "price": h["price"], "breakout_at": h["at"], "pct_over": h["pct_over"],
               "dollar_volume": round(dv[sym])}
        rec["decisions"].append(dec)
        try:
            if consider(state, dec, sym, day, T_et, T_utc, data, notify, path, sec_state, fetcher, broker):
                taken += 1
        except Exception as e:  # noqa: BLE001 - one bad symbol must not stop the others
            dec.update(status="error", gate="pipeline", reason="%s: %s" % (type(e).__name__, e))
    rec["scan"]["evaluated"] = len(rec["decisions"])
    rec["scan"]["taken"] = taken


# ---------------------------------------------------------------- manage open positions

def manage(state, now_utc, data, notify, path, broker):
    s = scan_m()
    now_et = s.to_eastern(now_utc)
    if broker.live:
        def flat_for(pos):
            return dt.datetime.combine(dt.date.fromisoformat(pos["date"]), dt.time(*map(int, pos["flat_by"].split(":"))))
        sess = s.session(now_et.date())
        broker.manage(state, now_utc, now_et, dt.datetime.combine(now_et.date(), sess[1]), flat_for)
        L.save(state, path)
        return
    for pos in list(state["positions"]):
        day = dt.date.fromisoformat(pos["date"])
        sess = s.session(day)
        close_t = sess[1] if sess else s.CLOSE
        close_et = dt.datetime.combine(day, close_t)
        cutoff = min(now_et, close_et)
        flat_et = dt.datetime.combine(day, dt.time(*map(int, pos["flat_by"].split(":"))))
        back = (data.today() - day).days
        bars = []
        if back > 5:
            ex = {"ref": pos["entry_ref"], "reason": "no intraday data left to manage it: flat at entry", "at": close_et}
            last = pos["entry_ref"]
        else:
            rng = "1d" if back == 0 else "5d"
            for step, interval in ((1, "1m"), (5, "5m")):
                try:
                    ser = s.parse_chart(data.chart(pos["symbol"], rng, interval), pos["symbol"])
                except Exception:  # noqa: BLE001
                    continue
                bars = bars_of(ser, day, step, close_t)
                want = max(0.0, (cutoff - dt.datetime.combine(day, s.OPEN)).total_seconds() / 60.0 / step)
                if len(bars) >= 0.8 * want and bars:
                    break
            ex, last, checked = L.walk_position(pos, bars, cutoff, close_et, flat_et)
            if ex is None:
                pos["price"], pos["checked_to"] = last, checked.strftime("%Y-%m-%d %H:%M")
                continue
        at = ex["at"]
        trade = L.close_position(state, pos, ex["ref"], at, s.eastern_to_utc(at), ex["reason"])
        rec = state["days"].setdefault(trade["date"], {"scan": None, "decisions": [], "telegram": 0})
        say(state, trade["date"], notify, "🅱️ DTS shadow · CLOSE LONG %s %d @ %.2f · %+.2f USD (%+.2f%%) · %s"
            % (pos["symbol"], pos["qty"], trade["exit_price"], trade["pnl"], trade["pnl_pct"], ex["reason"]))
        L.save(state, path)


# ---------------------------------------------------------------- one scheduled check

def run_once(state, now_utc, data, notify=None, path=STATE_PATH, broker=None):
    from dts_broker import Shadow
    broker = broker or Shadow()
    if broker.live:
        notify = None          # fill_alerts.py sends the fill messages for the paper account
    s = scan_m()
    now_et = s.to_eastern(now_utc)
    day = now_et.date()
    ok, why = s.trading_day(day)
    if not ok:
        return {"status": "closed", "why": why}
    sess = s.session(day)
    close_dt = dt.datetime.combine(day, sess[1])
    if now_et < dt.datetime.combine(day, s.OPEN) or now_et > close_dt + dt.timedelta(minutes=30):
        return {"status": "outside the session"}
    rec = state["days"].setdefault(day.isoformat(), {"scan": None, "decisions": [], "telegram": 0})
    manage(state, now_utc, data, notify, path, broker)
    T = floor5(now_et)
    if rec["scan"] is None and now_et.time() >= hhmm(CFG["scan_at"]) and now_et < close_dt:
        if now_et.time() <= hhmm(CFG["scan_latest"]):
            scan_and_enter(state, day, T, s.eastern_to_utc(T), data, notify, path, broker)
        else:
            rec["scan"] = {"status": "skipped", "why": "first run after %02d:%02d NY; no late scans" % CFG["scan_latest"]}
        if state["positions"]:
            manage(state, now_utc, data, notify, path, broker)
    if not broker.live:
        L.add_curve(state, now_utc, L.equity_of(state))
    broker.finalize(state, now_utc)
    L.save(state, path)
    return {"status": "ok", "broker": broker.name, "equity": state["equity"], "open": len(state["positions"]), "trades": len(state["trades"])}


# ---------------------------------------------------------------- CLI

def telegram_notify(text: str) -> bool:
    import telegram
    return telegram.send(text)


def replay(date: str, step: int, path, data=None, notify=None):
    """Dry run of one past session on a fresh $25,000 ledger: scan at 10:15, then a check every `step` minutes."""
    s = scan_m()
    day = dt.date.fromisoformat(date)
    data = data or Yahoo()
    state = L.new_state()
    t = dt.datetime.combine(day, dt.time(10, 15))
    end = dt.datetime.combine(day, dt.time(16, 5))
    t0 = time.time()
    while t <= end:
        run_once(state, s.eastern_to_utc(t), data, notify, path)
        t += dt.timedelta(minutes=step)
    return state, round(time.time() - t0, 1)


def summarize(state) -> str:
    lines = []
    for t in state["trades"]:
        lines.append("%s  %-6s %4d  in %s @ %.2f  out %s @ %.2f  %+8.2f  (%+.2fR)  %s" % (
            t["date"], t["symbol"], t["qty"], t["entry_time"][11:], t["entry_price"], t["exit_time"][11:],
            t["exit_price"], t["pnl"], t["r"] or 0, t["reason"]))
    lines.append("trades %d  realized %+.2f  equity %.2f" % (len(state["trades"]), sum(t["pnl"] for t in state["trades"]), state["equity"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="System B: day-trader-skill shadow strategy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--now", help='New York time, e.g. "2026-11-06 10:15"')
    r.add_argument("--state", default=str(STATE_PATH))
    r.add_argument("--no-telegram", action="store_true")
    r.add_argument("--shadow", action="store_true", help="force the shadow ledger even if the System B keys are set")
    p = sub.add_parser("replay")
    p.add_argument("--date", required=True)
    p.add_argument("--step", type=int, default=15)
    p.add_argument("--state", help="where to write the replay ledger")
    p.add_argument("--no-odds-gate", action="store_true", help="skip the time-machine gate (to see what it filters)")
    p.add_argument("--loose-gate", action="store_true", help="time-machine gate at break-even instead of the strict default")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        s = scan_m()
        state = L.load(a.state)
        now = s.parse_now(a.now)
        from dts_broker import select_broker
        out = run_once(state, now, Yahoo(), None if a.no_telegram else telegram_notify, a.state,
                       select_broker(a.shadow))
        print(json.dumps(out))
        return 0
    CFG["odds_gate"] = not a.no_odds_gate
    if a.loose_gate:
        CFG["tm_min_lean"] = 0.0
    path = a.state or str(ROOT / "results" / ("system_dts_replay_%s%s.json" % (a.date, "_nogate" if a.no_odds_gate else "_loose" if a.loose_gate else "")))
    state, secs = replay(a.date, a.step, path)
    print(summarize(state))
    print("runtime %.1fs" % secs)
    return 0


if __name__ == "__main__":
    sys.exit(main())

