import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import build_pipeline as B
from agent.memory import Memory
from iagent.memory import IMemory

TS = "2026-11-04T02:00:00+00:00"
DSPEC = {"market": "SPY", "entry": [{"feature": "ibs", "op": "<", "q": 10}], "filter": None, "exit": {"type": "hold", "n": 2}}
ISPEC = {"market": "QQQ", "time": 30, "side": "short", "entry": [{"feature": "gap", "op": ">", "q": 90}],
         "exit": {"type": "hold", "n": 60}, "stop_bp": 25}


def trial(n, rnd=1, status="scored", spec=DSPEC, **kw):
    return {"n": n, "ts": TS, "hash": f"h{n}", "spec": spec, "proposer": "evolution", "round": rnd, "status": status,
            "reward": 0.03, "fold_sharpes": [0.5, 0.2, 0.1], "trades": 50, **kw}


def mem(cls, tmp_path, trials):
    d = tmp_path / cls.__name__
    d.mkdir()
    (d / "journal.jsonl").write_text("".join(json.dumps(t) + "\n" for t in trials))
    m = cls(d)
    m.prior_window_pvalues = lambda: []
    return m


def hold(p=0.001, sh=1.2, sh2=0.9):
    return {"p_value": p, "sharpe": sh, "sharpe_2x_cost": sh2, "trades": 40, "window": "2021+"}


def test_session_is_latest_round_restart():
    ts = [trial(1, 1), trial(2, 2), trial(3, 1), trial(4, 2)]
    assert [t["n"] for t in B.session_slice(ts)] == [3, 4]
    assert B.session_slice([]) == []


def test_agent_outcomes(tmp_path):
    ts = [trial(1, 5), trial(2, 1, status="rejected", reward=0.0, reason="too few trades (12, min per fold 3)"),
          trial(3, 2), trial(4, 3, fold_sharpes=[0.5, -0.1, -0.2]),
          trial(5, 4, status="promoted", reward=0.97, holdout=hold(), proposer="claude"),
          trial(6, 5, status="promoted", reward=0.97, holdout=hold(p=0.6, sh=-0.1, sh2=-0.3))]
    ev = {e["id"]: e for e in B.agent_events(mem(Memory, tmp_path, ts), "long-term")}
    assert "l1" not in ev                                   # earlier session
    assert ev["l2"]["path"] == [[1, "scoring on the tuning years", "too few trades (12)"]] and ev["l2"]["end"] == "dead"
    assert ev["l3"]["path"][-1][2] == "score 0.03, needs 0.95 · all 3 periods positive"
    assert "lost money in 2 of 3 periods" in ev["l4"]["path"][-1][2]
    p = ev["l5"]
    assert p["src"] == "Claude" and p["end"] == "wait" and [x[0] for x in p["path"]] == [1, 2, 4]
    assert p["path"][-1][2] == "FTMO and comparison run when it is reviewed"
    assert ev["l6"]["end"] == "dead" and ev["l6"]["path"][-1][0] == 2 and ev["l6"]["path"][-1][2].startswith("failed")
    f = dict(ev["l3"]["tip"]["facts"])
    assert f["Score as found"].startswith("0.03") and "Sharpe per tuning period" in f and ev["l3"]["tip"]["why"]
    assert ev["l3"]["date"] == "2026-11-03" and ev["l3"]["market"] == "SPY"      # New York date


def test_intraday_agent_text(tmp_path):
    e = B.agent_events(mem(IMemory, tmp_path, [trial(1, spec=ISPEC)]), "intraday")[0]
    assert e["kind"] == "intraday" and "short when gap > p90" in e["text"] and "stop 25 bp" in e["text"]


def row(**kw):
    ftmo = [{"leverage": 2, "pass_rate": 0.1}, {"leverage": 4, "pass_rate": 0.27}]
    r = {"strategy": "id_x", "asset": "SPY", "params": {"k": 1}, "is_sharpe": 0.1, "oos": {"sharpe": 0.4},
         "oos_cost2x_sharpe": 0.8, "buy_hold_oos": {"sharpe": 0.9}, "ftmo": ftmo,
         "coin_flip": {"p_value": 0.001, "q_value": 0.01}, "candidate": True}
    return r | kw


STR = {"id": "id_x", "name": "X Break", "plain": "Trades the break.", "source": "A paper (2020)"}
GEN = "2026-11-04T21:00:00+00:00"


def srow(r, bench=None):
    return B.study_row(r, 6, GEN, STR, 2000, bench or {"intraday": 0.44})


