"""Report cards (quantstats): daily returns from snapshots, short histories, metrics, briefing lines. Synthetic data only."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import report_cards as rc


def cal(n, start=25000.0, step=0.004, d0="2026-11-02"):
    days = pd.bdate_range(d0, periods=n)
    eq, rows = start, []
    for i, d in enumerate(days):
        eq *= 1 + step * (1 if i % 3 else -1.5)
        rows.append({"date": str(d.date()), "pl": 0.0, "pct": 0.0, "equity": round(eq, 2)})
    return {"start_equity": start, "calendar": rows}


def test_returns_from_equity_uses_start_for_first_day():
    r = rc.returns_from_equity([("2026-11-03", 25250.0), ("2026-11-04", 25000.0)], 25000.0)
    assert len(r) == 2 and abs(r.iloc[0] - 0.01) < 1e-12 and abs(r.iloc[1] - (25000 / 25250 - 1)) < 1e-12
    assert r.index.tz is None and rc.returns_from_equity([], 25000.0).empty


def test_snapshot_returns_calendar_history_and_missing():
    assert len(rc.snapshot_returns(cal(6))) == 6
    assert rc.snapshot_returns(None).empty and rc.snapshot_returns({"equity": 1}).empty
    hist = {"start_equity": 25000.0, "history": {"ALL": [[1794013200, 25000.0], [1794272400, 25100.0], [1794358800, 25200.0]]}}
    assert len(rc.snapshot_returns(hist)) == 2


def test_short_history_gets_a_note_not_metrics(tmp_path):
    c = rc.compute(rc.snapshot_returns(cal(3)), None, tmp_path / "x.html", "x")
    assert c["ok"] is False and c["metrics"] is None and c["days"] == 3 and c["note"] == "not enough history yet (3 days)"
    assert not (tmp_path / "x.html").exists()
    assert rc.compute(rc.snapshot_returns(cal(1)), None)["note"] == "not enough history yet (1 day)"
    assert rc.compute(pd.Series(dtype=float), None)["note"] == "not enough history yet (0 days)"


def test_metrics_and_tearsheet(tmp_path):
    r = rc.snapshot_returns(cal(12))
    spy = pd.Series(np.random.default_rng(3).normal(0.001, 0.01, len(r)), index=r.index)
    c = rc.compute(r, spy, tmp_path / "cards" / "a.html", "A")
    assert c["ok"] and set(c["metrics"]) == set(rc.METRICS) and c["days"] == 12
    m = c["metrics"]
    assert m["max_dd"] < 0 and m["best_day"] > 0 > m["worst_day"] and 0 < m["win_days"] < 1 and m["beta"] is not None
    assert c["tearsheet"] and (tmp_path / "cards" / "a.html").stat().st_size > 10_000
    json.dumps(c, allow_nan=False)   # NaN / inf never reach the JSON
    assert rc.compute(r, None)["metrics"]["beta"] is None   # no benchmark: no beta, the rest still works


def test_file_returns_formats(tmp_path):
    f = tmp_path / "q.json"
    f.write_text(json.dumps({"daily_returns": {"2026-11-02": 0.01, "2026-11-03": -0.005}}))
    assert list(rc.file_returns(f).round(4)) == [0.01, -0.005]
    f.write_text(json.dumps({"returns": [["2026-11-02", 0.01], {"date": "2026-11-03", "ret": 0.02}]}))
    assert len(rc.file_returns(f)) == 2
    f.write_text("nope")
    assert rc.file_returns(f).empty and rc.file_returns(tmp_path / "missing.json").empty


def test_build_and_briefing_lines(tmp_path):
    mem = tmp_path / "mem"
    mem.mkdir()
    (mem / "alpaca_intraday.json").write_text(json.dumps(cal(10)))
    (mem / "system_ta.json").write_text(json.dumps(cal(3)))
    (mem / "system_dts.json").write_text(json.dumps({"equity": 25000.0}))   # no calendar yet; no system_qlib.json at all
    qb = ("qlib_bt", "qlib backtest", tmp_path / "qlib15.json")
    qb[2].write_text(json.dumps({"daily_returns": {str(d.date()): 0.001 * (1 if i % 2 else -1) for i, d in enumerate(pd.bdate_range("2026-01-05", periods=30))}}))
    out = rc.build(mem, tmp_path / "cards", benchmark=None, qlib_bt=qb)
    a = out["accounts"]
    assert list(a) == ["intraday", "qlib", "ta", "dts_shadow", "qlib_bt"]
    assert a["intraday"]["ok"] and a["qlib_bt"]["ok"] and not a["ta"]["ok"] and a["qlib"]["note"].startswith("not enough history")
    assert a["dts_shadow"]["name"] == "day-trader-skill (shadow)" and a["ta"]["name"] == "TradingAgents"
    p = tmp_path / "report_cards.json"
    p.write_text(json.dumps(out))
    lines = rc.briefing_lines(p)
    assert len(lines) == 4 and lines[0].startswith("Noise band: Sharpe ") and " · max DD " in lines[0] and lines[0].endswith("10 days")
    assert lines[1] == "qlib: not enough history yet (0 days)" and lines[2] == "TradingAgents: not enough history yet (3 days)"
    assert lines[3].startswith("day-trader-skill (shadow): not enough history")
    assert rc.briefing_lines(tmp_path / "missing.json") == []


def test_main_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "build", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rc.main() == 0
