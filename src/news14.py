"""Study 14 (NEWS_PLAN_14.md): short at the open after bad news and hold to the close of the same day (+ mirror long).

    python src/news14.py     # needs data/news13/ (first minutes + daily), data/news14/ (python src/news14_data.py SYM ...)

Bar j of a session starts at 09:30+j (j = 0..389). Entry variant e (0 = 09:30 print, 1 = 09:31) enters at the OPEN of bar e.
X55 = close of bar 385 (the bar starting 15:55); XC = official daily close; S1/S2 = stop at 1%/2% adverse to entry, checked on
every bar from e through 385 (short: bar high >= entry x (1+s); long: bar low <= entry x (1-s)); fill = the stop level, or
the bar's open when it opens beyond it; else X55.
"""
from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import news12
import news13 as N
import news13_data as D13
import news14_data as D14
from run import fisher_p
from stats import bh_qvalues, deflated_sharpe

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "news14.json"
PLAN = "NEWS_PLAN_14.md"
DISC, LOCK = news12.DISC, news12.LOCK
COSTS, COST = N.COSTS, N.COSTS[0]
RULES = ("X55", "XC", "S1", "S2")
STOP = {"S1": 0.01, "S2": 0.02}
ENTRIES = (0, 1)
PARS = {"E1": (0.5, 1, 2), "E1b": (0,), "E2": (2, 3)}
STRATS = {"e1_short": ("E1", -1), "e1_long": ("E1", 1), "e1b_short": ("E1b", -1), "e1b_long": ("E1b", 1),
          "e2_short": ("E2", -1), "e2_long": ("E2", 1)}
TUNE, TEST, LOCKPER = N.TUNE, N.TEST, N.LOCKPER
N_SIMS, MIN_TUNE_TRADES = N.N_SIMS, N.MIN_TUNE_TRADES
LAST_BAR = 385                      # bar starting 15:55
PATH_BARS = {"30m": 29, "1h": 59, "2h": 119, "3h": 179, "4h": 239, "15:55": 385}


# ------------------------------------------------------------------ gross returns of every exit rule
def stop_exit(O, H, L, e, s, direction):
    """Per session: (fill price, hit flag) of a stop s adverse to the entry open O[:, e]; NaN fill where no stop hits."""
    n = len(O)
    ent = O[:, e]
    with np.errstate(invalid="ignore"):
        if direction < 0:
            lvl = ent * (1 + s)
            hit = H[:, e:LAST_BAR + 1] >= lvl[:, None]
        else:
            lvl = ent * (1 - s)
            hit = L[:, e:LAST_BAR + 1] <= lvl[:, None]
    has, first = hit.any(1), hit.argmax(1)
    op = O[np.arange(n), e + first]
    with np.errstate(invalid="ignore"):
        fill = np.maximum(lvl, op) if direction < 0 else np.minimum(lvl, op)
        fill = np.where(np.isnan(op), lvl, fill)          # bar present (it has a high/low) but no open print: the level
    return np.where(has, fill, np.nan), has


def gross_returns(O, H, L, C, dclose, e, rule, direction):
    """exit/entry - 1 per session (NaN where the entry or the needed exit price is missing)."""
    ent = O[:, e]
    if rule == "X55":
        ex = C[:, LAST_BAR]
    elif rule == "XC":
        ex = np.asarray(dclose, float)
    else:
        fill, has = stop_exit(O, H, L, e, STOP[rule], direction)
        ex = np.where(has, fill, C[:, LAST_BAR])
    return ex / ent - 1


def adverse_excursion(O, H, L, e, direction):
    """Max adverse move from entry (>= 0) through the 15:55 bar, per session."""
    ent = O[:, e]
    with np.errstate(invalid="ignore", all="ignore"):
        return (np.nanmax(H[:, e:LAST_BAR + 1], 1) / ent - 1) if direction < 0 else (1 - np.nanmin(L[:, e:LAST_BAR + 1], 1) / ent)


net_return = N.net_return


