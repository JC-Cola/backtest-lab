"""System C (TradingAgents): rating -> position mapping, shadow ledger, watchlist, failures, paper broker, auth seed,
backtest plumbing. No network, no model calls, no orders."""
import base64
import json
from types import SimpleNamespace

import pytest

import system_ta as S
import system_ta_backtest as B
import ta_broker as TB
import ta_ledger as L


def mkbars(opens, closes=None, sym_dates=None):
    dates = sym_dates or [f"2026-11-{d:02d}" for d in range(2, 2 + len(opens))]
    closes = closes or opens
    return {d: (d, o, c) for d, o, c in zip(dates, opens, closes)}


def dec(day, sym, rating, why="because"):
    return L.make_decision(day, sym, rating, why)


# ---------- mapping ----------
def test_rating_targets():
    assert [L.target_for(r) for r in ("Buy", "Overweight", "Hold", "Underweight", "Sell")] == [0.2, 0.1, None, -0.1, -0.2]
    assert L.target_for("buy") == 0.2 and L.target_for("REVIEW") is None and L.target_for(None) is None


def test_parse_decision():
    t = "**Rating**: Sell\n\n**Executive Summary**: Exit now.\nSecond line.\n\n**Investment Thesis**: x\n\n**Price Target**: 5\n\n**Time Horizon**: 3-6 months"
    assert L.parse_decision(t) == ("Exit now. Second line.", "3-6 months")
    assert L.parse_decision("nothing") == ("", None)
    g = "**Rating**: Hold\n\n### Executive Summary\nThe debate is balanced.\nKeep watching.\n\n**Action**: wait\n\n### Investment Thesis\nx\n\n**Time Horizon**: 1-2 weeks"
    assert L.parse_decision(g) == ("The debate is balanced. Keep watching.", "1-2 weeks")


# ---------- ledger ----------
def test_long_entry_next_open_costs_and_exit():
    bars = {"SPY": mkbars([100, 100, 110, 110], [100, 100, 110, 110])}
    out = L.replay([dec("2026-11-02", "SPY", "Buy"), dec("2026-11-04", "SPY", "Sell")], bars)
    # Buy decided 11-02 -> fills 11-03 open at 100*(1+0.0002); qty = 0.2*25000/100 = 50
    assert out["trades"] == [] or out["trades"][0]["entry_date"] == "2026-11-03"
    t = out["trades"][0]
    assert t["entry_px"] == pytest.approx(100.02) and t["exit_date"] == "2026-11-05" and t["exit_reason"] == "re-decided"
    gross = (t["exit_px"] - t["entry_px"]) * 50
    fees = 0.0001 * 50 * (100.02 + t["exit_px"])
    assert t["pnl"] == pytest.approx(gross - fees, abs=0.02)
    # the Sell then opened a short from 11-05 open; marked at 11-05 close
    assert out["positions"][0]["side"] == "short" and out["positions"][0]["qty"] == -45.0 or out["positions"][0]["side"] == "short"
    assert out["balance_history"]["rows"][0]["action"].startswith("Close long position for symbol SPY")


def test_short_profit_and_hold_keeps_position():
    bars = {"SPY": mkbars([100, 100, 90, 90, 90], [100, 100, 90, 90, 90])}
    out = L.replay([dec("2026-11-02", "SPY", "Sell"), dec("2026-11-03", "SPY", "Hold"), dec("2026-11-05", "SPY", "Buy")], bars)
    short = out["trades"][0]
    assert short["side"] == "short" and short["pnl"] > 0 and short["entry_date"] == "2026-11-03"
    assert short["exit_date"] == "2026-11-06"          # Buy of 11-05 fills at the 11-06 open
    assert out["positions"][0]["side"] == "long"


