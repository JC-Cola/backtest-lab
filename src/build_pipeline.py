"""Builds report/pipeline.html (the animated "how a strategy gets in" page) from report/pipeline_template.html and
the REAL latest results: each agent's most recent session (agent_memory/journal.jsonl, agent_memory/intraday/
journal.jsonl), the hand-built studies that are still open (results/intraday*.json), the candidates awaiting
approval and the two Alpaca paper-account snapshots. Only the present is shown, no history.

Each event is one idea (chip). "path" is what the chip does, stage by stage: [stage, busy text, result text];
"end" is how it finishes: "dead" (discarded at the last stage) or "wait" (waits there)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from agent.memory import Memory
from iagent.memory import IMemory

ROOT = Path(__file__).resolve().parent.parent
BAR = 0.95  # deflated score an agent idea needs to reach the held-out test
NY = ZoneInfo("America/New_York")


# ------------------------------------------------------------------ small helpers
def ny_date(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(NY).strftime("%Y-%m-%d")


def pct(x) -> str:
    return f"{x * 100:.0f}%" if x >= 0.995 or x < 0.0005 else f"{x * 100:.1f}%".replace(".0%", "%")


def num(x, d=2) -> str:
    return "n/a" if x is None else f"{x:.{d}f}"


def plain_reason(reason: str) -> str:
    """'too few trades (12, min per fold 3)' -> 'too few trades (12)'."""
    return re.sub(r"\s*\(([^()]*?),\s*min per fold[^()]*\)", r" (\1)", reason or "rejected")


def folds_text(fs: list) -> str:
    neg = sum(1 for f in fs if f <= 0)
    return "all 3 periods positive" if neg == 0 else f"lost money in {neg} of {len(fs) or 3} periods"


def session_slice(trials: list[dict]) -> list[dict]:
    """The agent's latest session: from the last point where the round counter restarts."""
    start = 0
    for i in range(1, len(trials)):
        if trials[i].get("round", 0) < trials[i - 1].get("round", 0):
            start = i
    return trials[start:]


def snapshot(path: Path, name: str) -> dict | None:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return {"name": name, "equity": d.get("equity"), "start": d.get("start_equity"),
            "positions": len(d.get("positions") or []), "asof": d.get("generated")}


# ------------------------------------------------------------------ agent trials
def agent_events(mem: Memory, kind: str) -> list[dict]:
    """One event per trial of the agent's latest session (oldest first)."""
    sess = session_slice(mem.trials)
    verdicts = {v["n"]: v for v in mem.holdout_verdicts()}
    mem._sr_cache = {}
    out = []
    for t in sess:
        who = "Claude" if t.get("proposer") == "claude" else "Evolution"
        sp = t["spec"]
        fs = t.get("fold_sharpes") or []
        tip = [["Source", f"{who} (the {kind} learning agent)"], ["Type", kind], ["Market", sp["market"]],
               ["Tested", ny_date(t["ts"])]]
        busy = "scoring on the tuning years"
        end = "dead"
        if t.get("status") == "rejected":
            r = plain_reason(t.get("reason"))
            path = [[1, busy, r]]
            why = f"Discarded at the backtest: {r}. It never got a score."
            tip.append(["Rejected because", r])
        else:
            rw = t.get("reward", 0.0)
            tip += [["Score as found", f"{rw:.2f} (needs {BAR})"], ["Score now", f"{mem.current_reward(t):.2f}"],
                    ["Sharpe per tuning period", " / ".join(f"{f:.2f}" for f in fs) or "n/a"],
                    ["Trades", str(t.get("trades", "n/a"))]]
            ft = folds_text(fs)
            if t.get("status") == "promoted" and t.get("holdout"):
                h = t["holdout"]
                v = verdicts.get(t["n"])
                hs = (f"beat {pct(1 - h['p_value'])} of random traders · Sharpe {h['sharpe']:.2f}, "
                      f"{h['sharpe_2x_cost']:.2f} at 2× costs")
                tip += [["Held-out test", hs], ["After all tests (q)", num(v and v["q"])]]
                path = [[1, busy, f"score {rw:.2f} · {ft}"], [2, "one shot on unseen years", hs]]
                if v and v["pass"]:
                    path.append([4, "waiting for review", "FTMO and comparison run when it is reviewed"])
                    end = "wait"
                    why = "Passed the held-out test. FTMO and comparison run when it is reviewed."
                else:
                    path[-1][2] = "failed: " + hs
                    why = "Passed the backtest but failed the one-shot test on unseen years."
            elif rw >= BAR:
                path = [[1, busy, f"score {rw:.2f} · {ft}"], [2, "queued for its one shot", "waiting for the held-out test"]]
                end = "wait"
                why = "Passed the backtest; its one-shot test on unseen years is next."
            else:
                path = [[1, busy, f"score {rw:.2f}, needs {BAR} · {ft}"]]
                why = f"Discarded at the backtest: its score of {rw:.2f} is below the {BAR} needed ({ft})."
        out.append({"id": f"{kind[0]}{t['n']}", "src": who, "kind": kind, "market": sp["market"],
                    "text": mem.S.describe(sp), "date": ny_date(t["ts"]), "ts": t["ts"], "path": path, "end": end,
                    "tip": {"facts": tip, "why": why}})
    return out


