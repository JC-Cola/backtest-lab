"""The intraday self-improving loop: read memory -> propose -> score on the search years -> remember ->
(rarely) one-shot held-out test -> write lessons. Run from src/:

    python -m iagent.loop --rounds 20 --per-round 10
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from agent import claude_cli
from agent.loop import emitter, holdout_outcome, make_proposer, score_detail
from iagent import platform as P
from iagent import spec as S
from iagent.memory import IMemory
from iagent.proposer import IClaudeProposer, IEvolutionProposer, IWebResearchProposer

PROMOTE_DSR = 0.95          # deflated-Sharpe probability needed before the held-out test
MAX_HOLDOUT_PER_SESSION = 3  # the held-out window is precious: few shots per session


def run(rounds=10, per_round=10, markets=None, seed=0, quiet=False, mem_root=None, days=None, proposer="evolution",
        on_event=None, cache=None, deadline=None):
    """days: optional {symbol: Days} (tests inject synthetic minute data). on_event(spec, stage, status, why, detail),
    cache ({symbol: Market} reused between calls) and deadline (epoch seconds) work as in agent.loop.run;
    proposer: "evolution"/"claude"/"web", an object with .propose, or a factory(mem)."""
    markets = markets or S.MARKETS
    mem = IMemory(Path(mem_root)) if mem_root else IMemory()
    log = (lambda *a: None) if quiet else print
    log(f"memory: {len(mem.trials)} past trials, {len(mem.scored())} scored, "
        f"{len(mem.holdout_tests())} held-out tests already used")
    mk = cache if cache is not None else {}
    emit = emitter(on_event)
    evo = IEvolutionProposer(mem, seed)
    prop, proposer = make_proposer(proposer, mem, evo, {"claude": IClaudeProposer, "web": IWebResearchProposer})
    spent0 = claude_cli.spent()
    shots = 0
    start_best = max([mem.current_reward(t) for t in mem.top(1)], default=0.0)
    t0 = time.time()
    for rnd in range(1, rounds + 1):
        tr = time.time()
        specs, used = prop.propose(per_round, markets), proposer
        if not specs and proposer in ("claude", "web"):
            log(f"round {rnd}: {proposer} returned no ideas, using the evolution proposer")
            specs, used = evo.propose(per_round, markets), "evolution"
        meta = getattr(prop, "meta", {}) if used == proposer else {}
        info = lambda s: {"source": used, "round": rnd, "market": s["market"]} | meta.get(S.spec_hash(s), {})
        for s, why, det in getattr(prop, "skipped", []):
            emit(s, 0, "dead", why, {"source": proposer, "round": rnd, "market": s["market"]} | det)
        for s in specs:
            emit(s, 0, "busy", "proposed", info(s))
        for s in specs:
            if deadline and time.time() > deadline:
                break
            if s["market"] not in mk:
                mk[s["market"]] = P.Market(s["market"], (days or {}).get(s["market"]))
            m, h, base = mk[s["market"]], S.spec_hash(s), info(s)
            emit(s, 1, "busy", "backtesting on the tuning years", base)
            res = P.score(m, s, mem.trial_sharpes(s["market"]))
            rec = mem.add({"hash": h, "spec": s, "proposer": used, "round": rnd} | meta.get(h, {}) | res)
            det = score_detail(res, base)
            if res["status"] == "rejected":
                emit(s, 1, "dead", res["reason"], det)
            else:
                emit(s, 1, "ok", f"score {res['reward']:.2f}, needs {PROMOTE_DSR}", det)
            if (res["status"] == "scored" and res["dsr"] >= PROMOTE_DSR and res["consistent"]
                    and shots < MAX_HOLDOUT_PER_SESSION):
                shots += 1
                emit(s, 2, "busy", "one-shot held-out test", det)
                ho = P.holdout_test(m, s, h)
                mem.update(rec["n"], status="promoted", holdout=ho)
                log(f"  promoted #{rec['n']} to the held-out test: {S.describe(s)}")
                st, why = holdout_outcome(ho)
                emit(s, 2, st, why, det | {k: ho[k] for k in ("p_value", "sharpe", "sharpe_2x_cost", "trades")})
        best = mem.top(1)
        log(f"round {rnd} ({time.time() - tr:.1f}s): {len(specs)} new ideas | trials {len(mem.trials)} | best reward now "
            f"{mem.current_reward(best[0]):.3f} ({S.describe(best[0]['spec'])})" if best else f"round {rnd}: nothing scored yet")
    verdicts = mem.holdout_verdicts()
    end_best = max([mem.current_reward(t) for t in mem.top(1)], default=0.0)
    note = (f"{rounds} rounds x {per_round} ideas with the {proposer} proposer in {time.time() - t0:.0f}s. "
            f"Best deflated reward went from {start_best:.3f} to {end_best:.3f}. "
            f"Held-out shots this session: {shots}. Strategies passing the held-out test (BH across all "
            f"{len(verdicts)} shots ever): {sum(v['pass'] for v in verdicts)}.")
    if proposer in ("claude", "web"):
        note += f" Claude CLI cost this session: ${claude_cli.spent() - spent0:.2f}."
    mem.write_lessons(note)
    log(note)
    return mem


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--per-round", type=int, default=10)
    ap.add_argument("--markets", nargs="*", default=None)
    ap.add_argument("--proposer", choices=["evolution", "claude", "web"], default="evolution")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mem-root", default=None)
    a = ap.parse_args()
    run(a.rounds, a.per_round, a.markets, a.seed, mem_root=a.mem_root, proposer=a.proposer)