def test_same_tier_redecision_does_not_trade_again():
    bars = {"SPY": mkbars([100] * 5)}
    out = L.replay([dec("2026-11-02", "SPY", "Buy"), dec("2026-11-03", "SPY", "Buy"), dec("2026-11-04", "SPY", "Buy")], bars)
    assert out["trades"] == [] and len(out["positions"]) == 1


def test_exit_decision_and_idempotent_replay():
    bars = {"SPY": mkbars([100, 100, 100, 100])}
    ds = [dec("2026-11-02", "SPY", "Buy"), L.make_decision("2026-11-03", "SPY", "Exit", "off list", source="rotation")]
    a = L.replay(ds, bars)
    b = L.replay(ds + ds, bars)           # duplicated decision ids change nothing
    assert a == b and a["positions"] == [] and len(a["trades"]) == 1 and a["trades"][0]["exit_reason"] == "exit"
    assert a["equity"] < L.START_EQUITY    # round trip at flat prices costs money


def test_decision_waits_for_missing_bar():
    bars = {"SPY": mkbars([100, 100, 100, 100]), "NVDA": {"2026-11-05": ("2026-11-05", 50, 50)}}
    out = L.replay([dec("2026-11-02", "NVDA", "Buy")], bars)
    assert out["positions"][0]["symbol"] == "NVDA" and out["positions"][0]["entry_date"] == "2026-11-05"


def test_last_decision_stays_pending_without_next_bar():
    bars = {"SPY": mkbars([100, 100])}
    out = L.replay([dec("2026-11-03", "SPY", "Buy")], bars)
    assert out["positions"] == [] and out["pending"][0]["symbol"] == "SPY" and out["equity"] == L.START_EQUITY


def test_calendar_and_stats():
    bars = {"SPY": mkbars([100, 100, 110, 121], [100, 105, 110, 121])}
    out = L.replay([dec("2026-11-02", "SPY", "Buy")], bars)
    assert [c["date"] for c in out["calendar"]] == ["2026-11-03", "2026-11-04", "2026-11-05"]
    assert out["calendar"][-1]["equity"] == out["equity"]
    st = L.stats(out)
    assert st["total_pct"] == pytest.approx((out["equity"] / 25000 - 1) * 100, abs=0.001) and st["max_dd_pct"] <= 0


def test_message():
    m = L.nightly_message("2026-11-06", [{"symbol": "NVDA", "rating": "Sell", "rationale": "Weak <guide>", "horizon": "1 month"},
                                         {"symbol": "SPY", "rating": "Hold", "rationale": "Balanced"},
                                         {"symbol": "QQQ", "note": "auth: x"}], 25100, 0.4)
    assert m.startswith("🅲 TradingAgents 2026-11-06 · NVDA SELL · SPY HOLD · QQQ no decision")
    assert "short 20%" in m and "&lt;guide&gt;" in m and "keep position" in m and "QQQ: no decision" in m


# ---------- watchlist / failures ----------
def test_watchlist_cap_and_movers(tmp_path):
    p = tmp_path / "n.json"
    p.write_text(json.dumps({"shocks": [{"date": "2026-11-06", "symbol": "AAA", "score": 1}, {"date": "2026-11-06", "symbol": "NVDA", "score": 9},
                                        {"date": "2026-11-06", "symbol": "BBB", "score": 5}, {"date": "2026-11-05", "symbol": "OLD", "score": 99},
                                        {"date": "2026-11-06", "symbol": "CCC", "score": 3}]}))
    assert S.watchlist("2026-11-06", 5, p) == ["SPY", "QQQ", "NVDA", "BBB", "CCC"]
    assert S.watchlist("2026-11-06", 3, p) == ["SPY", "QQQ", "NVDA"]
    assert S.watchlist("2026-11-06", 5, tmp_path / "missing.json") == ["SPY", "QQQ", "NVDA"]


