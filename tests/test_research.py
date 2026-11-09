import json
import stat
import time
import sys
import urllib.request
from pathlib import Path

import pytest

import intraday as I
import live
import research
from agent import claude_cli
from agent import spec as DS
from agent.memory import Memory
from agent.proposer import WEB_SCHEMA, WebResearchProposer
from iagent import spec as IS
from iagent.loop import run as irun
from iagent.memory import IMemory
from iagent.proposer import IWebResearchProposer
from test_intraday import synth

REPO = Path(__file__).resolve().parent.parent
ENV = {"BACKTEST_LIVE_URL": "https://x.example.co", "BACKTEST_LIVE_KEY": "k", "BACKTEST_LIVE_TOKEN": "t"}
D1 = {"market": "SPY", "entry": [{"feature": "ibs", "op": "<", "q": 10.2}], "filter": None, "exit": {"type": "hold", "n": 3}}
D2 = {"market": "QQQ", "entry": [{"feature": "ibs", "op": "<", "q": 20}], "filter": None, "exit": {"type": "hold", "n": 2}}


def test_live_put_noop_without_env(monkeypatch):
    for v in live.VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network"))
    assert live.put({"run_id": "r"}, [{"id": "a"}]) is False


def test_live_put_request_and_never_raises(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    seen = {}

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b""

    def ok(req, timeout):
        seen.update(url=req.full_url, h={k.lower(): v for k, v in req.header_items()}, body=json.loads(req.data), t=timeout)
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", ok)
    assert live.put({"run_id": "r", "state": "running", "note": ""}, [{"id": "a"}])
    assert seen["url"] == "https://x.example.co/rest/v1/rpc/live_put" and seen["t"] == 5 and seen["h"]["apikey"] == "k"
    assert seen["body"] == {"p_token": "t", "p_run": {"run_id": "r", "state": "running", "note": ""}, "p_ideas": [{"id": "a"}]}
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert live.put(None, []) is False


def test_ask_allow_web_command(tmp_path, monkeypatch):
    out = tmp_path / "a.txt"
    p = tmp_path / "fake"
    p.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {out}\necho '{{\"is_error\": false, \"subtype\": \"success\", \"structured_output\": {{}}}}'\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CLAUDE_BIN", str(p))
    claude_cli.ask("hi", {}, allow_web=True)
    a = out.read_text().splitlines()
    dis = a[a.index("--disallowedTools") + 1]
    assert "WebSearch" not in dis and "WebFetch" not in dis and "Bash" in dis and "Agent" in dis
    claude_cli.ask("hi", {})
    assert "WebSearch" in out.read_text().splitlines()[out.read_text().splitlines().index("--disallowedTools") + 1]


def item(spec, verdict, **kw):
    return {"spec": json.dumps(spec), "source_url": "https://ssrn.com/1", "source_title": "Paper", "bull_case": "works",
            "bear_case": "decayed", "risk_review": {"verdict": verdict, "reasons": ["crowded"], "cost_sensitivity": "low",
                                                    "lookahead_risk": "low"}} | kw


def test_web_proposer_risk_review(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(claude_cli, "ask", lambda prompt, schema, timeout=None, allow_web=False: calls.append((schema, allow_web)) or {
        "specs": [item(D1, "test"), item(D2, "skip")]})
    mem = Memory(tmp_path)
    prop = WebResearchProposer(mem)
    out = prop.propose(5, ["SPY", "QQQ"])
    assert calls[0] == (WEB_SCHEMA, True) and out == [DS.canonical(D1)]
    h = DS.spec_hash(DS.canonical(D1))
    assert prop.meta[h]["source_url"] == "https://ssrn.com/1" and prop.meta[h]["risk_review"]["verdict"] == "test"
    rej = [t for t in mem.trials if t["status"] == "rejected"]
    assert len(rej) == 1 and rej[0]["reason"] == "risk review: crowded" and rej[0]["bear_case"] == "decayed" and rej[0]["spec"]
    assert prop.skipped[0][1] == "risk review: crowded" and DS.spec_hash(DS.canonical(D2)) in mem.seen


def test_on_event_sequence_with_web_review(tmp_path, monkeypatch):
    I1 = {"market": "QQQ", "time": 60, "side": "long", "entry": [{"feature": "gap", "op": ">", "q": 80}], "exit": {"type": "hold", "n": 30}, "stop_bp": None}
    I2 = {**I1, "time": 30}
    monkeypatch.setattr(claude_cli, "ask", lambda *a, **k: {"specs": [item(I1, "test"), item(I2, "skip")]})
    ev = []
    monkeypatch.setattr("iagent.platform.precheck", lambda m, s: None)   # 60 synthetic days would fail the trade-count screen
    irun(rounds=1, per_round=2, markets=["QQQ"], quiet=True, mem_root=tmp_path, days={"QQQ": I.Days.from_arrays(*synth(60))}, proposer="web",
         on_event=lambda s, st, status, why, d: ev.append((IS.spec_hash(s), st, status, why, d)))
    h1, h2 = IS.spec_hash(IS.canonical(I1)), IS.spec_hash(IS.canonical(I2))
    assert (h2, 0, "dead") == ev[0][:3] and ev[0][3].startswith("risk review") and ev[0][4]["bull_case"] == "works"
    mine = [(st, status) for h, st, status, *_ in ev if h == h1]
    assert mine[:2] == [(0, "busy"), (1, "busy")] and mine[2][0] == 1 and mine[2][1] in ("ok", "dead")
    assert ev[1][4]["source"] == "web" and ev[1][4]["source_url"] == "https://ssrn.com/1"
    assert json.loads((tmp_path / "journal.jsonl").read_text().splitlines()[-1])["proposer"] == "web"


def test_research_main_with_fake_loops(tmp_path, monkeypatch):
    puts = []
    monkeypatch.setattr(research.live, "put", lambda run=None, ideas=(): puts.append((run, list(ideas))) or True)
    monkeypatch.setattr(research.candidates, "update", lambda *a, **k: {})
    monkeypatch.setattr(claude_cli, "binary", lambda: None)
    spec = {"market": "SPY", "entry": [{"feature": "ibs", "op": "<", "q": 10}], "filter": None, "exit": {"type": "hold", "n": 1}}
    kinds = []

    ispec = {"market": "SPY", "time": 60, "side": "long", "entry": [{"feature": "gap", "op": ">", "q": 80}],
             "exit": {"type": "hold", "n": 30}, "stop_bp": None}

    def fake(sp):
        def run(rounds, per_round, proposer, on_event, **kw):
            kinds.append(proposer)
            on_event(sp, 0, "busy", "proposed", {"source": "evolution"})
            on_event(sp, 1, "dead", "too few trades", {"source": "evolution"})
            time.sleep(0.05)
        return run

    monkeypatch.setattr(research.aloop, "run", fake(spec))
    monkeypatch.setattr(research.iloop, "run", fake(ispec))
    assert research.AGENTS == ["intraday"]                      # the long-term agent is switched off (retired account)
    assert [a["name"] for a in research.agents(tmp_path)] == ["intraday"]
    assert [a["name"] for a in research.agents(tmp_path, ["intraday", "long-term"])] == ["intraday", "long-term"]   # code kept, flag restores it
    assert research.main(0.01, mem_base=tmp_path) == 0
    assert kinds[:2] == ["evolution", "evolution"] and len(kinds) >= 2   # only the intraday fake ran, and it kept the whole budget
    assert puts[0][0]["state"] == "running" and puts[-1][0]["state"] == "finished"
    rows = [r for _, ids in puts for r in ids]
    assert {r["account"] for r in rows} == {"intraday"} and rows[-1]["stage"] == 1 and rows[-1]["source"] == "Evolution"
    assert all(r["run_id"] == puts[0][0]["run_id"] for r in rows)


def test_workflow_and_page():
    text = (REPO / ".github/workflows/research.yml").read_text()
    assert "schedule:" not in text
    for s in ("workflow_dispatch", "group: backtest-lab-research", "cancel-in-progress: false",
              "timeout-minutes: 40", "actions/cache/restore@v4", "restore-keys", "claude-code", "python research.py --minutes"):
        assert s in text
    assert "backtest-lab-trading" not in text
    for n in ("ALPACA_INTRADAY_API_KEY", "ALPACA_INTRADAY_SECRET_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "BACKTEST_LIVE_URL", "BACKTEST_LIVE_KEY", "BACKTEST_LIVE_TOKEN"):
        assert f"secrets.{n}" in text
    try:
        import yaml
        assert yaml.safe_load(text)[True]["schedule"][0]["cron"] == "*/30 * * * *"
    except ImportError:
        pass
    page = (REPO / "report/pipeline.html").read_text()
    for bad in ("/*PIPE*/", "{{", "__"):
        assert bad not in page
    for need in ("tumnfmmnelsfswyowdqh", 'claude.use("mcp")', "watchTool", "callTool", "execute_sql", "live_ideas", "live_runs",
                 '"tools": ["execute_sql"]', "Nothing is being tested right now"):
        assert need in page