# ------------------------------------------------------------------ per-stock container
class Stock14(N.Stock13):
    def __init__(self, sym, mins=None, day=None, news=None, s12=None, full=None):
        day = D13.load_day(sym) if day is None else day
        super().__init__(sym, mins, day, news, s12)
        fd, fo, fh, fl, fc = D14.load_full(sym) if full is None else full
        pos = self.dates.get_indexer(fd)
        n = len(self.dates)
        self.FO, self.FH, self.FL, self.FC = (np.full((n, D14.NB), np.nan) for _ in range(4))
        ok = pos >= 0
        for dst, src in ((self.FO, fo), (self.FH, fh), (self.FL, fl), (self.FC, fc)):
            dst[pos[ok]] = src[ok]
        self.dclose = pd.Series(day.close.values, index=pd.DatetimeIndex(day.index)).reindex(self.dates).values
        self._gg, self._stopped = {}, {}

    def g(self, e, rule, direction):
        key = (e, rule, direction)
        if key not in self._gg:
            self._gg[key] = gross_returns(self.FO, self.FH, self.FL, self.FC, self.dclose, e, rule, direction)
        return self._gg[key]

    def stopped(self, e, rule, direction):
        key = (e, rule, direction)
        if key not in self._stopped:
            self._stopped[key] = stop_exit(self.FO, self.FH, self.FL, e, STOP[rule], direction)[1]
        return self._stopped[key]

    def e1(self, g, news):
        """gap-down (<= -g vol; g = 0: any negative gap) with news True / False / None (either)."""
        if g == 0:
            with np.errstate(invalid="ignore", divide="ignore"):
                m = (self.FO[:, 0] / self.prev_close - 1 < 0) & (self.vol > 0)
        else:
            m = N.gap_down(self.FO[:, 0], self.prev_close, self.vol, g)
        if news is True:
            return m & (self.n_news >= 1)
        return m & (self.n_news == 0) if news is False else m

    def mask(self, ev, par):
        return self.e2(par) if ev == "E2" else self.e1(par, True)


# ------------------------------------------------------------------ evaluation
def _evaluate_set(stocks, ev, par, direction, e, rule, per, with_placebo, tag):
    picks, skipped = [], 0
    for s in stocks:
        idx, sk = N.pick(s, s.mask(ev, par), per, e, rule, direction)
        picks.append(idx); skipped += sk
    nets = {c: np.concatenate([net_return(direction, s.g(e, rule, direction)[i], c) for s, i in zip(stocks, picks)])
            if sum(len(i) for i in picks) else np.array([]) for c in COSTS}
    n = len(nets[COSTS[0]])
    out = {"trades": n, "skipped": skipped}
    if n:
        out.update(mean_bp=float(nets[COSTS[0]].mean() * 1e4), mean_bp_2x=float(nets[COSTS[1]].mean() * 1e4),
                   mean_bp_4x=float(nets[COSTS[2]].mean() * 1e4), median_bp=float(np.median(nets[COSTS[0]]) * 1e4),
                   hit=float((nets[COSTS[0]] > 0).mean()))
    else:
        out.update(mean_bp=None, mean_bp_2x=None, mean_bp_4x=None, median_bp=None, hit=None)
    _, r1 = N.day_series(stocks, picks, direction, e, rule, COSTS[0], per)
    _, r2 = N.day_series(stocks, picks, direction, e, rule, COSTS[1], per)
    out.update(sharpe=N.sharpe(r1) if n else 0.0, sharpe_2x=N.sharpe(r2) if n else 0.0, total=float(np.prod(1 + r1) - 1))
    if n:
        bydate = {}
        for s, idx in zip(stocks, picks):
            for dte, v in zip(s.dates[idx], net_return(direction, s.g(e, rule, direction)[idx], COSTS[0])):
                bydate.setdefault(dte, []).append(v)
        dm = np.array([np.mean(v) for v in bydate.values()])
        out["event_days"] = len(dm)
        out["p_day_t"] = N.tstat_p(dm)
        out["p_day_t_2x"] = N.tstat_p(dm - (COSTS[1] - COSTS[0]) * 2)
        if rule in STOP:
            out["stopped_share"] = float(np.concatenate([s.stopped(e, rule, direction)[i] for s, i in zip(stocks, picks)]).mean())
    if with_placebo and n:
        pools = [s.period(per) for s in stocks]
        cf = N.placebo(stocks, picks, pools, direction, e, rule, tag)
        out["coin_flip"] = cf
        out["p"] = cf["p_value"] if cf else 1.0
        if ev in ("E1", "E1b"):
            for key, news in (("p_vs_gapdays", None), ("p_vs_nonews_gapdays", False)):
                pl = [s.e1(par, news) & s.period(per) for s in stocks]
                c2 = N.placebo(stocks, picks, pl, direction, e, rule, f"{tag}|{key}")
                out[key] = c2["p_value"] if c2 else None
    return out, picks, r1


