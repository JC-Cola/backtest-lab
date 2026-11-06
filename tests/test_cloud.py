"""Cloud scheduler safety (plan_once), the daily report, and the workflow files."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import live_noise as ln
import report as rp

NY = ln.NY
DAY = date(2026, 11, 3)
PTS = ln.schedule(datetime(2026, 11, 3, 9, 0, tzinfo=NY))


def at(h, m):
    return datetime(2026, 11, 3, h, m, 30, tzinfo=NY)


def st(done=(), qty=0, moc=False):
    return {"date": str(DAY), "qty": qty, "done": list(done), "moc": moc}


def plan(now, states, held=None):
    return ln.plan_once(now, PTS, states, held or {})


# ---------- plan_once ----------
def test_nothing_before_ten():
    assert plan(at(9, 45), {"SPY": st(), "QQQ": st()}) == []


def test_first_run_acts_then_repeat_is_a_noop():
    first = plan(at(10, 3), {"SPY": st(), "QQQ": st()})
    assert [(a["sym"], a["kind"], a["point"]) for a in first] == [("SPY", "check", "10:00"), ("QQQ", "check", "10:00")]
    assert plan(at(10, 33), {"SPY": st(["10:00"]), "QQQ": st(["10:00"])}) == []


def test_late_run_acts_on_latest_point_only():
    a = plan(at(12, 18), {"SPY": st(["10:00"])})
    assert [(x["kind"], x["point"]) for x in a] == [("check", "12:00")]


def test_last_check_is_a_plain_check():
    a = plan(at(15, 12), {"SPY": st(["14:00"], qty=10)}, {"SPY": 10})
    assert a == [{"sym": "SPY", "kind": "check", "point": "15:00"}]
    assert plan(at(15, 30), {"SPY": st(["15:00"], qty=10)}, {"SPY": 10}) == []


def test_flatten_at_1556_is_a_market_order_for_the_held_qty():
    a = plan(at(15, 56), {"SPY": st(["15:00"], qty=-16)}, {"SPY": -16})
    assert a == [{"sym": "SPY", "kind": "flatten_market", "point": "15:55", "qty": -16}]


def test_nothing_after_1600_and_no_double_close():
    s = {"SPY": st(["15:00"], qty=7)}
    assert plan(at(16, 1), s, {"SPY": 7}) == []
    closed = {"SPY": st(["15:00"], qty=0, moc=True)}
    assert plan(at(15, 57), closed, {}) == []
    assert not any(x["kind"] == "flatten_market" for x in plan(at(15, 57), closed, {"SPY": 7}))  # order pending: log only


def test_held_but_state_zero_at_flatten_closes_held():
    a = plan(at(15, 56), {"SPY": st(["15:00"], qty=0)}, {"SPY": -19})
    assert a == [{"sym": "SPY", "kind": "flatten_market", "point": "15:55", "qty": -19}]


def test_state_without_held_is_only_logged():
    a = plan(at(15, 56), {"SPY": st(["15:00"], qty=7)}, {})
    assert a == [{"sym": "SPY", "kind": "mismatch", "point": "15:55", "held": 0, "state": 7}]
    b = plan(at(12, 5), {"SPY": st(["12:00"], qty=7)}, {"SPY": 5})
    assert b == [{"sym": "SPY", "kind": "mismatch", "point": "12:00", "held": 5, "state": 7}]


def test_leftover_position_triggers_safety_flatten():
    a = plan(at(11, 5), {"SPY": st(), "QQQ": st(["10:00"], qty=-3)}, {"SPY": 12, "QQQ": -3})
    assert {"sym": "SPY", "kind": "safety", "point": ""} in a
    assert not any(x["sym"] == "QQQ" and x["kind"] == "safety" for x in a)  # QQQ's state holds it: today's position
    # a position opened today under the old state format (no `done` list) is today's, not a leftover
    assert not any(x["kind"] == "safety" for x in plan(at(13, 5), {"SPY": st(qty=16)}, {"SPY": 16}))
    assert [x["kind"] for x in a if x["sym"] == "SPY"] == ["safety", "check"]
    assert plan(at(9, 40), {"SPY": st()}, {"SPY": 5}) == [{"sym": "SPY", "kind": "safety", "point": ""}]


# ---------- live_noise executors (fake Alpaca) ----------
class FakeTrading:
    class _A:
        equity = "25000"
    def get_account(self):
        return self._A()

    def get_order_by_id(self, oid):  # entry orders fill at once in the fake
        class _O:
            status = "OrderStatus.FILLED"
        return _O()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ln, "state_path", lambda sym: tmp_path / f"s_{sym}.json")
    monkeypatch.setattr(ln, "LOG", tmp_path / "noise_live.csv")
    monkeypatch.setattr(ln, "submit", lambda tr, qty, tif, sym: sent.append((sym, qty, tif)) or f"id{len(sent)}")
    idx = pd.date_range("2026-11-03 09:30", periods=90, freq="min", tz=NY)
    px = np.linspace(100, 110, 90)
    bars = pd.DataFrame({"open": px, "high": px, "low": px, "close": px, "volume": 1000.0}, index=idx)
    monkeypatch.setattr(ln, "history_bands", lambda dc, today, sym: (np.full(390, 0.001), 100.0, date(2026, 11, 2)))
    monkeypatch.setattr(ln, "today_bars", lambda dc, now, sym: bars)
    return sent


def test_last_check_sends_no_close_order(fake):
    ln.act_check(FakeTrading(), None, at(15, 5), 0.5, "SPY", "15:00")
    assert [x[2] for x in fake] == ["day"] and len(fake) == 1
    s = ln.load_state(DAY, "SPY")
    assert s["qty"] == fake[0][1] and s["moc"] is False and s["done"] == ["15:00"]


def test_flatten_market_executor_and_no_double_close(fake):
    ln.save_state("SPY", st(["15:00"], qty=9))
    ln.act_flatten(FakeTrading(), at(15, 56), "SPY", 9)
    assert fake == [("SPY", -9, "day")]
    s = ln.load_state(DAY, "SPY")
    assert s["qty"] == 0 and s["moc"] is True
    assert plan(at(15, 57), {"SPY": s}, {"SPY": 0}) == []
    ln.act_flatten(FakeTrading(), at(15, 57), "SPY", 9)  # repeat run: moc is set, nothing sent
    assert fake == [("SPY", -9, "day")]


def test_held_with_zero_state_flattens_held(fake):
    ln.save_state("QQQ", st(["15:00"], qty=0))
    acts = plan(at(15, 56), {"QQQ": ln.load_state(DAY, "QQQ")}, {"QQQ": -19})
    for x in acts:
        ln.act_flatten(FakeTrading(), at(15, 56), x["sym"], x["qty"])
    assert fake == [("QQQ", 19, "day")] and ln.load_state(DAY, "QQQ")["moc"] is True


class SafetyTrading(FakeTrading):
    def __init__(self, held, open_orders=()):
        self.held, self.open, self.cancelled = held, list(open_orders), []

    def get_orders(self, req):
        return [o for o in self.open if o.symbol in req.symbols]

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)
        self.open = [o for o in self.open if o.id != oid]

    def get_all_positions(self):
        class P:
            def __init__(s, sym, q): s.symbol, s.qty = sym, str(q)
        return [P(s, q) for s, q in self.held.items() if q]


def test_safety_executor(fake):
    ln.act_safety(SafetyTrading({"QQQ": 4}), at(10, 5), "QQQ", 4)
    assert fake[-1] == ("QQQ", -4, "day")
    assert "safety flatten (left over from an earlier day)" in ln.LOG.read_text()


def test_safety_cancels_resting_order_first(fake):
    class O:
        id, symbol = "lim1", "QQQ"
    tr = SafetyTrading({"QQQ": -19}, [O()])
    ln.act_safety(tr, at(9, 31), "QQQ", -19)
    assert tr.cancelled == ["lim1"] and fake[-1] == ("QQQ", 19, "day")


def test_safety_skips_when_resting_order_already_filled(fake):
    tr = SafetyTrading({"QQQ": 0})
    ln.act_safety(tr, at(9, 31), "QQQ", -19)
    assert fake == [] and "already closed by its resting order" in ln.LOG.read_text()


# ---------- report ----------
def t(h, m):
    return datetime(2026, 11, 3, h, m, tzinfo=NY)


def order(**k):
    o = {"id": "", "symbol": "SPY", "side": "buy", "qty": 10.0, "filled_qty": 10.0, "price": 500.0, "status": "filled",
         "submitted": t(10, 0), "filled": t(10, 0)}
    return o | k


def acct(**k):
    a = {"ok": True, "equity": 25100.0, "cash": 20000.0, "last_equity": 25000.0, "positions": [], "orders": [], "note": ""}
    return a | k


def nrow(time, sym, decision="flat", event="check", oid="", price="500", up="505", lo="495", vwap="499"):
    return {"time_ny": f"2026-11-03 {time}", "symbol": sym, "event": event, "price": price, "vwap": vwap, "upper": up, "lower": lo,
            "decision": decision, "order_id": oid}


def full_rows():
    return [nrow(f"{h}:00:20", s) for h in range(10, 16) for s in ("SPY", "QQQ")]


LESSONS = "## Last session\n\n30 rounds in 2s. Claude CLI cost this session: $0.42.\n\n## Other\n\nx\n"
CAND = {"candidates": [{"account": "intraday", "description": "rule X", "sharpe_2x_cost": 0.9, "status": "awaiting approval"},
                       {"account": "long-term", "description": "rule Y", "sharpe_2x_cost": 0.1, "status": "not better than what is traded now"}]}


def build(accts=None, noise=None, orders=None, paper=None, cands=None):
    accts = accts or {"long-term": acct(), "intraday": acct()}
    return rp.build(DAY, accts, full_rows() if noise is None else noise, orders or [], paper or [], LESSONS, LESSONS, cands)


def test_report_trades_and_why():
    rows = full_rows()
    rows[0] = nrow("10:00:20", "SPY", "long", oid="abc", price="506", up="505")
    rows.append(nrow("15:46:00", "SPY", "", event="flatten at close", oid="moc"))
    it = acct(orders=[order(id="abc"), order(id="moc", side="sell", filled=t(16, 0), price=508.0)])
    lt = acct(orders=[order(id="L1", symbol="QQQ", filled=t(9, 30), price=700.0)])
    paper = [{"key": "A", "name": "Williams %R", "market": "QQQ", "position": "1.0", "decision_date": "2026-11-02"},
             {"key": "B", "name": "Old", "market": "QQQ", "position": "1.0", "decision_date": "2026-11-01"},
             {"key": "B", "name": "Old", "market": "QQQ", "position": "0.0", "decision_date": "2026-11-02"}]
    orows = [{"sent_at_utc": "2026-11-02T21:45:00+00:00", "symbol": "QQQ", "side": "buy", "qty": "5", "tif": "opg", "target_usd": "3500",
              "order_id": "L1", "status": "OrderStatus.ACCEPTED"}]
    md = build({"long-term": lt, "intraday": it}, rows, orows, paper)
    assert "long: price 506 vs upper 505, lower 495, VWAP 499" in md
    assert "close-out at the close" in md
    assert "strategies in: Williams %R" in md and "Old" not in md.split("Why")[1].split("##")[0]
    assert "16:00" in md and "09:30" in md
    assert "+$100.00 (+0.40%)" in md


def test_report_checks_waiting_and_none_cases():
    md = build()
    assert "| 10:00 | SPY | flat |" in md          # flat decisions are visible
    sec = lambda n: md.split(f"## {n}.")[1].split("\n## ")[0]
    assert sec(5).strip().endswith("None.") and sec(2).count("None.") == 2 and sec(4).strip().endswith("None.")
    assert "None yet." in sec(6) and "Claude CLI cost this session: $0.42" in sec(6)
    lt = acct(orders=[order(status="accepted", filled=None, filled_qty=0)])
    orows = [{"sent_at_utc": "2026-11-03T21:45:00+00:00", "symbol": "SPY", "side": "sell", "qty": "1", "tif": "opg", "target_usd": "0", "order_id": "z",
              "status": "OrderStatus.ACCEPTED"}]
    md = build({"long-term": lt, "intraday": acct()}, orders=orows, cands=CAND)
    assert "Open at Alpaca" in md and "16:45" in md and "rule X" in md and "rule Y" not in md


def test_report_problems():
    rows = [r for r in full_rows() if not (r["symbol"] == "QQQ" and r["time_ny"][11:13] == "12")]
    it = acct(positions=[{"symbol": "SPY", "qty": 3.0, "avg_price": 1, "price": 1, "pl": 0}],
              orders=[order(status="rejected", filled_qty=0, filled=None)], note="ALPACA_INTRADAY_API_KEY not set; using the snapshot")
    md = build({"long-term": acct(), "intraday": it}, rows)
    p = md.split("## 5. Problems")[1].split("## 6.")[0]
    assert "no 12:00 check logged for QQQ" in p and "was rejected" in p and "still holding 3 SPY" in p and "not set" in p
    assert "None." not in p
    assert "no log rows today" in rp.problems(DAY, {"intraday": acct()}, [])[0]


def test_last_session_and_index(tmp_path):
    assert rp.last_session(LESSONS).startswith("30 rounds") and "Other" not in rp.last_session(LESSONS)
    (tmp_path / "2026-11-02.md").write_text("<!-- pl: long-term=1.5 intraday= -->\n")
    (tmp_path / "2026-11-03.md").write_text("<!-- pl: long-term=-2.0 intraday=4.25 -->\n")
    rp.index(tmp_path)
    lines = (tmp_path / "README.md").read_text().splitlines()
    assert lines[-2].startswith("- [2026-11-03]") and "intraday +$4.25" in lines[-2] and "long-term -$2.00" in lines[-2]
    assert "intraday n/a" in lines[-1]


def test_main_without_keys_uses_snapshots(tmp_path, monkeypatch):
    mem = tmp_path / "mem"
    mem.mkdir()
    snap = {"generated": "2026-11-03T21:00:00+00:00", "equity": 25200.0, "cash": 1.0, "last_equity": 25000.0, "positions": [],
            "orders": [{"date": "2026-11-03T15:00+00:00", "symbol": "SPY", "side": "buy", "qty": 2.0, "status": "filled", "price": 500.0}]}
    (mem / "s.json").write_text(json.dumps(snap))
    monkeypatch.setattr(rp, "MEM", mem)
    monkeypatch.setattr(rp, "OUT", tmp_path / "reports")
    monkeypatch.setattr(rp, "ACCOUNTS", [("long-term", "NOPE_K", "NOPE_S", mem / "s.json"), ("intraday", "NOPE_K2", "NOPE_S2", mem / "missing.json")])
    md = rp.main("2026-11-03")
    assert (tmp_path / "reports" / "2026-11-03.md").exists() and (tmp_path / "reports" / "README.md").exists()
    assert "using the snapshot saved" in md and "no snapshot file either" in md and "| SPY | buy | 2 |" in md


# ---------- workflows ----------
WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def load_wf(name):
    text = (WF / name).read_text()
    try:
        import yaml
    except ImportError:
        return text, None
    d = yaml.safe_load(text)
    return text, d


def test_intraday_workflow():
    text, d = load_wf("intraday.yml")
    assert "schedule:" not in text and "workflow_dispatch" in text  # timer: Supabase pg_cron bl-intraday
    assert "group: backtest-lab-trading" in text and "cancel-in-progress: false" in text and "contents: write" in text
    assert "python live_noise.py --once" in text and "secrets.ALPACA_INTRADAY_API_KEY" in text and "secrets.ALPACA_INTRADAY_SECRET_KEY" in text
    assert "secrets.ALPACA_API_KEY" not in text
    assert "if: failure()" in text and "secrets.TELEGRAM_BOT_TOKEN" in text and "secrets.TELEGRAM_CHAT_ID" in text
    assert "github.run_id" in text and text.rindex("if: failure()") > text.index("Commit log and state") and "|| true" in text
    if d:
        assert d["concurrency"]["cancel-in-progress"] is False and "schedule" not in d[True]


def test_nightly_workflow():
    text, d = load_wf("nightly.yml")
    assert "schedule:" not in text and "workflow_dispatch" in text and "group: backtest-lab-trading" in text
    for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_INTRADAY_API_KEY", "ALPACA_INTRADAY_SECRET_KEY"):
        assert f"secrets.{name}" in text
    for s in ("python intraday_data.py SPY QQQ", "python daily.py", "python report.py",
              "gh issue create", "daily-report", "issues: write", "contents: write", "restore-keys", "actions/cache@v4"):
        assert s in text
    assert "python flat_guard.py" in text and "if: failure()" in text and "secrets.TELEGRAM_CHAT_ID" in text and "github.run_id" in text
    assert text.index("daily.py") < text.index("report.py") < text.index("gh issue create")
    if d:
        assert "rounds" not in d[True]["workflow_dispatch"]["inputs"]


def test_requirements_cover_imports():
    req = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text().split()
    for p in ("yfinance", "pandas", "numpy", "pytest", "alpaca-py"):
        assert p in req
    assert "anthropic" not in req


def test_no_safety_flatten_after_the_day_was_closed():
    now = at(15, 31)
    acts = ln.plan_once(now, ln.schedule(now), {"SPY": {"qty": 0, "done": ["15:00"], "moc": True}}, {"SPY": -16})
    assert [a["kind"] for a in acts] == ["mismatch"]


# ---------- flat_guard ----------
def test_flat_guard_messages(monkeypatch):
    import flat_guard as fg
    assert fg.message({}, []) is None
    assert fg.message({"SPY": -16, "QQQ": -19}, []) == ("⚠️ Intraday account is NOT flat after the close: QQQ -19, SPY -16. "
                                                      "They will be closed at the 9:31 check.")
    assert "Open orders: buy 3 SPY." in fg.message({}, ["buy 3 SPY"])


def test_flat_guard_main_sends_only_when_not_flat():
    import flat_guard as fg
    from types import SimpleNamespace as NS
    sent = []

    class T:
        def __init__(self, pos):
            self.pos = pos
        def get_all_positions(self):
            return [NS(symbol=s, qty=str(q)) for s, q in self.pos.items()]
        def get_orders(self, req):
            return []
    assert fg.main(T({}), sent.append) is None and sent == []
    assert "QQQ -19, SPY -16" in fg.main(T({"SPY": -16, "QQQ": -19}), sent.append) and len(sent) == 1

    class Boom(T):
        def get_all_positions(self):
            raise RuntimeError("api down")
    assert fg.main(Boom({}), sent.append) is None and len(sent) == 1  # never raises