def test_study_cases():
    e = srow(row(candidate=False, coin_flip={"p_value": 0.29, "q_value": 0.8}))
    assert e["path"] == [[2, "one shot on unseen years", "beat only 71% of 2,000 random traders"]] and not e["open"]
    assert srow(row(candidate=False, oos_cost2x_sharpe=-0.2, coin_flip={"p_value": 0.001}))["path"][-1][2] == "loses money at 2× costs"
    e = srow(row(lockbox={"confirmed": False, "median_oos_sharpe": -0.97}))
    assert e["path"][-1][2] == "failed confirmation on untouched markets (median Sharpe -0.97)" and not e["open"]
    e = srow(row())
    assert e["open"] and e["end"] == "wait" and e["path"][-1][0] == 3
    assert e["path"][-1][2].endswith("confirmation on untouched markets pending") and "27% at 4× size" in e["path"][-1][2]
    e = srow(row(lockbox={"confirmed": True, "median_oos_sharpe": 0.3}))
    assert [p[0] for p in e["path"]] == [2, 3, 4, 5] and e["path"][-1][2] == "awaiting your approval"
    e = srow(row(lockbox={"confirmed": True, "median_oos_sharpe": 0.3}, oos_cost2x_sharpe=0.3))
    assert e["path"][-1][0] == 4 and e["end"] == "dead" and "not better" in e["path"][-1][2]
    f = dict(e["tip"]["facts"])
    assert f["Source"] == "Study 6 · A paper (2020)" and f["Buy-and-hold Sharpe"] == "0.90" and f["Best FTMO pass rate"] == "27% at 4× size"
    assert e["src"] == "Study 6" and e["text"].startswith("X Break (k 1): Trades the break.")


def test_study_number_and_files(tmp_path):
    assert B.study_number("INTRADAY_PLAN.md") == 1 and B.study_number("INTRADAY_PLAN_6.md") == 6
    f = tmp_path / "intraday6.json"
    f.write_text(json.dumps({"plan": "INTRADAY_PLAN_6.md", "generated": GEN, "n_sims": 2000, "strategies": [STR], "results": [row()]}))
    ev = B.study_events([f, tmp_path / "missing.json"], {})
    assert len(ev) == 1 and ev[0]["src"] == "Study 6"


def make_root(tmp_path, cands):
    (tmp_path / "report").mkdir()
    (tmp_path / "results").mkdir()
    (tmp_path / "agent_memory").mkdir()
    (tmp_path / "report" / "pipeline_template.html").write_text('<script id="pipe" type="application/json">/*PIPE*/</script>')
    (tmp_path / "agent_memory" / "candidates.json").write_text(json.dumps({"benchmarks": {"intraday": 0.44}, "candidates": cands}))
    (tmp_path / "agent_memory" / "alpaca_intraday.json").write_text(json.dumps(
        {"generated": "2026-11-04T19:01:23+00:00", "start_equity": 25000, "equity": 24937.99, "positions": [{"symbol": "SPY"}]}))
    (tmp_path / "results" / "intraday6.json").write_text(json.dumps(
        {"plan": "INTRADAY_PLAN_6.md", "generated": GEN, "strategies": [STR], "results": [row(), row(asset="IWM", candidate=False)]}))


def test_build_and_html(tmp_path):
    cand = {"account": "long-term", "description": "GLD: buy when x", "status": "awaiting approval", "sharpe_2x_cost": 1.0}
    make_root(tmp_path, [cand, dict(cand, status="not better than what is traded now")])
    d = mem(Memory, tmp_path, [trial(1), trial(2, status="rejected", reason="coverage 40% < 70%", reward=0.0)])
    i = mem(IMemory, tmp_path, [trial(1, spec=ISPEC)])
    data = B.main(tmp_path, d, i)
    html = (tmp_path / "report" / "pipeline.html").read_text()
    assert "/*PIPE*/" not in html and json.dumps(data["counts"], separators=(",", ":")) in html
    assert data["studies_open"] == 1 and len(data["events"]) == 4          # 2 + 1 agent trials + the open study only
    assert [e["ts"] for e in data["events"]] == sorted(e["ts"] for e in data["events"])
    assert data["waiting"] == [{"account": "long-term", "text": "GLD: buy when x", "sharpe": 1.0}]
    assert data["counts"]["proposed"] == 4 and data["counts"]["discarded"] == 3 and data["counts"]["waiting"] == 0
    assert data["accounts"] == [{"name": "Intraday", "equity": 24937.99, "start": 25000, "positions": 1, "asof": "2026-11-04T19:01:23+00:00"}]
    assert any(e["path"][-1][2] == "coverage 40% < 70%" for e in data["events"])


def test_template_is_real_data_only():
    t = (Path(__file__).resolve().parent.parent / "report" / "pipeline_template.html").read_text().lower()
    assert "/*pipe*/" in t and 'id="pipe"' in t
    for bad in ("demo", "illustration", "math.random() <", "info = ["):
        assert bad not in t