def grid_sets():
    for sid, (ev, d) in STRATS.items():
        for par, e, rule in itertools.product(PARS[ev], ENTRIES, RULES):
            yield sid, ev, d, par, e, rule


def grid_rows(stocks, per, with_placebo):
    rows = []
    for sid, ev, d, par, e, rule in grid_sets():
        m, _, r1 = _evaluate_set(stocks, ev, par, d, e, rule, per, with_placebo, f"{sid}|{par}|{e}|{rule}")
        rows.append({"strategy": sid, "event": ev, "dir": d, "par": par, "entry": e, "exit": rule, "m": m, "_r1": r1})
    return rows


def evaluate(disc):
    tune = grid_rows(disc, TUNE, False)
    test = grid_rows(disc, TEST, True)
    for r, q in zip(test, bh_qvalues([r["m"].get("p", 1.0) for r in test])):
        r["m"]["q"] = float(q)
    tuned = []
    for sid in STRATS:
        cand = [r for r in tune if r["strategy"] == sid and r["entry"] == 0]
        ok = [r for r in cand if r["m"]["trades"] >= MIN_TUNE_TRADES] or cand
        tuned.append((sid, max(ok, key=lambda r: r["m"]["sharpe"]), cand))
    return tune, test, tuned


def is_candidate(m):
    return N.is_candidate(m)


def results_rows(disc, test, tuned):
    tmap = {(r["strategy"], r["par"], r["entry"], r["exit"]): r for r in test}
    cal = pd.DatetimeIndex(sorted(set().union(*[set(s.dates[s.period(TEST)]) for s in disc])))
    out = []
    for sid, b, cand in tuned:
        t0, t1 = tmap[(sid, b["par"], 0, b["exit"])], tmap[(sid, b["par"], 1, b["exit"])]
        trial = [x["_r1"].mean() / x["_r1"].std(ddof=1) for x in cand if x["_r1"].std(ddof=1) > 1e-12]
        r1, m = t0["_r1"], t0["m"]
        dsr = deflated_sharpe(r1, trial) if len(trial) > 1 and r1.std(ddof=1) > 1e-12 else None
        cd = is_candidate(m)
        act = bool(cd and b["exit"] != "XC" and (t1["m"]["mean_bp"] or 0) > 0 and t1["m"].get("p", 1) < 0.05)
        cum = np.cumprod(1 + r1)
        out.append({"strategy": sid, "asset": "MEGACAP10", "event": b["event"], "direction": "short" if b["dir"] < 0 else "long",
                    "params": {"g_or_k": b["par"], "exit": b["exit"], "entry": "09:30"},
                    "n_trials": len(cand), "is_sharpe": b["m"]["sharpe"], "is_trades": b["m"]["trades"],
                    "oos": {"sharpe": m["sharpe"], "total": m["total"], "trades": m["trades"], "win_rate": m["hit"],
                            "avg_trade_bp": m["mean_bp"], "mean_bp_2x": m["mean_bp_2x"], "mean_bp_4x": m["mean_bp_4x"]},
                    "oos_cost2x_sharpe": m["sharpe_2x"],
                    "coin_flip": {"p_value": m.get("p"), "q_value": m.get("q"), "day_level_p": m.get("p_day_t")},
                    "entry_0931": {k: t1["m"].get(k) for k in ("trades", "mean_bp", "mean_bp_2x", "hit", "p", "q", "sharpe")},
                    "deflated_sharpe": dsr, "tuned": True, "candidate": cd, "actionable": act,
                    "window": [str(cal[0].date()), str(cal[-1].date())],
                    "equity": {"dates": [str(x.date()) for x in cal[::5]], "strategy": [round(float(x), 4) for x in cum[::5]]}})
    return out


