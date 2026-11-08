"""Research that never stops: the job behind research.yml (every 30 minutes). Alternates the intraday and long-term
self-learning agents for a time budget; ideas come from the evolution proposer, from Claude (lessons files, at most
every 2 hours per agent) and from Claude doing web research (at most every 6 hours per agent). Every idea is streamed
to the live table (live.py) while it is tested; the live page shows only what is running now.

    python src/research.py --minutes 20
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import candidates
import live
import telegram
from agent import claude_cli
from agent import proposer_health
from agent import loop as aloop
from agent import memory as amem
from agent import spec as AS
from iagent import loop as iloop
from iagent import memory as imem
from iagent import spec as IS

CLAUDE_EVERY, WEB_EVERY = 2 * 3600, 6 * 3600      # seconds between Claude / web rounds, per agent
LABEL = {"evolution": "Evolution", "claude": "Claude", "web": "Web research"}
KEEP_S, BEAT_S, BATCH_S, BACKOFF_S = 120, 20, 2, 30


class Feed:
    """Batches live updates: one row per idea (latest state wins), busy rows are re-sent as a keep-alive and a
    background heartbeat runs while the main thread is stuck in a long call (a Claude web search)."""

    def __init__(self, run_id: str):
        self.run = {"run_id": run_id, "state": "running", "note": "starting"}
        self.rows, self.dirty, self.done_at = {}, set(), {}
        self.lock, self.send, self.stop_ev = threading.Lock(), threading.Lock(), threading.Event()
        self.ok = self.fail = 0
        self.last = self.quiet_until = 0.0
        self.thread = threading.Thread(target=self._beat, daemon=True)

    def _beat(self):
        while not self.stop_ev.wait(BEAT_S):
            self.flush()

    def start(self):
        self.thread.start()
        self.flush(force=True)

    def finish(self, state: str, note: str):
        self.stop_ev.set()
        with self.lock:
            self.run |= {"state": state, "note": note}
            for r in self.rows.values():
                if r["status"] == "busy":       # never scored (time ran out): stop keeping it alive
                    r["status"] = "waiting"
                    self.dirty.add(r["id"])
        self.flush(force=True)

    def note(self, text: str):
        with self.lock:
            self.run["note"] = text
        self.flush()

    def idea(self, row: dict):
        with self.lock:
            self.rows[row["id"]] = row
            self.dirty.add(row["id"])
            if row["status"] == "busy":
                self.done_at.pop(row["id"], None)
            else:
                self.done_at[row["id"]] = time.time()
        if time.time() - self.last >= BATCH_S:
            self.flush()

    def flush(self, force: bool = False):
        with self.send:
            now = time.time()
            if now < self.quiet_until and not force:
                return
            with self.lock:
                for i, t in list(self.done_at.items()):
                    if now - t > KEEP_S:
                        self.rows.pop(i, None); self.done_at.pop(i); self.dirty.discard(i)
                batch = [r for i, r in self.rows.items() if i in self.dirty or r["status"] == "busy"]
                sent, run = set(self.dirty), dict(self.run)
                self.dirty.clear()
            self.last = now
            if live.put(run, batch):
                self.ok += 1
            else:
                self.fail += 1
                self.quiet_until = now + BACKOFF_S
                with self.lock:
                    self.dirty |= sent


def last_ts(root: Path, proposer: str) -> float | None:
    """Epoch seconds of the newest journal record written by this proposer."""
    f = Path(root) / "journal.jsonl"
    best = None
    for line in (f.read_text().splitlines() if f.exists() else []):
        if f'"proposer": "{proposer}"' in line:
            try:
                t = datetime.fromisoformat(json.loads(line)["ts"]).timestamp()
            except (ValueError, KeyError):
                continue
            best = t if best is None or t > best else best
    return best


def agents(base: Path | None):
    """The two self-learning agents, intraday first."""
    root = lambda default, sub="": (Path(base) / sub) if base else default
    return [
        {"name": "intraday", "run": iloop.run, "S": IS, "root": root(imem.ROOT, "intraday"), "cache": {}},
        {"name": "long-term", "run": aloop.run, "S": AS, "root": root(amem.ROOT), "cache": {}},
    ]


def pick(ag: dict, tried: set, remaining: float) -> str:
    """evolution unless a Claude / web round is due (and a claude binary exists and there is time for one)."""
    if claude_cli.binary() is None or remaining < 300:
        return "evolution"
    for kind, every in (("web", WEB_EVERY), ("claude", CLAUDE_EVERY)):
        t = last_ts(ag["root"], kind)
        if (ag["name"], kind) not in tried and (t is None or time.time() - t > every):
            return kind
    return "evolution"


def hook(feed: Feed, ag: dict, run_id: str, counts: dict):
    def on_event(spec, stage, status, why, detail):
        h = ag["S"].spec_hash(spec)
        if stage == 0:
            counts[ag["name"]] = counts.get(ag["name"], 0) + 1
        feed.idea({"id": f"{ag['name']}-{h}", "run_id": run_id, "account": ag["name"],
                   "source": LABEL.get(detail.get("source"), "Evolution"), "rule": ag["S"].describe(spec),
                   "stage": stage, "status": status, "why": why, "detail": detail})
    return on_event


def main(minutes: float = 20, per_round: int = 10, mem_base=None) -> int:
    t0 = time.time()
    end = t0 + minutes * 60
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    feed, counts, tried, kinds = Feed(run_id), {}, set(), []
    if mem_base:   # test runs keep every state file under mem_base
        proposer_health.PATH = Path(mem_base) / "proposer_health.json"
    ags, off = agents(mem_base), set()
    state, note = "finished", ""
    feed.start()
    try:
        i = idle = 0
        while time.time() < end and len(off) < len(ags) and idle < 2 * len(ags):
            ag = ags[i % len(ags)]
            i += 1
            if ag["name"] in off:
                continue
            kind = pick(ag, tried, end - time.time())
            tried.add((ag["name"], kind))
            feed.note(f"{ag['name']} agent: {LABEL[kind]} ideas")
            before = counts.get(ag["name"], 0)
            try:
                ag["run"](rounds=1, per_round=per_round if kind == "evolution" else 8, proposer=kind, seed=i,
                          mem_root=ag["root"] if mem_base else None, on_event=hook(feed, ag, run_id, counts),
                          cache=ag["cache"], deadline=end)
            except Exception as e:
                print(f"!! {ag['name']} agent failed: {type(e).__name__}: {e}")
                off.add(ag["name"])
                continue
            kinds.append(f"{ag['name']}:{kind}")
            idle = 0 if counts.get(ag["name"], 0) > before else idle + 1
        if len(off) == len(ags):
            state = "failed"
        try:
            proposer_health.alert_if_needed(telegram.send)   # one Telegram message per day while the Claude login is refused
        except Exception as e:
            print(f"!! health alert failed: {type(e).__name__}")
        try:
            if mem_base:
                candidates.update(amem.Memory(ags[1]["root"]), imem.IMemory(ags[0]["root"]), path=Path(mem_base) / "candidates.json")
            else:
                candidates.update()
        except Exception as e:
            print(f"!! candidates failed: {type(e).__name__}: {e}")
        note = (f"{sum(counts.values())} ideas tested ({counts.get('long-term', 0)} long-term, "
                f"{counts.get('intraday', 0)} intraday) in {(time.time() - t0) / 60:.0f} min"
                + (f"; rounds: {', '.join(k for k in kinds if not k.endswith(':evolution'))}" if any(
                    not k.endswith(":evolution") for k in kinds) else "") + (f"; off: {', '.join(sorted(off))}" if off else ""))
    except BaseException as e:
        state, note = "failed", f"{type(e).__name__}: {e}"[:200]
        raise
    finally:
        feed.finish(state, note)
        print(f"research {state}: {note}\nlive puts: {feed.ok} ok, {feed.fail} failed")
    return 0 if state == "finished" else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=20)
    ap.add_argument("--per-round", type=int, default=10)
    ap.add_argument("--mem-root", default=None, help="test runs: keep the journals/lessons/candidates here instead of agent_memory/")
    a = ap.parse_args()
    sys.exit(main(a.minutes, a.per_round, a.mem_root))