# ------------------------------------------------------------------ hand-built studies
def study_number(plan: str | None) -> int:
    m = re.search(r"PLAN_(\d+)", plan or "")
    return int(m.group(1)) if m else 1


def study_events(files: list[Path], bench: dict) -> list[dict]:
    """One event per study row. 'open' is True while the study still needs something (confirmation pending,
    awaiting approval); finished studies are mapped too, but build_data() leaves them out."""
    out = []
    for f in files:
        if not f.exists():
            continue
        d = json.loads(f.read_text())
        if not d.get("plan") or not d.get("generated"):
            continue
        n = study_number(d["plan"])
        info = {s["id"]: s for s in d.get("strategies") or []}
        for r in d.get("results") or []:
            out.append(study_row(r, n, d["generated"], info.get(r["strategy"], {}), d.get("n_sims") or 2000, bench))
    return out


def study_row(r: dict, n: int, gen: str, info: dict, sims: int, bench: dict) -> dict:
    oos, cf, lock = r.get("oos") or {}, r.get("coin_flip") or {}, r.get("lockbox")
    c2, p, q = r.get("oos_cost2x_sharpe"), cf.get("p_value"), cf.get("q_value")
    best = max(r.get("ftmo") or [], key=lambda x: x["pass_rate"], default=None)
    params = ", ".join(f"{k} {v}" for k, v in (r.get("params") or {}).items())
    text = (info.get("name") or r["strategy"]) + (f" ({params})" if params else "") \
        + (f": {info['plain']}" if info.get("plain") else "")
    beat = None if p is None else f"beat {pct(1 - p)} of {sims:,} random traders"
    tip = [["Source", f"Study {n}" + (f" · {info['source']}" if info.get("source") else "")], ["Type", "intraday"],
           ["Market", r["asset"]], ["Tested", ny_date(gen)], ["Tuning-years Sharpe", num(r.get("is_sharpe"))],
           ["Test-years Sharpe", f"{num(oos.get('sharpe'))} at 1× costs, {num(c2)} at 2× costs"],
           ["Buy-and-hold Sharpe", num((r.get("buy_hold_oos") or {}).get("sharpe"))],
           ["Random traders beaten", "n/a" if p is None else pct(1 - p)], ["After all tests (q)", num(q, 3)]]
    if best:
        tip.append(["Best FTMO pass rate", f"{pct(best['pass_rate'])} at {best['leverage']}× size"])
    end, open_ = "dead", False
    if not r.get("candidate"):
        if p is not None and p >= 0.05:
            msg = beat.replace("beat", "beat only", 1)
        elif c2 is not None and c2 <= 0:
            msg = "loses money at 2× costs"
        else:
            msg = f"q {num(q)} after all tests"
        path = [[2, "one shot on unseen years", msg]]
        why = f"Discarded at the held-out test: {msg}."
        tip.append(["Confirmation", "not reached"])
    else:
        path = [[2, "one shot on unseen years", f"{beat or 'passed'} · Sharpe {num(c2)} at 2× costs"]]
        ft = f"best pass rate {pct(best['pass_rate'])} at {best['leverage']}× size" if best else "pass rates measured"
        if lock and not lock.get("confirmed"):
            msg = f"failed confirmation on untouched markets (median Sharpe {num(lock.get('median_oos_sharpe'))})"
            path.append([2, "confirming on untouched markets", msg])
            tip.append(["Confirmation", msg])
            why = f"Passed the held-out test, then {msg}."
        elif not lock:
            path.append([3, "replaying the challenge", f"{ft} · confirmation on untouched markets pending"])
            end, open_ = "wait", True
            tip.append(["Confirmation", "pending (untouched markets not run yet)"])
            why = "Passed the held-out test. It stops before the comparison until confirmation on untouched markets is done."
        else:
            b = bench.get("intraday")
            tip.append(["Confirmation", f"confirmed (median Sharpe {num(lock.get('median_oos_sharpe'))})"])
            path.append([3, "replaying the challenge", ft])
            if b is not None and c2 is not None and c2 > b:
                path += [[4, f"vs current {b:.2f}", f"Sharpe {num(c2)} beats {b:.2f}"],
                         [5, "waiting for you", "awaiting your approval"]]
                end, open_ = "wait", True
                why = f"Confirmed and better than what is traded now ({num(c2)} vs {b:.2f}); it waits for your approval."
            else:
                msg = f"not better than what is traded now ({num(c2)} vs {num(b)})"
                path.append([4, "comparing", msg])
                why = f"Confirmed, but {msg}."
    return {"id": f"s{n}{r['strategy']}{r['asset']}", "src": f"Study {n}", "kind": "intraday", "market": r["asset"],
            "text": text, "date": ny_date(gen), "ts": gen, "path": path, "end": end, "open": open_,
            "tip": {"facts": tip, "why": why}}


