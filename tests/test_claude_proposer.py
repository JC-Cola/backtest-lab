import json
import stat

import pytest

import candidates
import intraday as I
from agent import claude_cli
from agent import spec as DS
from agent.memory import Memory
from agent.proposer import ClaudeProposer
from iagent import spec as IS
from iagent.loop import run as irun
from iagent.memory import IMemory
from iagent.proposer import IClaudeProposer
from test_intraday import synth


def script(tmp_path, body, name="fakeclaude"):
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def ok_json(cost=0.25, **kw):
    return json.dumps({"is_error": False, "subtype": "success", "structured_output": {"specs": ["x"]},
                       "total_cost_usd": cost} | kw)


def test_ask_success_and_spent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(claude_cli, "_spent", 0.0)
    out = tmp_path / "args.txt"
    monkeypatch.setenv("CLAUDE_BIN", script(tmp_path, f"pwd > {out}\nprintf '%s\\n' \"$@\" >> {out}\ncat <<'EOF'\n{ok_json()}\nEOF\n"))
    assert claude_cli.ask("hello", {"type": "object"}) == {"specs": ["x"]}
    assert claude_cli.ask("again", {"type": "object"}) == {"specs": ["x"]}
    assert claude_cli.spent() == pytest.approx(0.5)
    lines = out.read_text().splitlines()
    assert lines[0] != str(tmp_path)                      # ran in a fresh temp directory
    assert lines[1:5] == ["-p", "again", "--output-format", "json"] and "--no-session-persistence" in lines
    assert lines[lines.index("--json-schema") + 1] == '{"type": "object"}'
    assert lines[lines.index("--disallowedTools") + 1].startswith("Bash Edit Write")


@pytest.mark.parametrize("body", [
    f"cat <<'EOF'\n{ok_json(is_error=True, subtype='error_max_turns')}\nEOF\n",
    "echo 'not json'\n",
    "echo boom >&2\nexit 3\n",
    "echo '[1, 2]'\n",
    'echo \'{"is_error": false, "subtype": "success"}\'\n',
])
def test_ask_failures_return_none(tmp_path, monkeypatch, capsys, body):
    monkeypatch.setattr(claude_cli, "_spent", 0.0)
    monkeypatch.setenv("CLAUDE_BIN", script(tmp_path, body))
    assert claude_cli.ask("hi", {}) is None
    assert capsys.readouterr().out.startswith("claude_cli:")


def test_ask_timeout_and_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_BIN", script(tmp_path, "sleep 5\n"))
    assert claude_cli.ask("hi", {}, timeout=1) is None
    monkeypatch.setenv("CLAUDE_BIN", str(tmp_path / "nope"))
    assert claude_cli.ask("hi", {}) is None and claude_cli.binary() is None


def fake_ask(monkeypatch, specs):
    """specs: list of strings, or None to simulate a failed call."""
    calls = []
    monkeypatch.setattr(claude_cli, "ask", lambda prompt, schema, timeout=600: calls.append(prompt) or (
        None if specs is None else {"specs": specs}))
    return calls


D1 = {"market": "SPY", "entry": [{"feature": "ibs", "op": "<", "q": 10.2}], "filter": None, "exit": {"type": "hold", "n": 3}}
I1 = {"market": "QQQ", "time": 60, "side": "long", "entry": [{"feature": "gap", "op": ">", "q": 80.4}],
      "exit": {"type": "hold", "n": 30}, "stop_bp": None}


@pytest.mark.parametrize("cls,mk,S,good,bad", [
    (ClaudeProposer, Memory, DS, D1, {**D1, "market": "XXX"}),
    (IClaudeProposer, IMemory, IS, I1, {**I1, "time": 45}),
])
def test_claude_proposer(tmp_path, monkeypatch, cls, mk, S, good, bad):
    mem = mk(tmp_path)
    calls = fake_ask(monkeypatch, [json.dumps(good), json.dumps(good), json.dumps(bad), "{not json"])
    prop = cls(mem)
    assert prop.S is S
    out = prop.propose(5, ["SPY", "QQQ"])
    assert out == [S.canonical(good)] and out[0] == S.canonical(S.validate(good))   # canonical, deduped
    rej = [t for t in mem.trials if t["status"] == "rejected"]
    assert len(rej) == 2 and all(t["proposer"] == "claude" and t["spec"] is None for t in rej)
    assert "invalid spec" in rej[0]["reason"] and "5 NEW" in calls[0]
    # a spec already tried is skipped; and the rejections now show up in the next prompt
    mem.add({"hash": S.spec_hash(good), "spec": S.canonical(good), "status": "scored", "reward": 0.1})
    assert cls(mem).propose(5, ["SPY"]) == []
    assert "invalid spec" in calls[-1]
    fake_ask(monkeypatch, None)
    assert prop.propose(5, ["SPY"]) == []


