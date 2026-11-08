"""The self-improving loop: read memory -> propose -> build table -> check -> score -> remember ->
(rarely) one-shot held-out test -> write lessons. Run from src/:

    python -m agent.loop --rounds 20 --per-round 10                 # free built-in proposer
    python -m agent.loop --rounds 5 --per-round 8 --proposer claude  # Claude proposes (local Claude CLI)
"""
from __future__ import annotations

import argparse
import time

import data
from agent import claude_cli
from agent import platform as P
from agent import spec as S
from agent.memory import Memory
from agent.proposer import ClaudeProposer, EvolutionProposer, WebResearchProposer

PROMOTE_DSR = 0.95          # deflated-Sharpe probability needed before the held-out test
MAX_HOLDOUT_PER_SESSION = 3  # the held-out window is precious: few shots per session


def make_proposer(proposer, mem, evo, classes):
    """-> (proposer object, label). proposer: "evolution"/"claude"/"web", an object with .propose, or a factory(mem)."""
    if isinstance(proposer, str):
        return (evo, proposer) if proposer == "evolution" else (classes[proposer](mem), proposer)
    p = proposer if hasattr(proposer, "propose") else proposer(mem)
    return p, getattr(p, "name", "custom")


def emitter(on_event):
    """Wrap the optional callback so a failure in it can never stop the research."""
    def emit(spec, stage, status, why, detail):
        if on_event:
            try:
                on_event(spec, stage, status, why, detail)
            except Exception as e:
                print(f"on_event failed: {type(e).__name__}")
    return emit


def pre_screen(specs, market, precheck, mem, used, rnd, emit, spec_hash):
    """Specs from any proposer that fail the cheap trade-count/coverage check are journaled as rejected WITHOUT being
    evaluated (no return series, so no daily_sr: they are not trials of the deflated Sharpe) and dropped."""
    keep = []
    for s in specs:
        why = precheck(market(s), s)
        if not why:
            keep.append(s)
            continue
        why += "; pre-check, not evaluated"
        mem.add({"hash": spec_hash(s), "spec": s, "proposer": used, "round": rnd, "status": "rejected",
                 "reason": why, "reward": 0.0})
        emit(s, 0, "dead", why, {"source": used, "round": rnd, "market": s["market"]})
    return keep


def score_detail(res: dict, base: dict) -> dict:
    """The numbers behind a score outcome, for the live page's hover."""
    keep = {k: res[k] for k in ("reward", "dsr", "fold_sharpes", "trades", "sharpe", "excess_sharpe", "coverage") if k in res}
    return base | keep | {"needed": PROMOTE_DSR} | ({"reason": res["reason"]} if res.get("reason") else {})


def holdout_outcome(ho: dict) -> tuple[str, str]:
    ok = ho["sharpe_2x_cost"] > 0 and ho["sharpe"] > 0 and ho["p_value"] < 0.10
    return ("ok" if ok else "dead"), (f"held-out Sharpe {ho['sharpe']:.2f} (2x costs {ho['sharpe_2x_cost']:.2f}), "
                                      f"p {ho['p_value']:.3f}, {ho['trades']} trades")


def run(rounds=10, per_round=10, markets=None, proposer="evolution", seed=0, quiet=False, mem_root=None,
        on_event=None, cache=None, deadline=None):
    """on_event(spec, stage, status, why, detail): optional live feed (stage 0 proposed, 1 backtest, 2 held-out).
    cache: optional {market: Market} reused between calls. deadline: epoch seconds after which no idea is started."""
    markets = markets or S.MARKETS
    mem = Memory(mem_root) if mem_root else Memory()
    log = (lambda *a: None) if quiet else print
    log(f"memory: {len(mem.trials)} past trials, {len(mem.scored())} scored, "
        f"{len(mem.holdout_tests())} held-out tests already used")
    if (mem.root / "lessons.md").exists():
        log("read lessons.md from the last session")
    ctx = data.load_context()
    mk = cache if cache is not None else {}
    emit = emitter(on_event)
    def market(s):
        if s["market"] not in mk:
            mk[s["market"]] = P.Market(s["market"], ctx)
        return mk[s["market"]]

    evo = EvolutionProposer(mem, seed)
    evo.screen = lambda s: P.precheck(market(s), s)   # resample specs that cannot pass the trade-count rules
    prop, proposer = make_proposer(proposer, mem, evo, {"claude": ClaudeProposer, "web": WebResearchProposer})
    spent0 = claude_cli.spent()
    shots = 0
    start_best = max([mem.current_reward(t) for t in mem.top(1)], default=0.0)
    t0 = time.time()
    for rnd in range(1, rounds + 1):
        specs, used = prop.propose(per_round, markets), proposer
        if not specs and proposer in ("claude", "web"):
            log(f"round {rnd}: {proposer} returned no ideas, using the evolution proposer")
            specs, used = evo.propose(per_round, markets), "evolution"
        meta = getattr(prop, "meta", {}) if used == proposer else {}
        info = lambda s: {"source": used, "round": rnd, "market": s["market"]} | meta.get(S.spec_hash(s), {})
        for s, why, det in getattr(prop, "skipped", []):
            emit(s, 0, "dead", why, {"source": proposer, "round": rnd, "market": s["market"]} | det)
        specs = pre_screen(specs, market, P.precheck, mem, used, rnd, emit, S.spec_hash)
        for s in specs:
            emit(s, 0, "busy", "proposed", info(s))
        for s in specs:
            if deadline and time.time() > deadline:
                break
            m, h, base = market(s), S.spec_hash(s), info(s)
            emit(s, 1, "busy", "backtesting on the tuning years", base)
            res = P.score(m, s, mem.trial_sharpes(s["market"]))
            rec = mem.add({"hash": h, "spec": s, "proposer": used, "round": rnd} | meta.get(h, {}) | res)
            det = score_detail(res, base)
            if res["status"] == "rejected":
                emit(s, 1, "dead", res["reason"], det)
            else:
                passed = res["dsr"] >= PROMOTE_DSR and res["consistent"]
                emit(s, 1, "ok" if passed else "dead",
                     f"score {res['reward']:.2f}" + ("" if passed else f", needs {PROMOTE_DSR}"), det)
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
        log(f"round {rnd}: {len(specs)} new ideas | trials {len(mem.trials)} | best reward now "
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
    a = ap.parse_args()
    run(a.rounds, a.per_round, a.markets, a.proposer, a.seed)