# ------------------------------------------------------------------ assemble
def counts(events: list[dict]) -> dict:
    last = lambda e: e["path"][-1][0]
    reached = lambda e, s: any(p[0] >= s for p in e["path"])
    return {"proposed": len(events),
            "discarded": sum(1 for e in events if e["end"] == "dead" and last(e) == 1),
            "heldout": sum(1 for e in events if reached(e, 2)),
            "failed": sum(1 for e in events if e["end"] == "dead" and last(e) == 2),
            "ftmo": sum(1 for e in events if reached(e, 3)),
            "notbetter": sum(1 for e in events if e["end"] == "dead" and last(e) == 4),
            "waiting": sum(1 for e in events if e["end"] == "wait" and last(e) >= 4)}


def session_info(mem: Memory, label: str) -> dict:
    s = session_slice(mem.trials)
    if not s:
        return {"label": label, "n": 0}
    mem._sr_cache = {}
    sc = [t for t in s if t.get("status") in ("scored", "promoted")]
    return {"label": label, "n": len(s), "scored": len(sc), "rejected": len(s) - len(sc),
            "best": max((mem.current_reward(t) for t in sc), default=None),
            "start": s[0]["ts"], "end": s[-1]["ts"], "date": ny_date(s[-1]["ts"])}


def build_data(daily: Memory, intra: Memory, root: Path = ROOT) -> dict:
    cands = {}
    cp = root / "agent_memory" / "candidates.json"
    if cp.exists():
        try:
            cands = json.loads(cp.read_text())
        except ValueError:
            pass
    bench = cands.get("benchmarks") or {}
    names = ["intraday.json"] + [f"intraday{i}.json" for i in range(2, 8)] + ["intraday2_gapfade_lockbox.json"]
    studies = [e for e in study_events([root / "results" / f for f in names], bench) if e["open"]]
    events = agent_events(daily, "long-term") + agent_events(intra, "intraday") + studies
    events.sort(key=lambda e: e["ts"])
    waiting = [{"account": c.get("account"), "text": c.get("description"), "sharpe": c.get("sharpe_2x_cost")}
               for c in cands.get("candidates") or [] if c.get("status") == "awaiting approval"]
    waiting += [{"account": "intraday", "text": e["text"], "sharpe": None} for e in studies if e["path"][-1][0] == 5]
    snaps = [s for s in (snapshot(root / "agent_memory" / "alpaca_account.json", "Long-term"),
                         snapshot(root / "agent_memory" / "alpaca_intraday.json", "Intraday")) if s]
    sessions = [session_info(daily, "Long-term agent"), session_info(intra, "Intraday agent")]
    newest = max([x["end"] for x in sessions if x.get("end")] + [e["ts"] for e in studies], default="")
    return {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "newest": newest,
            "events": events, "counts": counts(events), "sessions": sessions, "studies_open": len(studies),
            "waiting": waiting, "bench": bench, "accounts": snaps}


def main(root: Path = ROOT, daily: Memory | None = None, intra: Memory | None = None):
    data = build_data(daily or Memory(), intra or IMemory(), root)
    blob = json.dumps(data, separators=(",", ":"), default=float).replace("</", "<\\/")
    html = (root / "report" / "pipeline_template.html").read_text().replace("/*PIPE*/", blob)
    out = root / "report" / "pipeline.html"
    out.write_text(html)
    print(f"wrote {out} ({len(html) / 1e6:.2f} MB, {len(data['events'])} events)")
    return data


if __name__ == "__main__":
    main()