def test_classify():
    class ChatGPTAuthError(RuntimeError):
        pass

    class ChatGPTUnreachable(ChatGPTAuthError):
        pass

    class RateLimitError(Exception):
        pass
    assert S.classify(ChatGPTAuthError("x")) == "auth" and S.classify(ChatGPTUnreachable("x")) == "error"
    assert S.classify(RateLimitError("x")) == "limit" and S.classify(ValueError("boom")) == "error"
    try:
        try:
            raise ChatGPTAuthError("signin")
        except ChatGPTAuthError as e:
            raise RuntimeError("wrapped") from e
    except RuntimeError as e:
        assert S.classify(e) == "auth"


def fake_decide(ratings, calls=None):
    def decide(tk, day):
        if calls is not None:
            calls.append(tk)
        r = ratings[tk]
        if isinstance(r, Exception):
            raise r
        return {"rating": r, "rationale": f"{tk} view", "horizon": None, "calls": 30, "seconds": 1}
    return decide


def bars_fn(syms):
    return {s: mkbars([100, 100, 101, 102, 103]) for s in syms}


def test_run_idempotent_and_records(tmp_path):
    state, sent, calls = S.load_state(tmp_path / "x.json"), [], []
    d = fake_decide({"SPY": "Buy", "QQQ": "Hold", "NVDA": "Sell"}, calls)
    res, alert = S.run(state, "2026-11-03", ["SPY", "QQQ", "NVDA"], d, bars_fn, send=lambda t: sent.append(t) or True)
    assert alert is None and [r["rating"] for r in res] == ["Buy", "Hold", "Sell"] and calls == ["SPY", "QQQ", "NVDA"]
    eq = state["equity"]
    S.run(state, "2026-11-03", ["SPY", "QQQ", "NVDA"], d, bars_fn, send=lambda t: sent.append(t) or True)
    assert calls == ["SPY", "QQQ", "NVDA"] and state["equity"] == eq and len(state["decisions"]) == 3   # no second model run
    assert state["runs"][0]["calls"] == 90 and state["mode"] == "shadow" and state["health"]["status"] == "ok"
    assert {p["symbol"] for p in state["positions"]} == {"SPY", "NVDA"}


def test_auth_failure_no_decision_one_alert_per_day(tmp_path):
    class ChatGPTAuthError(RuntimeError):
        pass
    state, sent = S.load_state(tmp_path / "x.json"), []
    d = fake_decide({"SPY": ChatGPTAuthError("Not signed in"), "QQQ": "Buy", "NVDA": "Buy"})
    res, alert = S.run(state, "2026-11-03", ["SPY", "QQQ", "NVDA"], d, bars_fn, send=lambda t: sent.append(t) or True)
    assert alert == "auth" and all("rating" not in r for r in res) and state["decisions"] == []
    assert len(sent) == 1 and "GOOGLE_API_KEY" in sent[0] and "eyJ" not in sent[0]
    S.run(state, "2026-11-03", ["SPY"], d, bars_fn, send=lambda t: sent.append(t) or True)
    assert len(sent) == 1 and state["health"]["status"] == "auth"      # not alerted twice the same UTC day


def test_dropped_ticker_exits(tmp_path):
    state = S.load_state(tmp_path / "x.json")
    S.run(state, "2026-11-02", ["SPY", "AAA"], fake_decide({"SPY": "Hold", "AAA": "Buy"}), bars_fn)
    S.run(state, "2026-11-03", ["SPY"], fake_decide({"SPY": "Hold"}), bars_fn)
    assert any(d["source"] == "rotation" and d["symbol"] == "AAA" for d in state["decisions"])
    assert state["positions"] == [] and state["trades"][0]["exit_reason"] == "exit"


def test_main_dry_run_and_no_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(S, "PATH", tmp_path / "s.json")
    monkeypatch.setattr(S, "NEWS_PATH", tmp_path / "none.json")
    assert S.main(["--asof", "2026-11-06", "--dry-run"]) == 0
    assert "['SPY', 'QQQ', 'NVDA']" in capsys.readouterr().out
    sent = []
    assert S.main(["--asof", "2026-11-30"], decide=lambda *a: 1 / 0, send=lambda t: sent.append(t) or True, bars_fn=bars_fn) == 0
    assert sent == []                                  # no SPY bar for that day: nothing run, nothing sent