# ------------------------------------------------------------------ descriptive
def pub(rows):
    return [{"strategy": r["strategy"], "param": r["par"], "entry": "09:30" if r["entry"] == 0 else "09:31", "exit": r["exit"],
             **{k: v for k, v in r["m"].items() if k != "coin_flip"},
             "median_random_bp_total": (r["m"].get("coin_flip") or {}).get("median_random_bp_total")} for r in rows]


def events_per_year(stocks):
    out = {}
    def add(name, fn):
        for s in stocks:
            for y in np.unique(s.dates.year):
                out.setdefault(name, {}).setdefault(str(y), 0)
                out[name][str(y)] += int((fn(s) & (s.dates.year == y)).sum())
    for g in (0.5, 1, 2, 0):
        add(f"E1_g{g}_news" if g else "E1b_news_negative_gap", lambda s, g=g: s.e1(g, True))
        add(f"gapdown_g{g}_nonews" if g else "negative_gap_nonews", lambda s, g=g: s.e1(g, False))
    for k in (2, 3):
        add(f"E2_k{k}", lambda s, k=k: s.e2(k))
    out["_per_stock_year_note"] = f"{len(stocks)} stocks; divide by {len(stocks)} for per stock"
    return out


def nonews_table(stocks):
    """Descriptive: the same rules on gap-down days WITHOUT news, test years (09:30 and 09:31 entries)."""
    rows = []
    for g, direction, e, rule in itertools.product((0.5, 1, 2, 0), (-1, 1), ENTRIES, RULES):
        m, _, _ = _evaluate_nonews(stocks, g, direction, e, rule)
        rows.append({"g": g, "dir": "short" if direction < 0 else "long", "entry": "09:30" if e == 0 else "09:31", "exit": rule, **m})
    return rows


def _evaluate_nonews(stocks, g, direction, e, rule):
    picks, tot = [], 0
    for s in stocks:
        idx, _ = N.pick(s, s.e1(g, False), TEST, e, rule, direction)
        picks.append(idx)
    v = np.concatenate([net_return(direction, s.g(e, rule, direction)[i], COST) for s, i in zip(stocks, picks)])
    m = {"trades": len(v), "mean_bp": float(v.mean() * 1e4) if len(v) else None, "hit": float((v > 0).mean()) if len(v) else None}
    if len(v):
        cf = N.placebo(stocks, picks, [s.period(TEST) for s in stocks], direction, e, rule, f"nn|{g}|{direction}|{e}|{rule}")
        m["p"] = cf["p_value"] if cf else None
    return m, picks, None


def stops_effect(stocks):
    """Paired (same events, both exits available) mean net bp difference Sx - X55 and share stopped, test years, 09:30 and 09:31."""
    out = []
    for sid, (ev, d) in STRATS.items():
        for par, e in itertools.product(PARS[ev], ENTRIES):
            row = {"strategy": sid, "param": par, "entry": "09:30" if e == 0 else "09:31"}
            for rule in STOP:
                diffs, base, st_, mae = [], [], [], []
                for s in stocks:
                    ok = s.mask(ev, par) & s.period(TEST) & ~np.isnan(s.g(e, "X55", d)) & ~np.isnan(s.g(e, rule, d))
                    diffs.append(net_return(d, s.g(e, rule, d)[ok], COST) - net_return(d, s.g(e, "X55", d)[ok], COST))
                    base.append(net_return(d, s.g(e, "X55", d)[ok], COST))
                    st_.append(s.stopped(e, rule, d)[ok])
                    mae.append(adverse_excursion(s.FO, s.FH, s.FL, e, d)[ok])
                df_, b_, st2, mae2 = map(np.concatenate, (diffs, base, st_, mae))
                row[rule] = {"trades": len(df_), "x55_mean_bp": float(b_.mean() * 1e4) if len(df_) else None,
                             "diff_bp": float(df_.mean() * 1e4) if len(df_) else None,
                             "stopped_share": float(st2.mean()) if len(df_) else None,
                             "mean_max_adverse_bp": float(np.nanmean(mae2) * 1e4) if len(df_) else None}
            out.append(row)
    return out


