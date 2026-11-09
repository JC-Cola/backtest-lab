"""Emergency loss limits of live_noise: pure risk_action plus executors with fake Alpaca clients."""
import json
from datetime import date, datetime

import pytest

import live_noise as ln

NY = ln.NY
TODAY = date(2026, 11, 3)
PTS = ln.schedule(datetime(2026, 11, 3, 9, 0, tzinfo=NY))


def at(h, m):
    return datetime(2026, 11, 3, h, m, 30, tzinfo=NY)


# ---------- risk_action ----------
def test_daily_threshold_edges():
    f = lambda eq: ln.risk_action(eq, 100000, 100000, {}, TODAY)
    assert f(97000.01) == "ok" and f(97000) == "daily_stop" and f(90000.5) == "daily_stop" and f(103000) == "ok"


def test_kill_threshold_edges():
    f = lambda eq: ln.risk_action(eq, eq, 100000, {}, TODAY)  # last_equity == equity: no daily stop in play
    assert f(88000.01) == "ok" and f(88000) == "kill"


def test_halted_today_and_new_day_resets():
    halted = {"halted_day": True, "date": str(TODAY)}
    assert ln.risk_action(99000, 100000, 100000, halted, TODAY) == "halted"
    assert ln.risk_action(99000, 100000, 100000, halted, date(2026, 11, 4)) == "ok"
    assert ln.risk_action(96000, 100000, 100000, halted, date(2026, 11, 4)) == "daily_stop"


def test_kill_outranks_and_persists_across_days():
    both = ln.risk_action(80000, 100000, 100000, {"halted_day": True, "date": str(TODAY)}, TODAY)
    assert both == "kill"
    k = {"killed": True}
    assert ln.risk_action(100000, 100000, 100000, k, TODAY) == "killed"
    assert ln.risk_action(100000, 100000, 100000, k, date(2027, 1, 1)) == "killed"


@pytest.mark.parametrize("last", [None, 0, -5, float("nan")])
def test_bad_last_equity_never_stops(last):
    assert ln.risk_action(1000, last, 1000, {}, TODAY) == "ok"


def test_missing_equity_or_peak_is_ok_but_flags_apply():
    assert ln.risk_action(None, 100, 100, {}, TODAY) == "ok"
    assert ln.risk_action(50, 100, 0, {}, TODAY) == "daily_stop"  # no peak: only the daily rule can fire
    assert ln.risk_action(None, None, None, {"killed": True}, TODAY) == "killed"


def test_initial_peak(tmp_path, monkeypatch):
    snap = tmp_path / "snap.json"
    monkeypatch.setattr(ln, "SNAPSHOT", snap)
    assert ln.initial_peak(24000) == 25000  # no file: start equity
    snap.write_text(json.dumps({"calendar": [{"equity": 26100.5}, {"equity": 25000}], "history": {"1D": [[1, 25900], [2, None]]}}))
    assert ln.initial_peak(24000) == 26100.5 and ln.initial_peak(27000) == 27000


# ---------- executors with fake Alpaca ----------
class FakeTrading:
    def __init__(self, equity, last, held=None, open_orders=()):
        self.eq, self.last, self.held, self.open, self.cancelled = equity, last, dict(held or {}), list(open_orders), []

    def get_account(self):
        class A: pass
        a = A(); a.equity, a.last_equity = str(self.eq), str(self.last)
        return a

    def get_orders(self, req):
        return [o for o in self.open if o.symbol in req.symbols]

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)
        self.open = [o for o in self.open if o.id != oid]

    def get_all_positions(self):
        class P:
            def __init__(s, sym, q): s.symbol, s.qty = sym, str(q)
        return [P(s, q) for s, q in self.held.items() if q]


class Ord:
    def __init__(self, id, symbol): self.id, self.symbol = id, symbol


@pytest.fixture
def env(tmp_path, monkeypatch):
    sent, alerts = [], []
    monkeypatch.setattr(ln, "state_path", lambda sym: tmp_path / f"s_{sym}.json")
    monkeypatch.setattr(ln, "LOG", tmp_path / "noise_live.csv")
    monkeypatch.setattr(ln, "RISK", tmp_path / "noise_risk.json")
    monkeypatch.setattr(ln, "SNAPSHOT", tmp_path / "snap.json")
    monkeypatch.setattr(ln, "alert", alerts.append)

    def submit(tr, qty, tif, sym):
        sent.append((sym, qty, tif))
        tr.held[sym] = tr.held.get(sym, 0) + qty  # fills at once
        return f"id{len(sent)}"
    monkeypatch.setattr(ln, "submit", submit)
    return sent, alerts


