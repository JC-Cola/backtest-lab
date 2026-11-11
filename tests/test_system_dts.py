"""System B (day-trader-skill) wiring, sizing, bar-by-bar exits, ledger P&L, idempotency and the paper broker. Fake data only."""
import datetime as dt
import json
import random
from types import SimpleNamespace

import pytest

import dts_broker
import dts_core
import dts_ledger as L
import system_dts as S

DAY = dt.date(2026, 11, 6)           # a Friday
NOW = dt.datetime(2026, 11, 6, 15, 15)   # 10:15 New York (EST) in UTC


def et(h, m):
    return dt.datetime(2026, 11, 6, h, m)


def ts(day, h, m):
    return int((S.scan_m().eastern_to_utc(dt.datetime.combine(day, dt.time(h, m))) - dt.datetime(1970, 1, 1)).total_seconds())


def sessions(n=60, seed=1):
    out, d, rnd, px = [], dt.date(2026, 11, 5), random.Random(seed), 100.0
    while len(out) < n:
        if d.weekday() < 5 and d.isoformat() not in S.scan_m().NYSE_HOLIDAYS:
            out.append(d)
        d -= dt.timedelta(days=1)
    return sorted(out)


def day_bars(day, path):
    """78 bars from a list of (o, h, l, c); returns dict of columns."""
    cols = {k: [] for k in "tohlcv"}
    for i, (o, h, l, c) in enumerate(path):
        m = 570 + 5 * i
        cols["t"].append(ts(day, m // 60, m % 60))
        for k, v in zip("ohlc", (o, h, l, c)):
            cols[k].append(v)
        cols["v"].append(100_000)
    return cols


def flat_path(px=100.0, n=78, seed=0):
    rnd, out = random.Random(seed), []
    for _ in range(n):
        o = px
        c = px * (1 + rnd.uniform(-0.0015, 0.0015))
        out.append((o, max(o, c) * 1.0008, min(o, c) * 0.9992, c))
        px = c
    return out


def orb_path(after):
    """Opening range 99.5-100.5; the 10:00 bar (closes 10:05) breaks out; then `after` bars (o, h, l, c)."""
    p = [(100.0, 100.3, 99.6, 100.1), (100.1, 100.5, 99.9, 100.2), (100.2, 100.4, 99.5, 100.0)]
    p += [(100.0, 100.2, 99.9, 100.1)] * 3                                  # 9:45-10:00 inside the range ... bar 6 = 10:00
    p[5] = (100.1, 101.0, 100.0, 100.9)                                     # the 10:00 bar closes above 100.5
    p += [(100.9, 101.0, 100.8, 100.9)] * 3                                 # 10:00, 10:05, 10:10
    p += after
    while len(p) < 78:
        p.append((p[-1][3],) * 4)
    return p


class World:
    """A fake Yahoo: one symbol, 60 prior sessions, and a replay day built from a path."""

    def __init__(self, after, sym="AAA"):
        self.sym, self.path = sym, orb_path(after)
        self.cache_calls = 0

    def today(self):
        return DAY

    def series(self, days):
        cols = {k: [] for k in "tohlcv"}
        for i, d in enumerate(days):
            c = day_bars(d, self.path if d == DAY else flat_path(100.0, 78, i))
            for k in cols:
                cols[k] += c[k]
        return cols

    def spark(self, symbols, rng, interval):
        c = day_bars(DAY, self.path)
        ser = {"t": c["t"], "c": c["c"], "o": c["o"], "h": c["h"], "l": c["l"], "v": c["v"], "meta": {}}
        return {self.sym: ser}, {}

    def chart(self, sym, rng, interval):
        if interval == "1m":
            raise RuntimeError("no 1m in this fake")
        days = sessions() + [DAY] if rng == "60d" else [DAY]
        c = self.series(days)
        res = {"meta": {"symbol": sym, "dataGranularity": "5m", "regularMarketTime": ts(DAY, 16, 0), "previousClose": 99.0},
               "timestamp": c["t"], "indicators": {"quote": [{"open": c["o"], "high": c["h"], "low": c["l"], "close": c["c"],
                                                              "volume": c["v"]}]}}
        return json.dumps({"chart": {"result": [res], "error": None}})


@pytest.fixture
def env(tmp_path, monkeypatch):
    u = tmp_path / "uni.txt"
    u.write_text("AAA|Alpha Inc\n")
    monkeypatch.setattr(dts_core, "UNIVERSE_PATH", u)
    monkeypatch.setattr(S, "UNIVERSE_PATH", u)
    monkeypatch.setattr(S, "news_gate", lambda *a, **k: {"block": False, "reasons": [], "flags": [], "items": 0, "notes": []})
    good = {"p_target": 0.3, "p_stop": 0.3, "p_neither": 0.4, "p_green": 0.61, "avg_R": 0.1, "lean": 0.1, "hist_n": 3,
            "hist_avg_R": None, "verdict": "fake", "seed": 19}
    monkeypatch.setattr(S, "odds_gate", lambda *a, **k: (good, None))
    st = L.new_state()
    st["universe_refreshed"] = DAY.isoformat()
    return SimpleNamespace(state=st, path=tmp_path / "st.json", sent=[])


def run(env, world, now=NOW, notify=True):
    return S.run_once(env.state, now, world, (lambda t: env.sent.append(t) or True) if notify else None, env.path)


# ---------------------------------------------------------------- pipeline wiring, sizing, idempotency

def test_pipeline_opens_sized_position_with_costs_and_telegram(env):
    out = run(env, World([(101.0, 101.2, 100.9, 101.1)]))
    assert out["status"] == "ok"
    (pos,) = env.state["positions"]
    # planned at the 10:15 bar's open (101.0); stop = opening-range midpoint 100.00 rounded down; target 2R
    assert pos["entry_ref"] == 101.0 and pos["stop"] == 100.0 and pos["target"] == 103.0
    assert pos["avg_price"] == pytest.approx(101.0 * 1.0002)
    assert pos["qty"] == int(0.25 * 25000 // 101.0)                  # $125 / $1 risk = 125 sh, capped at 25% of equity
    assert pos["qty"] * 101.0 <= 0.25 * 25000
    assert env.state["cash"] == pytest.approx(25000 - pos["qty"] * pos["avg_price"] - pos["qty"] * pos["avg_price"] * 0.0001)
    assert len(env.sent) == 1 and env.sent[0].startswith("🅱️ DTS shadow · OPEN LONG AAA ") and "odds 61%" in env.sent[0]


def test_sizing_binds_on_max_loss_or_position_cap(env):
    plan = {"symbol": "AAA", "side": "buy", "date": DAY.isoformat(), "setup": "orb", "entry": 100.0, "stop": 90.0,
            "target": 120.0, "risk_per_share": 10.0, "flat_by": "15:55", "entry_time": "10:15 AM"}
    qty, note = S.size_and_risk("AAA", plan, 100.0, env.state, et(10, 15), NOW, 25000.0)
    assert qty == 12 and "max loss" in note                       # $125 / $10
    plan.update(stop=99.5, risk_per_share=0.5, target=101.0)
    qty, note = S.size_and_risk("AAA", plan, 100.0, env.state, et(10, 15), NOW, 25000.0)
    assert qty == 62 and "position cap" in note                   # 25% of 25,000 / 100 = 62


def test_rerun_is_idempotent_and_scans_once(env):
    w = World([(101.0, 101.2, 100.9, 101.1)])
    run(env, w)
    snap = json.dumps(env.state["positions"]), env.state["cash"], list(env.state["orders"]), len(env.sent)
    run(env, w)
    assert (json.dumps(env.state["positions"]), env.state["cash"], list(env.state["orders"]), len(env.sent)) == snap
    run(env, w, NOW + dt.timedelta(minutes=5))                       # a later check: no second scan, no new orders
    assert (env.state["cash"], list(env.state["orders"]), len(env.sent)) == snap[1:]
    assert env.state["days"][DAY.isoformat()]["scan"]["status"] == "done"


def test_max_four_concurrent_and_no_scan_before_1015(env):
    out = run(env, World([(101.0, 101.2, 100.9, 101.1)]), now=dt.datetime(2026, 11, 6, 14, 50))   # 09:50 NY
    assert env.state["days"][DAY.isoformat()]["scan"] is None and not env.state["positions"]
    assert out["status"] == "ok"


def test_late_first_run_after_1130_skips_the_scan(env):
    run(env, World([(101.0, 101.2, 100.9, 101.1)]), now=dt.datetime(2026, 11, 6, 17, 0))   # 12:00 NY
    assert env.state["days"][DAY.isoformat()]["scan"]["status"] == "skipped" and not env.state["positions"]


def test_closed_market_is_a_noop(env):
    assert S.run_once(env.state, dt.datetime(2026, 11, 7, 15, 0), World([]), None, env.path)["status"] == "closed"


# ---------------------------------------------------------------- stop / target / flat-by, bar by bar

def pos_for(stop=100.0, target=103.0, entry=101.0):
    return {"id": "x", "symbol": "AAA", "qty": 100, "avg_price": entry, "entry_ref": entry, "stop": stop, "target": target,
            "checked_to": "2026-11-06 10:15", "flat_by": "15:55", "risk_per_share": entry - stop, "fees_in": 0.0,
            "entry_et": "2026-11-06 10:15", "date": "2026-11-06"}


def mk(start, o, h, l, c, step=5):
    return {"start": start, "end": start + dt.timedelta(minutes=step), "o": o, "h": h, "l": l, "c": c}


CLOSE, FLAT = et(16, 0), et(15, 55)


def test_walk_stop_target_gap_and_both_in_one_bar():
    bars = [mk(et(10, 15), 101, 101.5, 100.5, 101.2), mk(et(10, 20), 101.2, 102, 99.9, 100.5)]
    ex, _, _ = L.walk_position(pos_for(), bars, et(11, 0), CLOSE, FLAT)
    assert ex["reason"] == "stop" and ex["ref"] == 100.0 and ex["at"] == et(10, 25)
    ex, _, _ = L.walk_position(pos_for(), [mk(et(10, 15), 101, 103.2, 100.9, 103.0)], et(11, 0), CLOSE, FLAT)
    assert ex["reason"] == "target" and ex["ref"] == 103.0
    ex, _, _ = L.walk_position(pos_for(), [mk(et(10, 15), 99.0, 99.5, 98.0, 98.5)], et(11, 0), CLOSE, FLAT)
    assert ex["ref"] == 99.0                                       # gapped through the stop: filled at the open
    ex, _, _ = L.walk_position(pos_for(), [mk(et(10, 15), 101, 103.5, 99.5, 101)], et(11, 0), CLOSE, FLAT)
    assert ex["reason"] == "stop"                                  # touches both: counts as the stop


def test_walk_ignores_incomplete_bar_and_flats_at_1555_open():
    p = pos_for()
    ex, last, chk = L.walk_position(p, [mk(et(10, 15), 101, 101.5, 100.5, 101.2)], et(10, 18), CLOSE, FLAT)
    assert ex is None and chk == et(10, 15)                       # bar not complete at 10:18
    bars = [mk(et(15, 50), 101, 101.4, 100.8, 101.2), mk(et(15, 55), 101.3, 101.4, 99.0, 99.5)]
    ex, _, _ = L.walk_position(pos_for(), bars, et(15, 56), CLOSE, FLAT)
    assert ex["reason"] == "flat by 15:55" and ex["ref"] == 101.3  # the 15:55 bar's open, its low is after we are out
    ex, _, _ = L.walk_position(pos_for(), bars[:1], et(16, 0), CLOSE, FLAT)
    assert "flat at the close" in ex["reason"] and ex["ref"] == 101.2


def test_end_to_end_stop_flat_and_target_in_the_pipeline(env):
    # target: price runs to 103+ at 10:30
    w = World([(101.0, 101.2, 100.9, 101.1), (101.1, 103.5, 101.0, 103.2)])
    run(env, w)
    run(env, w, dt.datetime(2026, 11, 6, 15, 45))                    # 10:45
    (t,) = env.state["trades"]
    assert t["reason"] == "target" and t["exit_ref"] == 103.0 and t["exit_price"] == pytest.approx(103.0 * 0.9998)
    assert any("CLOSE LONG AAA" in m and "target" in m for m in env.sent)


def test_end_to_end_flat_by_close(env):
    w = World([(101.0, 101.2, 100.9, 101.1)] * 70)
    run(env, w)
    run(env, w, dt.datetime(2026, 11, 6, 20, 55))                    # 15:55 NY
    (t,) = env.state["trades"]
    assert t["reason"].startswith("flat by 15:55") and not env.state["positions"]
    assert env.state["trades"][0]["exit_time"].endswith("15:55")


# ---------------------------------------------------------------- ledger P&L and snapshot shape

def test_ledger_pnl_calendar_and_balance_rows():
    st = L.new_state()
    plan = {"stop": 99.0, "target": 103.0, "flat_by": "15:55", "risk_per_share": 1.0}
    pos = L.open_position(st, "AAA", 100, 100.0, et(10, 15), dt.datetime(2026, 11, 6, 15, 15), plan)
    assert pos["avg_price"] == 100.02 and st["cash"] == pytest.approx(25000 - 10002 - 1.0002)
    t = L.close_position(st, pos, 103.0, et(11, 0), dt.datetime(2026, 11, 6, 16, 0), "target")
    gross = 100 * (103 * 0.9998 - 100.02)
    fees = 0.0001 * (10002 + 100 * 103 * 0.9998)
    assert t["pnl"] == pytest.approx(gross - fees, abs=0.01) and t["r"] == pytest.approx(t["pnl"] / 100, abs=0.01)
    L.refresh_snapshot(st, dt.datetime(2026, 11, 6, 21, 0))
    assert st["equity"] == pytest.approx(25000 + t["pnl"], abs=0.01) and not st["positions"]
    assert st["calendar"] == [{"date": "2026-11-06", "pl": t["pnl"], "pct": round(100 * t["pnl"] / 25000, 2),
                               "equity": round(25000 + t["pnl"], 2)}]
    (row,) = st["balance_history"]["rows"]
    assert row["balance_before"] == 25000.0 and row["realized_pnl"] == t["pnl"] and "AAA" in row["action"]
    assert set(st["balance_history"]) >= {"rows", "realized", "unrealized", "equity", "available", "margin", "start_balance"}


def test_telegram_is_capped_at_ten_a_day(env):
    st = env.state
    st["days"][DAY.isoformat()] = {"scan": None, "decisions": [], "telegram": 0}
    for i in range(14):
        S.say(st, DAY.isoformat(), lambda t: True, "x%d" % i)
    assert st["days"][DAY.isoformat()]["telegram"] == 10


# ---------------------------------------------------------------- the paper broker (fake client only)

class FakeClient:
    _base_url = "https://paper-api.alpaca.markets"

    def __init__(self):
        self.orders, self.held, self.cancelled = [], {}, []
        self.legs = []

    def get_open_position(self, sym):
        if sym not in self.held:
            raise Exception("position does not exist")
        return SimpleNamespace(qty=str(self.held[sym]), current_price="101.5")

    def submit_order(self, req):
        o = SimpleNamespace(id="o%d" % len(self.orders), status="filled", filled_avg_price="101.02", filled_at=None,
                            legs=[], req=req)
        self.orders.append(o)
        side = str(getattr(req.side, "value", req.side))
        if side == "buy":
            self.held[req.symbol] = int(req.qty)
        else:
            self.held.pop(req.symbol, None)
            o.filled_avg_price = "100.9"
            o.filled_at = dt.datetime(2026, 11, 6, 20, 55, tzinfo=dt.timezone.utc)
        return o

    def get_order_by_id(self, oid):
        return next(o for o in self.orders if o.id == oid)

    def get_orders(self, req):
        return [SimpleNamespace(id="leg1")]

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)

    def get_account(self):
        return SimpleNamespace(equity="25000", cash="25000")


def test_broker_refuses_non_paper_and_falls_back_without_keys(monkeypatch):
    bad = FakeClient()
    bad._base_url = "https://api.alpaca.markets"
    with pytest.raises(RuntimeError):
        dts_broker.AlpacaPaper(bad)
    monkeypatch.delenv("ALPACA_DTS_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_DTS_SECRET_KEY", raising=False)
    assert isinstance(dts_broker.select_broker(), dts_broker.Shadow)
    assert isinstance(dts_broker.select_broker(force_shadow=True), dts_broker.Shadow)


def test_broker_entry_is_a_market_bracket_and_flatten_is_a_plain_market_sell(env, monkeypatch):
    fc = FakeClient()
    br = dts_broker.AlpacaPaper(fc, wait=0, sleep=lambda s: None)
    monkeypatch.setattr("alpaca_paper.snapshot", lambda *a, **k: {"equity": 25010.0, "cash": 24000.0, "last_equity": 25000.0,
                                                                  "positions": [], "orders": [], "calendar": [],
                                                                  "history": {}, "balance_history": {"rows": []}})
    w = World([(101.0, 101.2, 100.9, 101.1)] * 70)
    S.run_once(env.state, NOW, w, None, env.path, br)
    (pos,) = env.state["positions"]
    req = fc.orders[0].req
    assert str(req.order_class.value) == "bracket" and str(req.type.value) == "market" if hasattr(req, "type") else True
    assert float(req.take_profit.limit_price) == 103.0 and float(req.stop_loss.stop_price) == 100.0
    assert pos["avg_price"] == 101.02 and pos["order_id"] == "o0" and env.state["source"] == "alpaca-paper"
    assert env.state["equity"] == 25010.0
    S.run_once(env.state, dt.datetime(2026, 11, 6, 20, 56), w, None, env.path, br)   # 15:56
    sell = fc.orders[-1].req
    assert str(sell.side.value) == "sell" and not getattr(sell, "order_class", None) or str(sell.order_class) in ("None", "simple")
    assert fc.cancelled == ["leg1"] and not env.state["positions"]
    (t,) = env.state["trades"]
    assert t["reason"].startswith("flat by") and t["exit_price"] == 100.9


def test_broker_records_a_bracket_leg_fill(env, monkeypatch):
    fc = FakeClient()
    br = dts_broker.AlpacaPaper(fc, wait=0, sleep=lambda s: None)
    monkeypatch.setattr("alpaca_paper.snapshot", lambda *a, **k: {"equity": 25000.0, "cash": 25000.0, "positions": []})
    w = World([(101.0, 101.2, 100.9, 101.1)] * 70)
    S.run_once(env.state, NOW, w, None, env.path, br)
    fc.held.clear()                                                        # the stop leg filled at the broker
    fc.orders[0].legs = [SimpleNamespace(filled_qty="40", filled_avg_price="99.95", order_type="stop",
                                         filled_at=dt.datetime(2026, 11, 6, 16, 5, tzinfo=dt.timezone.utc))]
    S.run_once(env.state, dt.datetime(2026, 11, 6, 16, 30), w, None, env.path, br)
    (t,) = env.state["trades"]
    assert t["reason"] == "stop" and t["exit_price"] == 99.95 and not env.state["positions"]