# ---------- paper broker ----------
def night(*pairs, day="2026-11-06"):
    return [L.make_decision(day, s, r) for s, r in pairs]


def test_plan_orders_open_hold_flip_exit():
    prices = {"SPY": 500.0, "NVDA": 200.0, "QQQ": 400.0}
    o = TB.plan_orders(night(("SPY", "Buy"), ("NVDA", "Sell")), [], {}, prices, 25000)
    assert o == [{"symbol": "NVDA", "side": "sell", "qty": 25.0, "note": "open"}, {"symbol": "SPY", "side": "buy", "qty": 10.0, "note": "open"}]
    hist = [L.make_decision("2026-11-05", "SPY", "Buy")]
    assert TB.plan_orders(night(("SPY", "Buy")), hist, {"SPY": 10.0}, prices, 25000) == []                    # same tier: hold
    assert TB.plan_orders(night(("SPY", "Hold")), hist, {"SPY": 10.0}, prices, 25000) == []                   # Hold: nothing
    flip = TB.plan_orders(night(("SPY", "Sell")), hist, {"SPY": 10.0}, prices, 25000)
    assert flip == [{"symbol": "SPY", "side": "sell", "qty": 10.0, "note": "flip: close first, open next night"}]
    nxt = TB.plan_orders(night(("SPY", "Sell"), day="2026-11-09"), hist + night(("SPY", "Sell")), {}, prices, 25000)
    assert nxt[0]["side"] == "sell" and nxt[0]["qty"] == 10.0                                                 # second leg opens the short
    ex = [L.make_decision("2026-11-06", "NVDA", "Exit", "x", source="rotation")]
    assert TB.plan_orders(ex, [], {"NVDA": -5.0}, prices, 25000) == [{"symbol": "NVDA", "side": "buy", "qty": 5.0, "note": "exit"}]
    ns = TB.plan_orders(night(("NVDA", "Sell")), [], {}, prices, 25000, shortable=lambda s: False)
    assert ns == [{"symbol": "NVDA", "side": None, "qty": 0, "note": "not shortable: no short opened"}]
    assert TB.plan_orders(night(("SPY", "Overweight")), [], {}, {"SPY": 5000.0}, 25000) == []                 # below the $50 floor / 0 shares


class FakeClient:
    def __init__(self, number="PA123", positions=(), open_orders=()):
        self.number, self.positions, self.open_orders = number, positions, list(open_orders)
        self.submitted, self.cancelled = [], []

    def get_account(self):
        return SimpleNamespace(account_number=self.number, equity="25000")

    def get_all_positions(self):
        return list(self.positions)

    def get_orders(self, *a, **k):
        return self.open_orders

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)

    def submit_order(self, req):
        self.submitted.append(req)
        return SimpleNamespace(status="accepted", id="o1")

    def get_asset(self, s):
        return SimpleNamespace(shortable=s != "BAD")


def test_client_from_env_paper_only():
    assert TB.client_from_env({}) is None and TB.client_from_env({TB.KEY: "k"}) is None
    env = {TB.KEY: "k", TB.SECRET: "s"}
    assert TB.client_from_env(env, factory=lambda k, s: FakeClient()).number == "PA123"
    with pytest.raises(RuntimeError, match="not an Alpaca PAPER"):
        TB.client_from_env(env, factory=lambda k, s: FakeClient(number="123456789"))