def test_daily_stop_flattens_long_and_short_cancels_orders_alerts_once(env):
    sent, alerts = env
    tr = FakeTrading(24060, 24832, {"SPY": 16, "QQQ": -18}, [Ord("o1", "SPY")])
    assert ln.risk_step(tr, at(11, 1), PTS) == (False, [])
    assert sorted(sent) == [("QQQ", 18, "day"), ("SPY", -16, "day")]
    assert tr.cancelled == ["o1"] and len(alerts) == 1
    assert "daily loss stop" in alerts[0] and "$24,060 vs $24,832" in alerts[0] and "3.1%" in alerts[0]
    r = ln.load_risk()
    assert r["halted_day"] and r["date"] == str(TODAY) and r["equity"] == 24060 and r["last_equity"] == 24832
    assert all(ln.load_state(TODAY, s)["moc"] for s in ln.SYMBOLS)
    # same-minute re-run and the next check: no new orders, no new alert
    assert ln.risk_step(tr, at(11, 1), PTS) == (False, [])
    assert ln.risk_step(tr, at(11, 31), PTS) == (False, [])
    assert len(sent) == 2 and len(alerts) == 1
    import csv
    ev = [r["event"] for r in csv.DictReader(ln.LOG.open())]
    assert ev.count("daily loss stop") == 1 and ev.count("halted (daily loss stop)") == 0  # same 11:00 check point: quiet


def test_halted_run_quiet_within_same_checkpoint(env):
    sent, alerts = env
    tr = FakeTrading(24060, 24832)
    ln.risk_step(tr, at(11, 1), PTS)
    ln.risk_step(tr, at(11, 1), PTS)
    ln.risk_step(tr, at(11, 2), PTS)
    assert ln.LOG.read_text().count("halted (daily loss stop)") == 0
    ln.risk_step(tr, at(12, 1), PTS)
    ln.risk_step(tr, at(12, 31), PTS)
    assert ln.LOG.read_text().count("halted (daily loss stop)") == 1


def test_halted_day_still_closes_leftover_at_1556(env):
    sent, alerts = env
    tr = FakeTrading(24060, 24832)
    ln.risk_step(tr, at(10, 1), PTS)
    tr.held["SPY"] = 5  # something appears later
    assert ln.risk_step(tr, at(15, 56), PTS) == (False, [])
    assert sent == [("SPY", -5, "day")] and len(alerts) == 1


def test_new_day_resumes_after_daily_stop(env):
    sent, alerts = env
    ln.risk_step(FakeTrading(24060, 24832), at(10, 1), PTS)
    nxt = datetime(2026, 11, 4, 10, 1, tzinfo=NY)
    assert ln.risk_step(FakeTrading(24100, 24060), nxt, PTS) == (True, [])


def test_kill_switch_then_blocks_entries_but_closes_leftovers_then_rearm(env):
    sent, alerts = env
    tr = FakeTrading(21950, 22000, {"SPY": 10}, [Ord("o9", "SPY")])
    ln.save_risk({"peak": 25055.9})
    assert ln.risk_step(tr, at(12, 1), PTS) == (False, [])
    assert sent == [("SPY", -10, "day")] and tr.cancelled == ["o9"]
    assert len(alerts) == 1 and "KILL SWITCH" in alerts[0] and "12.4%" in alerts[0] and "$21,950 vs peak $25,056" in alerts[0]
    assert ln.load_risk()["killed"] is True
    # re-run and a later day: blocked, no re-alert
    assert ln.risk_step(tr, at(12, 1), PTS) == (False, [])
    nxt = datetime(2026, 11, 4, 10, 1, tzinfo=NY)
    assert ln.risk_step(FakeTrading(25000, 25000), nxt, PTS) == (False, [])
    assert len(alerts) == 1 and len(sent) == 1
    # the 15:56 close still flattens a stray position
    tr2 = FakeTrading(21950, 21950, {"QQQ": -3})
    assert ln.risk_step(tr2, at(15, 56), PTS) == (False, [])
    assert sent[-1] == ("QQQ", 3, "day")
    # rearm
    ln.rearm(FakeTrading(22000, 22000), at(9, 0))
    r = ln.load_risk()
    assert "killed" not in r and r["peak"] == 22000 and "rearm" in ln.LOG.read_text()
    assert ln.risk_step(FakeTrading(22000, 22000), at(10, 1), PTS) == (True, [])


def test_peak_tracking_and_init(env):
    ln.risk_step(FakeTrading(26000, 26000), at(10, 1), PTS)
    assert ln.load_risk()["peak"] == 26000
    ln.risk_step(FakeTrading(25500, 26000), at(10, 31), PTS)
    assert ln.load_risk()["peak"] == 26000
    ln.risk_step(FakeTrading(26500, 26000), at(11, 1), PTS)
    assert ln.load_risk()["peak"] == 26500


def test_cancel_failure_skips_close_and_reports_nothing_double(env, monkeypatch):
    sent, alerts = env
    tr = FakeTrading(24060, 24832, {"SPY": 16}, [Ord("o1", "SPY")])
    def boom(oid): raise RuntimeError("order is filling")
    tr.cancel_order_by_id = boom
    assert ln.risk_step(tr, at(11, 1), PTS) == (False, [])
    assert sent == [] and "in flight" in ln.LOG.read_text()


def test_account_error_does_not_block_normal_flow(env):
    class Bad(FakeTrading):
        def get_account(self): raise RuntimeError("down")
    assert ln.risk_step(Bad(0, 0), at(10, 1), PTS) == (True, [])


def test_workflow_rearm_and_state_commit():
    text = (ln.ROOT / ".github" / "workflows" / "intraday.yml").read_text()
    assert "rearm:" in text and "python live_noise.py --rearm" in text and "agent_memory/noise_risk.json" in text
    assert "python live_noise.py --once" in text
