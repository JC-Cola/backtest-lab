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
    assert [(x["kind"], x["point"], x["last"]) for x in a] == [("check", "12:00", False)]


def test_last_check_flags_close_order():
    a = plan(at(15, 12), {"SPY": st(["14:00"], qty=10)})
    assert a == [{"sym": "SPY", "kind": "check", "point": "15:00", "last": True}]
    assert plan(at(15, 30), {"SPY": st(["15:00"], moc=True)}) == []


def test_backstop_before_and_after_1550():
    s = {"SPY": st(["15:00"], qty=7)}  # 15:00 check ran but the close order was not recorded
    assert plan(at(15, 47), s)[0]["kind"] == "flatten_moc"
    assert plan(at(15, 52), s)[0]["kind"] == "flatten_market"
    assert plan(at(16, 1), s) == []
    assert plan(at(15, 47), {"SPY": st(["15:00"], qty=7, moc=True)}) == []
    assert plan(at(15, 47), {"SPY": st(["15:00"], qty=0)}) == []


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


def test_last_check_submits_moc_and_marks_state(fake):
    ln.act_check(FakeTrading(), None, at(15, 5), 0.5, "SPY", "15:00", True)
    assert fake[0][2] == "day" and fake[1][2] == "cls" and fake[1][1] == -fake[0][1]
    s = ln.load_state(DAY, "SPY")
    assert s["qty"] == 0 and s["moc"] is True and s["done"] == ["15:00"]
    assert plan(at(15, 20), {"SPY": s}) == []


def test_backstop_market_and_safety_executors(fake):
    ln.save_state("SPY", st(["15:00"], qty=9))
    ln.act_flatten(FakeTrading(), at(15, 52), "SPY", "flatten_market")
    assert fake == [("SPY", -9, "day")] and ln.load_state(DAY, "SPY")["qty"] == 0
    ln.act_safety(FakeTrading(), at(10, 5), "QQQ", 4)
    assert fake[-1] == ("QQQ", -4, "day")
    assert "safety flatten (left over from an earlier day)" in ln.LOG.read_text()


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
    assert '"1,31 13-20 * * 1-5"' in text and "workflow_dispatch" in text
    assert "group: backtest-lab-trading" in text and "cancel-in-progress: false" in text and "contents: write" in text
    assert "python live_noise.py --once" in text and "secrets.ALPACA_INTRADAY_API_KEY" in text and "secrets.ALPACA_INTRADAY_SECRET_KEY" in text
    assert "secrets.ALPACA_API_KEY" not in text
    if d:
        assert d["concurrency"]["cancel-in-progress"] is False and d[True]["schedule"][0]["cron"] == "1,31 13-20 * * 1-5"


def test_nightly_workflow():
    text, d = load_wf("nightly.yml")
    assert '"45 21 * * 1-5"' in text and "workflow_dispatch" in text and "group: backtest-lab-trading" in text
    for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_INTRADAY_API_KEY", "ALPACA_INTRADAY_SECRET_KEY"):
        assert f"secrets.{name}" in text
    for s in ("python intraday_data.py SPY QQQ", "python daily.py", "python report.py",
              "gh issue create", "daily-report", "issues: write", "contents: write", "restore-keys", "actions/cache@v4"):
        assert s in text
    assert text.index("daily.py") < text.index("report.py") < text.index("gh issue create")
    if d:
        assert "rounds" not in d[True]["workflow_dispatch"]["inputs"]


def test_requirements_cover_imports():
    req = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text().split()
    for p in ("yfinance", "pandas", "numpy", "pytest", "alpaca-py"):
        assert p in req
    assert "anthropic" not in req