def path_table(stocks, per):
    """Mean move in bp (price at the time vs the 09:30 open), E1 events vs no-news gap days vs all days."""
    def avg(masks):
        res = {}
        for name, j in list(PATH_BARS.items()) + [("close", None)]:
            v = []
            for s, mk in zip(stocks, masks):
                ok = mk & s.period(per) & ~np.isnan(s.FO[:, 0])
                x = (s.dclose[ok] if j is None else s.FC[ok, j]) / s.FO[ok, 0] - 1
                v.extend(x[~np.isnan(x)])
            res[name] = {"mean_bp": float(np.mean(v) * 1e4) if v else None, "n": len(v)}
        return res
    out = {}
    for g in (0.5, 1, 2, 0):
        out[f"E1_g{g}_news" if g else "E1b_news_negative_gap"] = avg([s.e1(g, True) for s in stocks])
        out[f"gap_g{g}_nonews"] = avg([s.e1(g, False) for s in stocks])
    for k in (2, 3):
        out[f"E2_k{k}"] = avg([s.e2(k) for s in stocks])
    out["all_days"] = avg([np.ones(len(s.dates), bool) for s in stocks])
    return out


def nvda_descriptive(nv):
    out = {"rules": [], "events": [], "recent_days": []}
    for sid, ev, d, par, e, rule in grid_sets():
        row = {"strategy": sid, "param": par, "entry": e, "exit": rule}
        for name, per in (("tune", TUNE), ("test", TEST)):
            m = _evaluate_set([nv], ev, par, d, e, rule, per, False, "")[0]
            row[name] = {k: m[k] for k in ("trades", "mean_bp", "hit")}
        out["rules"].append(row)
    def shorts(i):
        return {f"{e}_{r}": float(net_return(-1, nv.g(e, r, -1)[i], COST) * 1e4) for e in ENTRIES for r in RULES}
    for ev, par in (("E1", 0.5), ("E1", 1), ("E1", 2), ("E1b", 0), ("E2", 2), ("E2", 3)):
        for i in np.where(nv.mask(ev, par) & nv.period(TEST))[0]:
            out["events"].append({"event": ev, "par": par, "date": str(nv.dates[i].date()),
                                  "gap_pct": float((nv.FO[i, 0] / nv.prev_close[i] - 1) * 100), "n_articles": int(nv.n_news[i]),
                                  "open_to_close_pct": float((nv.dclose[i] / nv.FO[i, 0] - 1) * 100), "short_net_bp": shorts(i)})
    day = D13.load_day("NVDA")
    ret = day.close / day.close.shift(1) - 1
    for dte, r in ret.tail(12).items():
        i = nv.dates.searchsorted(dte)
        has = i < len(nv.dates) and nv.dates[i] == dte
        out["recent_days"].append({"date": str(dte.date()), "close_to_close_pct": float(r * 100), "in_minute_data": bool(has),
                                   **({"open": float(nv.FO[i, 0]), "close_1555": float(nv.FC[i, LAST_BAR]), "official_close": float(nv.dclose[i]),
                                       "gap_pct": float((nv.FO[i, 0] / nv.prev_close[i] - 1) * 100), "vol_pct": float(nv.vol[i] * 100),
                                       "n_articles_overnight": int(nv.n_news[i]),
                                       "E1_g0.5": bool(nv.e1(0.5, True)[i]), "E1_g1": bool(nv.e1(1, True)[i]), "E1_g2": bool(nv.e1(2, True)[i]),
                                       "E1b": bool(nv.e1(0, True)[i]), "short_net_bp": shorts(i)} if has else {})})
    return out