def test_intraday_prompt_describes_grammar(tmp_path, monkeypatch):
    calls = fake_ask(monkeypatch, [])
    IClaudeProposer(IMemory(tmp_path)).propose(8, ["SPY", "QQQ"])
    p = calls[0]
    for needle in ("10:00", "15:30", "vwap_dist", "rv_5d", "2016-2020", "random timing", "stop_bp", "SPY"):
        assert needle in p


def test_intraday_loop_falls_back_to_evolution(tmp_path, monkeypatch):
    fake_ask(monkeypatch, None)
    days = {s: I.Days.from_arrays(*synth(60, seed=i)) for i, s in enumerate(IS.MARKETS)}
    mem = irun(rounds=1, per_round=4, seed=1, quiet=True, mem_root=tmp_path, days=days, proposer="claude")
    assert len(mem.trials) == 4 and all(t["proposer"] == "evolution" for t in mem.trials)
    assert "Claude CLI cost" in (tmp_path / "lessons.md").read_text()


def test_intraday_loop_uses_claude_specs(tmp_path, monkeypatch):
    fake_ask(monkeypatch, [json.dumps(I1)])
    days = {s: I.Days.from_arrays(*synth(60, seed=i)) for i, s in enumerate(IS.MARKETS)}
    mem = irun(rounds=1, per_round=4, seed=1, quiet=True, mem_root=tmp_path, days=days, proposer="claude")
    assert [t["proposer"] for t in mem.trials] == ["claude"] and mem.trials[0]["hash"] == IS.spec_hash(I1)


# ---------------------------------------------------------------- candidates
class FakeMem:
    def __init__(self, S, rows):
        self.S, self.rows = S, rows
        self.trials = [{"n": i + 1, "spec": r["spec"], "holdout": r["holdout"]} for i, r in enumerate(rows)]

    def holdout_verdicts(self):
        return [{"n": i + 1, "hash": r["hash"], "q": 0.05, "pass": r["pass"], "spec": r["spec"]} for i, r in enumerate(self.rows)]


def ho(s2):
    return {"sharpe": s2 + 0.1, "sharpe_2x_cost": s2, "p_value": 0.01, "trades": 80, "window": ["2021-01-04", "2025-01-01"]}


def test_candidates_update(tmp_path):
    rd = tmp_path / "results"
    rd.mkdir()
    (rd / "results.json").write_text(json.dumps({"results": [
        {"strategy": "ibs", "asset": "SPY", "oos_cost2x_sharpe": 0.5, "oos": {"sharpe": 0.9}},
        {"strategy": "trend", "asset": "QQQ", "oos": {"sharpe": 0.7}}]}))                       # falls back to oos sharpe
    (rd / "intraday.json").write_text(json.dumps({"results": [
        {"strategy": "id_noise_breakout", "asset": "SPY", "oos_cost2x_sharpe": 0.0},
        {"strategy": "id_noise_breakout", "asset": "QQQ", "oos_cost2x_sharpe": 1.0},
        {"strategy": "other", "asset": "SPY", "oos_cost2x_sharpe": 9.0}]}))
    live = [("ibs", "SPY"), ("trend", "QQQ")]
    assert candidates.benchmarks(rd, live) == {"intraday": 0.5, "long-term": 0.6}
    dm = FakeMem(DS, [{"hash": "d1", "pass": True, "spec": D1, "holdout": ho(0.9)},
                      {"hash": "d2", "pass": True, "spec": D1, "holdout": ho(0.3)},
                      {"hash": "d3", "pass": False, "spec": D1, "holdout": ho(2.0)}])
    im = FakeMem(IS, [{"hash": "i1", "pass": True, "spec": IS.canonical(I1), "holdout": ho(0.5)}])   # equal: not better
    path = tmp_path / "agent_memory" / "candidates.json"
    doc = candidates.update(dm, im, path, rd, live)
    by = {c["hash"]: c for c in doc["candidates"]}
    assert set(by) == {"d1", "d2", "i1"} and doc["benchmarks"] == {"intraday": 0.5, "long-term": 0.6}
    assert by["d1"]["better"] and by["d1"]["status"] == "awaiting approval" and by["d1"]["account"] == "long-term"
    assert not by["d2"]["better"] and by["d2"]["status"] == "not better than what is traded now"
    assert by["i1"]["account"] == "intraday" and not by["i1"]["better"]
    assert by["d1"]["description"] == DS.describe(D1) and by["d1"]["trades"] == 80 and by["d1"]["q"] == 0.05
    assert candidates.load(path) == json.loads(path.read_text())
    # the user's approval survives the next nightly update
    doc2 = json.loads(path.read_text())
    next(c for c in doc2["candidates"] if c["hash"] == "d1")["status"] = "approved"
    path.write_text(json.dumps(doc2))
    again = {c["hash"]: c for c in candidates.update(dm, im, path, rd, live)["candidates"]}
    assert again["d1"]["status"] == "approved" and again["d2"]["status"] != "approved"


def test_candidates_load_missing(tmp_path):
    assert candidates.load(tmp_path / "none.json") == {"generated": None, "benchmarks": {}, "candidates": []}
