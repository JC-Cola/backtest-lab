"""Self-learning loop fixes: the pre-check that stops 'too few trades' waste, and the loud Claude-login health state."""
import json
import random
from datetime import datetime, timezone

import numpy as np
import pytest

import data
import intraday as I
import research
from agent import claude_cli, proposer_health as H
from agent import platform as AP
from agent import spec as AS
from agent.loop import pre_screen
from agent.memory import Memory
from agent.proposer import EvolutionProposer
from iagent import platform as IP
from iagent import spec as IS
from iagent.memory import IMemory
from iagent.proposer import IEvolutionProposer
from test_agent import synth as dsynth
from test_claude_proposer import script
from test_intraday import synth as isynth


@pytest.fixture(scope="module")
def dmarket():
    df, ctx = dsynth(1500)
    mp = pytest.MonkeyPatch()
    mp.setattr(data, "load", lambda t: df)
    try:
        return AP.Market("SPY", ctx)
    finally:
        mp.undo()


@pytest.fixture(scope="module")
def imarket():
    return IP.Market("SPY", I.Days.from_arrays(*isynth(60)))


def _agree(P, m, specs, mem_sr=()):
    seen = set()
    for s in specs:
        why, res = P.precheck(m, s), P.score(m, s, list(mem_sr))
        if why:
            assert res["status"] == "rejected" and res["reason"].split(" (")[0] == why.split(" (")[0]
            seen.add("fail")
        else:
            assert not (res["status"] == "rejected" and res["reason"].startswith(("too few", "coverage")))
            seen.add("pass")
    return seen


def test_daily_precheck_agrees_with_score(dmarket):
    rng = random.Random(3)
    specs = [AS.random_spec(rng, None, "SPY") for _ in range(60)]
    assert _agree(AP, dmarket, specs) == {"fail", "pass"}


def test_intraday_precheck_agrees_with_score(imarket):
    rng = random.Random(4)
    specs = [IS.random_spec(rng, None, "SPY") for _ in range(60)]
    assert _agree(IP, imarket, specs) == {"fail", "pass"}


@pytest.mark.parametrize("Prop,Mem,S,P,fix", [(EvolutionProposer, Memory, AS, AP, "dmarket"),
                                              (IEvolutionProposer, IMemory, IS, IP, "imarket")])
def test_screened_evolution_only_proposes_viable(tmp_path, request, Prop, Mem, S, P, fix):
    m = request.getfixturevalue(fix)
    plain = Prop(Mem(tmp_path / "a"), seed=1)
    base = plain.propose(30, ["SPY"])
    assert sum(bool(P.precheck(m, s)) for s in base) > 0     # the unscreened proposer wastes slots
    scr = Prop(Mem(tmp_path / "b"), seed=1)
    scr.screen = lambda s: P.precheck(m, s)
    out = scr.propose(30, ["SPY"])
    assert len(out) == 30 and not any(P.precheck(m, s) for s in out) and scr.screened > 0


def test_pre_screen_journals_without_entering_the_deflation(tmp_path, dmarket):
    mem, events = Memory(tmp_path), []
    rng = random.Random(5)
    specs = [AS.random_spec(rng, None, "SPY") for _ in range(40)]
    bad = [s for s in specs if AP.precheck(dmarket, s)]
    keep = pre_screen(specs, lambda s: dmarket, AP.precheck, mem, "claude", 1,
                      lambda *a: events.append(a), AS.spec_hash)
    assert bad and len(keep) == len(specs) - len(bad) and len(events) == len(bad)
    assert len(mem.trials) == len(bad) and all(t["status"] == "rejected" and "daily_sr" not in t for t in mem.trials)
    assert mem.trial_sharpes("SPY") == [] and all(AS.spec_hash(t["spec"]) in mem.seen or t["hash"] in mem.seen for t in mem.trials)
    assert mem.trials[0]["reason"].split(" (")[0] in ("too few trades", "coverage 0%") or "pre-check" in mem.trials[0]["reason"]


# ------------------------------------------------------------------ Claude login health
def auth_json(**kw):
    return json.dumps({"is_error": True, "subtype": "success", "api_error_status": 401,
                       "result": "Failed to authenticate. API Error: 401 Invalid bearer token"} | kw)