def lockbox(cands):
    import time
    t0 = time.time()
    for s in LOCK:
        D14.download(s)
    dl = time.time() - t0
    lock = [Stock14(s) for s in LOCK]
    out = []
    for r in cands:
        ev, d = STRATS[r["strategy"]]
        par, rule = r["params"]["g_or_k"], r["params"]["exit"]
        res = {}
        for e in ENTRIES:
            m, _, _ = _evaluate_set(lock, ev, par, d, e, rule, LOCKPER, True, f"lock|{r['strategy']}|{e}")
            ps = []
            for s in lock:
                mm, _, _ = _evaluate_set([s], ev, par, d, e, rule, LOCKPER, True, f"lock|{r['strategy']}|{e}|{s.sym}")
                ps.append({"asset": s.sym, "trades": mm["trades"], "p_value": mm.get("p", 1.0)})
            fp = fisher_p([x["p_value"] for x in ps])
            res["0930" if e == 0 else "0931"] = {"assets": ps, "fisher_p": fp, "pooled": {k: v for k, v in m.items() if k != "coin_flip"},
                                                 "confirmed": bool(fp < 0.05 and m["sharpe"] > 0 and (m["mean_bp"] or 0) > 0)}
        out.append({"strategy": r["strategy"], "params": r["params"], "download_seconds": dl, **res})
    return out


def main():
    t0 = datetime.now()
    disc = [Stock14(s) for s in DISC]
    info = [{"asset": s.sym, "feed": D13.feed(s.sym), "sessions": len(s.dates), "first": str(s.dates[0].date()), "last": str(s.dates[-1].date()),
             "bars_missing_entry_0930": int(np.isnan(s.FO[:, 0]).sum()), "bars_missing_1555": int(np.isnan(s.FC[:, LAST_BAR]).sum()),
             "sessions_without_daily_close": int(np.isnan(s.dclose).sum())} for s in disc]
    print("loaded", [(i["asset"], i["sessions"]) for i in info], flush=True)
    tune, test, tuned = evaluate(disc)
    rows = results_rows(disc, test, tuned)
    cands = [r for r in rows if r["candidate"]]
    lb = lockbox(cands) if cands else []
    for r in rows:
        r["lockbox"] = next((x for x in lb if x["strategy"] == r["strategy"]), None)
    nv = next(s for s in disc if s.sym == "NVDA")
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan": PLAN, "cost_per_side": COST,
           "stress_per_side": COSTS[1:], "n_sims": N_SIMS, "tune": list(TUNE), "test": [TEST[0], "end of data"],
           "universe": DISC, "lockbox_symbols": LOCK, "data": info,
           "strategies": [{"id": sid, "name": f"Study 14: {'overnight bad news + negative gap' if ev == 'E1b' else 'overnight bad news, gap down' if ev == 'E1' else 'day after bad-news shock'}, "
                                              f"{'short' if d < 0 else 'buy'} the open, hold to the close", "source": "Study 14 (NEWS_PLAN_14.md)",
                           "grid_size": len(PARS[ev]) * len(RULES)} for sid, (ev, d) in STRATS.items()],
           "results": rows, "grid_tune": pub(tune), "grid": pub(test), "events_per_year": events_per_year(disc),
           "stops_effect": stops_effect(disc), "no_news_gapdays": nonews_table(disc), "paths": path_table(disc, TEST),
           "nvda": nvda_descriptive(nv), "lockbox": lb, "candidates": [r["strategy"] for r in cands]}
    OUT.write_text(json.dumps(out, default=float))
    print(f"wrote {OUT}; candidates: {out['candidates'] or 'none'}; {(datetime.now() - t0).seconds}s")


if __name__ == "__main__":
    main()
