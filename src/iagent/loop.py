"""The intraday self-improving loop: read memory -> propose -> score on the search years -> remember ->
(rarely) one-shot held-out test -> write lessons. Run from src/:

    python -m iagent.loop --rounds 20 --per-round 10
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

from agent import claude_cli
from agent.loop import emitter, holdout_outcome, make_proposer, pre_screen, score_detail
from iagent import platform as P
from iagent import shots as H
from iagent import universe as U
from iagent import spec as S
from iagent.memory import IMemory
from iagent.proposer import IClaudeProposer, IEvolutionProposer, IWebResearchProposer

PROMOTE_DSR = 0.95          # kept for the live page's "needed" hover only; it no longer gates the held-out test


def run(rounds=10, per_round=10, markets=None, seed=0, quiet=False, mem_root=None, days=None, proposer="evolution",
        on_event=None, cache=None, deadline=None, spend=True, universe=None):
    """days: optional {symbol: Days} (tests inject synthetic minute data). on_event(spec, stage, status, why, detail),
    spend=False skips the held-out shots (dry runs); cache ({symbol: Market} reused between calls) and deadline (epoch seconds) work as in agent.loop.run;
    proposer: "evolution"/"claude"/"web", an object with .propose, or a factory(mem)."""
    mem = IMemory(Path(mem_root)) if mem_root else IMemory()
    # research universe: SPY/QQQ + liquid names; a rotating subset per run; lockbox names are never searched
    uni = universe or (U.ensure(mem.root) if days is None else (U.read(mem.root) or U.default()))
    S.register_markets(uni["search"] + uni["lockbox"])
    markets = markets or U.run_names(uni, 6, int(time.time() // 1800) + seed)
    log = (lambda *a: None) if quiet else print
    log(f"memory: {len(mem.trials)} past trials, {len(mem.scored())} scored, "
        f"{len(mem.holdout_tests())} held-out tests already used")
    mk = cache if cache is not None else {}
    emit = emitter(on_event)
    def market(s):
        if s["market"] not in mk:
            mk[s["market"]] = P.Market(s["market"], (days or {}).get(s["market"]))
        return mk[s["market"]]

    if days is None:    # a name whose 5-minute bars are not cached (the nightly prefetch has not reached it) is skipped this run
        keep = []
        for nm in markets:
            try:
                market({"market": nm})
                keep.append(nm)
            except Exception as ex:
                if nm in U.CORE:
                    raise
                log(f"skipping {nm} this run: {type(ex).__name__}")
        markets = keep

    evo = IEvolutionProposer(mem, seed)
    evo.screen = lambda s: P.precheck(market(s), s)   # resample specs that cannot pass the trade-count rules
    prop, proposer = make_proposer(proposer, mem, evo, {"claude": IClaudeProposer, "web": IWebResearchProposer})
    spent0 = claude_cli.spent()
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
        ok_names = set(markets)
        for s in [x for x in specs if x["market"] not in ok_names]:
            emit(s, 0, "dead", "lockbox name" if s["market"] in uni["lockbox"] else "not a search name of this run",
                 {"source": used, "round": rnd, "market": s["market"]})
        specs = [x for x in specs if x["market"] in ok_names]
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
                rb = H.robustness(rec)
                emit(s, 1, "ok" if rb > 0 else "dead",
                     f"robustness {rb:.2f}" if rb > 0 else "not robust (a fold or 2x costs is not positive)", det)
        best = mem.top(1)
        log(f"round {rnd} ({time.time() - tr:.1f}s): {len(specs)} new ideas | trials {len(mem.trials)} | best reward now "
            f"{mem.current_reward(best[0]):.3f} ({S.describe(best[0]['spec'])})" if best else f"round {rnd}: nothing scored yet")
    spent = [] if not spend else H.spend(mem, market, lambda sp: P.robust_eval(market(sp), sp), P.holdout_test, log=log,
                    extra=lambda mk, sp: {"spy_bh_sharpe": P.spy_buy_hold_sharpe(market({"market": "SPY"})),
                                          "lockbox": P.lockbox_check(lambda nm: market({"market": nm}), sp, uni["lockbox"])},
                    emit=lambda sp, st, status, why, det: emit(sp, st, status, why, det))
    for n in spent:
        ho = mem.trials[n - 1]["holdout"]
        st, why = holdout_outcome(ho)
        emit(mem.trials[n - 1]["spec"], 2, st, why, {"source": mem.trials[n - 1].get("proposer"), "market": mem.trials[n - 1]["spec"]["market"]}
             | {k: ho[k] for k in ("p_value", "sharpe", "sharpe_2x_cost", "trades")})
    verdicts = mem.holdout_verdicts()
    end_best = max([mem.current_reward(t) for t in mem.top(1)], default=0.0)
    note = (f"{rounds} rounds x {per_round} ideas with the {proposer} proposer in {time.time() - t0:.0f}s. "
            f"Best deflated reward went from {start_best:.3f} to {end_best:.3f}. "
            f"Held-out shots this session: {len(spent)} ({H.shots_this_month(mem, datetime.now(timezone.utc))} of {H.K_PER_MONTH} this month). Strategies passing the held-out test (BH across all "
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