def test_place_day_orders_cancels_own_only():
    c = FakeClient(open_orders=[SimpleNamespace(client_order_id="ta-old-SPY", id="a"), SimpleNamespace(client_order_id="lab-x", id="b")])
    rows = TB.place(c, [{"symbol": "SPY", "side": "buy", "qty": 10.0, "note": "open"}, {"symbol": "X", "side": None, "qty": 0, "note": "n"}], "202611062115")
    assert c.cancelled == ["a"] and rows[0]["status"] == "accepted" and rows[1]["status"] == "skipped"
    r = c.submitted[0]
    assert str(r.time_in_force.value) == "day" and r.client_order_id == "ta-202611062115-SPY" and r.qty == 10.0


def test_held_qty_short_sign():
    c = FakeClient(positions=[SimpleNamespace(symbol="A", qty="-5", side="short"), SimpleNamespace(symbol="B", qty="3", side="long")])
    assert TB.held_qty(c) == {"A": -5.0, "B": 3.0}


def test_paper_mode_run_uses_account_ledger(tmp_path, monkeypatch):
    snap = {"equity": 25200.0, "cash": 100.0, "positions": [{"symbol": "SPY", "qty": 10.0, "avg_price": 500.0}],
            "orders": [], "calendar": [{"date": "2026-11-05", "pl": 5.0, "pct": 0.02, "equity": 25200.0}],
            "balance_history": {"rows": [{"time": "2026-11-05 09:30:00", "balance_before": 25000, "balance_after": 25010, "realized_pnl": 10.0,
                                          "action": "Close long position for symbol SPY at price 501 for 10 units. Position AVG Price was 500"}]},
            "strategies_by_symbol": {}}
    monkeypatch.setattr(TB, "snapshot", lambda client, snapshot_fn=None: dict(snap))
    c = FakeClient()
    state = S.load_state(tmp_path / "x.json")
    S.run(state, "2026-11-03", ["SPY", "QQQ"], fake_decide({"SPY": "Buy", "QQQ": "Hold"}), bars_fn, broker=c)
    assert state["mode"] == "paper" and state["equity"] == 25200.0 and len(c.submitted) == 1
    assert state["positions"][0]["rating"] == "Buy" and state["trades"][0]["rationale"] == "SPY view" and "shadow" in state