def test_is_auth_error():
    assert H.is_auth_error(json.loads(auth_json()))
    assert H.is_auth_error({"is_error": True, "api_error_status": 403})
    assert not H.is_auth_error({"is_error": True, "api_error_status": 500, "result": "overloaded"})
    assert not H.is_auth_error({"is_error": False, "result": "401 mentioned"})
    assert not H.is_auth_error(None)


def test_cli_auth_error_recorded_and_cleared(tmp_path, monkeypatch, capsys):
    bad = script(tmp_path, f"cat <<'EOF'\n{auth_json(result='401 Invalid bearer token sk-ant-oat01-ABCDEFGHIJKLMNOP')}\nEOF\nexit 1\n", "bad")
    monkeypatch.setenv("CLAUDE_BIN", bad)
    assert claude_cli.ask("hi", {}) is None
    d = H.read()
    assert d["status"] == "auth_error" and "sk-ant" not in json.dumps(d) and "sk-ant" not in capsys.readouterr().out
    first = d["error_since"]
    assert claude_cli.ask("hi", {}) is None and H.read()["error_since"] == first     # the streak keeps its start
    good = script(tmp_path, "echo '{\"is_error\": false, \"subtype\": \"success\", \"structured_output\": {\"specs\": []}}'\n", "good")
    monkeypatch.setenv("CLAUDE_BIN", good)
    assert claude_cli.ask("hi", {}) == {"specs": []}
    d = H.read()
    assert d["status"] == "ok" and d["last_ok"] and H.briefing_warning() is None


def test_other_failures_are_not_auth_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_BIN", script(tmp_path, "echo boom >&2\nexit 3\n"))
    assert claude_cli.ask("hi", {}) is None and H.read() == {}


def test_alert_once_per_day_and_only_when_sent(monkeypatch):
    sent = []
    t0 = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    assert not H.alert_if_needed(lambda m: sent.append(m) or True, t0)          # healthy: nothing
    H.record_auth_error("401")
    assert not H.alert_if_needed(lambda m: False, t0) and H.read().get("last_alert_date") is None   # failed send: retry later
    assert H.alert_if_needed(lambda m: sent.append(m) or True, t0)
    assert not H.alert_if_needed(lambda m: sent.append(m) or True, t0.replace(hour=22))
    assert H.alert_if_needed(lambda m: sent.append(m) or True, t0.replace(day=9))
    assert len(sent) == 2 and "can't log in (401)" in sent[0] and "CLAUDE_CODE_OAUTH_TOKEN" in sent[0]
    H.record_ok()
    assert H.read()["last_alert_date"] == "2026-11-09" and not H.alert_if_needed(lambda m: sent.append(m), t0.replace(day=10))


def test_briefing_warning_age():
    H.record_auth_error("401")
    assert "can't log in (401)" in H.briefing_warning()
    later = datetime.now(timezone.utc).replace(year=2099)
    assert H.briefing_warning(now=later) is None


def test_research_main_sends_alert_via_fake_sender(tmp_path, monkeypatch):
    msgs = []
    monkeypatch.setattr(research.telegram, "send", lambda t: msgs.append(t) or True)
    monkeypatch.setattr(claude_cli, "binary", lambda: None)
    monkeypatch.setattr(research.aloop, "run", lambda **k: None)
    monkeypatch.setattr(research.iloop, "run", lambda **k: None)
    monkeypatch.setattr(research.live, "put", lambda *a, **k: True)
    (tmp_path / "proposer_health.json").write_text(json.dumps({"status": "auth_error", "error_since": "2026-11-04T01:00:00",
                                                               "last_error": "2026-11-08T01:00:00"}))
    research.main(0.01, mem_base=tmp_path)
    assert len(msgs) == 1 and "since 2026-11-04 01:00" in msgs[0]


def test_workflow_has_telegram_and_commits_state():
    from pathlib import Path
    w = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "research.yml").read_text()
    assert "TELEGRAM_BOT_TOKEN" in w and "TELEGRAM_CHAT_ID" in w and "agent_memory/proposer_health.json" in w