def test_paper_failure_falls_back_to_shadow(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("alpaca down")
    monkeypatch.setattr(TB, "plan_orders", boom)
    state = S.load_state(tmp_path / "x.json")
    S.run(state, "2026-11-03", ["SPY"], fake_decide({"SPY": "Buy"}), bars_fn, broker=FakeClient())
    assert state["mode"].startswith("paper (account step failed") and state["equity"] > 0


# ---------- backtest ----------
def test_backtest_plan_budget():
    days = [f"2026-{m:02d}-{d:02d}" for m in (7, 8, 9, 10) for d in range(1, 29)]
    dates, step = B.plan(days, ["SPY", "QQQ", "NVDA"], "2026-07-02", "2026-10-28", 1, max_decisions=60)
    assert len(dates) * 3 <= 60 and step > 1 and dates[0] >= "2026-07-02"


def test_backtest_collect_resumable_and_build():
    bars = {s: mkbars([100 + i for i in range(8)]) for s in ("SPY", "QQQ")}
    dates = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    seen = []
    entries = [{"ticker": t, "date": d, "rating": r, "decision": f"**Executive Summary**: {t} {d}.\n\n**Time Horizon**: 1 month"}
               for d, r in zip(dates, ["Buy", "Sell", "Hold", "Overweight"]) for t in ("SPY", "QQQ")]
    ds = B.collect(["SPY", "QQQ"], dates, "unused", chunk=3, runner=lambda tk, part: seen.append(part), loader=lambda p: entries, log=lambda *_: None)
    assert seen == [dates[:3], dates[3:]] and len(ds) == 8 and ds[1]["rationale"] == "SPY 2026-11-02." and ds[0]["horizon"] == "1 month"
    res = B.build(ds, bars, ["SPY", "QQQ"], cutoff="2026-11-03", trials=20)
    assert res["clean"]["decisions"] == 4 and res["possibly_contaminated"]["decisions"] == 4 and res["all_dates"]["decisions"] == 8
    cf = res["clean"]["coin_flip"]
    assert cf["trials"] == 20 and 0 <= cf["share_of_trials_beating_system"] <= 1 and set(res["clean"]) >= {"buy_hold_equal", "buy_hold_spy", "system"}
    assert B.build(ds, bars, ["SPY", "QQQ"], "2026-11-03", 20)["clean"]["coin_flip"] == cf      # seeded: reproducible


def test_backtest_main_dry_run(tmp_path, capsys):
    bars = lambda syms: {s: mkbars([100] * 30, sym_dates=[f"2026-11-{d:02d}" for d in range(1, 31)]) for s in syms}
    assert B.main(["--start", "2026-11-01", "--end", "2026-11-30", "--cutoff", "2026-10-01", "--dry-run"], bars_fn=bars) == 0
    out = capsys.readouterr().out
    assert "decisions" in out and "model calls" in out


# ---------- Gemini free tier ----------
def test_classify_gemini_errors():
    class ClientError(Exception):
        def __init__(self, code, msg):
            super().__init__(msg)
            self.code = code
    assert S.classify(ClientError(429, "RESOURCE_EXHAUSTED: You exceeded your current quota")) == "limit"
    assert S.classify(ClientError(400, "API key not valid. Please pass a valid API key.")) == "auth"
    assert S.classify(ClientError(503, "model is currently experiencing high demand")) == "error"
    assert "GOOGLE_API_KEY" in S.alert_text("auth", "2026-11-06") and "quota" in S.alert_text("limit", "2026-11-06")


def test_rpm_margin_and_env(monkeypatch):
    assert S.rpm_for("gemini-3.5-flash-lite") == pytest.approx(12.0) and S.rpm_for("gemini-3.5-flash") == pytest.approx(4.0)
    monkeypatch.setenv("TA_RPM_GEMINI_3_5_FLASH", "2")
    assert S.rpm_for("gemini-3.5-flash") == 2.0 and S.rpm_for("unknown-model") == pytest.approx(4.0)


def test_ta_config_and_limiter_share_per_model():
    pytest.importorskip("tradingagents")
    cfg = S.ta_config()
    assert cfg["llm_provider"] == "google" and cfg["quick_think_llm"] == "gemini-3.5-flash-lite" and cfg["deep_think_llm"] == "gemini-3.1-flash-lite"
    S.install_rate_limits()
    S.install_rate_limits()   # idempotent
    from tradingagents.llm_clients.google_client import GoogleClient
    a = GoogleClient("gemini-3.5-flash-lite", api_key="x").get_llm()
    b = GoogleClient("gemini-3.5-flash-lite", api_key="x").get_llm()
    c = GoogleClient("gemini-3.5-flash", api_key="x").get_llm()
    assert a.rate_limiter is b.rate_limiter and a.rate_limiter is not c.rate_limiter


def test_backtest_budget_and_quota_stop():
    dates = [f"2026-11-{d:02d}" for d in range(2, 12)]
    store = []

    def runner(tk, part):
        for d in part:
            for t in tk:
                store.append({"ticker": t, "date": d, "rating": "Buy", "decision": ""})
    logs = []
    B.collect(["SPY", "QQQ"], dates, "u", chunk=2, runner=runner, loader=lambda p: list(store), log=logs.append, budget=6)
    assert len(store) == 8 and any("budget" in x for x in logs)         # stops after the chunk that reaches the budget

    class R:
        failures = [("SPY", "2026-11-02", "429 RESOURCE_EXHAUSTED exceeded your current quota")]
    calls = []
    B.collect(["SPY"], dates, "u", chunk=2, runner=lambda tk, p: calls.append(p) or R(), loader=lambda p: [], log=logs.append)
    assert len(calls) == 1 and any("quota" in x for x in logs)
