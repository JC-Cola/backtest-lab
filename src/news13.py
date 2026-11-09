"""Study 13 (NEWS_PLAN_13.md): short at the open after bad news, cover within minutes (and the mirror long).

    python src/news13.py        # needs data/news/<SYM>.jsonl and data/news13/ (python src/news13_data.py SYM ...)

Time convention: bar j of a session starts at 09:30+j (j = 0..44). The price at time 09:30+j is the OPEN of bar j.
Entry variant e (0 = 09:30 print, 1 = 09:31) enters at open[e]; exit m is the CLOSE of bar e+m-1 (price at 09:30+e+m);
exit G is the close of the first green (short) / red (long) bar from bar e on, capped at bar e+14.
"""
from __future__ import annotations

import itertools
import json
import math
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import news12
import news13_data as D
from run import fisher_p
from stats import bh_qvalues, deflated_sharpe

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "news13.json"
PLAN = "NEWS_PLAN_13.md"
DISC, LOCK = news12.DISC, news12.LOCK
COSTS = (0.0005, 0.0010, 0.0020)
COST = COSTS[0]
RULES = (1, 2, 5, 15, 30, "G")
ENTRIES = (0, 1)
GS, KS = (1, 2), (2, 3)
VOL_N, MIN_TUNE_TRADES, NB = 60, 30, 45
TUNE = ("2016-01-01", "2021-12-31")
TEST = ("2022-01-01", "2100-01-01")
LOCKPER = ("2016-01-01", "2100-01-01")
N_SIMS, SEED = 2000, 20261109
STRATS = {"e1_short": ("E1", -1), "e1_long": ("E1", 1), "e2_short": ("E2", -1), "e2_long": ("E2", 1)}
PATH_MIN = (1, 2, 5, 15, 30)


# ------------------------------------------------------------------ events / timing
def news_counts_open(dates, created_at):
    """Per trading day t: #articles with 16:00 ET of the previous row of `dates` < created_at < 09:30:00 ET of day t."""
    d = pd.DatetimeIndex(dates)
    hi = (d + pd.Timedelta(hours=9, minutes=30)).tz_localize("US/Eastern").tz_convert("UTC").asi8
    prev = (d + pd.Timedelta(hours=16)).tz_localize("US/Eastern").tz_convert("UTC").asi8
    lo = np.r_[hi[:1], prev[:-1]]                      # first day has no previous close: empty window
    ts = np.sort(pd.DatetimeIndex(created_at).tz_convert("UTC").asi8) if len(created_at) else np.array([], np.int64)
    return np.maximum(np.searchsorted(ts, hi, side="left") - np.searchsorted(ts, lo, side="right"), 0)


def vol_series(close, n=VOL_N):
    """vol_t = sample std (ddof 1) of the n close-to-close returns ending on day t-1 (day t is not in it)."""
    c = np.asarray(close, float)
    ret = np.full(len(c), np.nan)
    ret[1:] = c[1:] / c[:-1] - 1
    return pd.Series(ret).rolling(n).std(ddof=1).shift(1).values


def gap_down(open0, prev_close, vol, g):
    """True where open0/prev_close - 1 <= -g * vol (vol NaN or 0 -> False)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        gap = np.asarray(open0, float) / np.asarray(prev_close, float) - 1
        return (gap <= -g * np.asarray(vol, float)) & (np.asarray(vol, float) > 0)


# ------------------------------------------------------------------ gross returns of every exit rule
def gross_returns(O, C, e, rule, direction):
    """exit/entry - 1 per session (NaN if entry or the chosen exit bar is missing). O, C: (n, NB) arrays."""
    n = len(O)
    ent = O[:, e]
    if rule == "G":
        Cw, Ow = C[:, e:e + 15], O[:, e:e + 15]
        with np.errstate(invalid="ignore"):
            hit = (Cw > Ow) if direction < 0 else (Cw < Ow)
        has, first = hit.any(1), hit.argmax(1)
        ex = np.where(has, Cw[np.arange(n), first], Cw[:, 14])
    else:
        ex = C[:, e + rule - 1]
    return ex / ent - 1


def net_return(direction, g, cost):
    """Long: g - 2c. Short: -g - 2c (no borrow: minutes)."""
    return direction * g - 2 * cost


# ------------------------------------------------------------------ per-stock container
class Stock13:
    def __init__(self, sym, mins=None, day=None, news=None, s12=None):
        self.sym = sym
        m = D.load_min(sym) if mins is None else mins
        day = D.load_day(sym) if day is None else day
        m = m[~m.index.duplicated()]
        sess = pd.DatetimeIndex(m.index.tz_localize(None).normalize())     # NY wall-clock date of each bar
        j = (m.index.hour * 60 + m.index.minute - 570).values
        self.dates = pd.DatetimeIndex(sorted(set(sess)))
        pos = np.searchsorted(self.dates.asi8, sess.asi8)
        self.O = np.full((len(self.dates), NB), np.nan)
        self.C = np.full((len(self.dates), NB), np.nan)
        self.O[pos, j], self.C[pos, j] = m.open.values, m.close.values
        # daily: calendar of trading days (Alpaca daily), prev close and vol from data before the open of day t
        dd = pd.DatetimeIndex(day.index)
        art = news13_news(sym) if news is None else news
        self.n_news_d = news_counts_open(dd, art["created_at"])
        pc = pd.Series(day.close.values, index=dd).shift(1)
        vol = pd.Series(vol_series(day.close.values), index=dd)
        self.prev_close = pc.reindex(self.dates).values
        self.vol = vol.reindex(self.dates).values
        self.n_news = pd.Series(self.n_news_d, index=dd).reindex(self.dates).fillna(0).values
        self.s12 = news12.Stock(sym) if s12 is None else s12
        self._g, self._e2 = {}, {}

    def g(self, e, rule, direction):
        key = (e, rule, direction)
        if key not in self._g:
            self._g[key] = gross_returns(self.O, self.C, e, rule, direction)
        return self._g[key]

    def e1(self, g, news):
        """bool per session: gap-down >= g vol; news = True (>=1 article), False (none), None (either)."""
        m = gap_down(self.O[:, 0], self.prev_close, self.vol, g)
        if news is True:
            return m & (self.n_news >= 1)
        return m & (self.n_news == 0) if news is False else m

    def e2(self, k):
        """bool per session: the previous trading day (Yahoo series of study 12) was a negative news shock."""
        if k not in self._e2:
            sg = self.s12.sig(k, "all")
            trade_days = self.s12.dates[1:][sg[:-1] == -1]
            self._e2[k] = self.dates.isin(trade_days)
        return self._e2[k]

    def mask(self, ev, par):
        return self.e1(par, True) if ev == "E1" else self.e2(par)

    def period(self, per):
        return (self.dates >= pd.Timestamp(per[0])) & (self.dates <= pd.Timestamp(per[1]))


def news13_news(sym):
    import news_data
    return news_data.load(sym)


# ------------------------------------------------------------------ evaluation
def pick(st, mask, per, e, rule, direction):
    """Indices of taken trades (event, in period, bars present) and number skipped for missing bars."""
    ok = mask & st.period(per)
    g = st.g(e, rule, direction)
    idx = np.where(ok & ~np.isnan(g))[0]
    return idx, int(ok.sum() - len(idx))


def sharpe(r):
    r = np.asarray(r, float)
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return float(r.mean() / sd * math.sqrt(252)) if sd > 1e-12 else 0.0


def day_series(stocks, picks, direction, e, rule, cost, per):
    """Daily return series over the sessions of `per`: each stock a 1/N sleeve; day = sum of trade returns / N."""
    cal = pd.DatetimeIndex(sorted(set().union(*[set(s.dates[s.period(per)]) for s in stocks])))
    r = np.zeros(len(cal))
    for s, idx in zip(stocks, picks):
        if len(idx):
            pos = np.searchsorted(cal.asi8, s.dates[idx].asi8)
            np.add.at(r, pos, net_return(direction, s.g(e, rule, direction)[idx], cost) / len(stocks))
    return cal, r


def tstat_p(day_means):
    """One-sided p for mean > 0 on day-level means (normal approximation of the t statistic)."""
    x = np.asarray(day_means, float)
    if len(x) < 5 or x.std(ddof=1) < 1e-15:
        return 1.0
    t = x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))
    return float(0.5 * math.erfc(t / math.sqrt(2)))


def seed_of(*parts):
    return SEED + zlib.crc32("|".join(map(str, parts)).encode()) % 1000003


def placebo(stocks, picks, pools, direction, e, rule, tag, n_sims=N_SIMS):
    """Pooled coin flip: per stock the same number of trades on random days drawn without replacement from pools[i]
    (a bool mask over sessions; only days with bars count). Returns p and summary, or None if no trades."""
    obs, sim, any_ = 0.0, np.zeros(n_sims), False
    for i, (s, idx) in enumerate(zip(stocks, picks)):
        k = len(idx)
        if not k:
            continue
        g = s.g(e, rule, direction)
        cand = np.where(pools[i] & ~np.isnan(g))[0]
        if len(cand) < k:
            return None
        any_ = True
        obs += float((direction * g[idx]).sum())
        rng = np.random.default_rng(seed_of(tag, s.sym, k, len(cand)))
        pick_ = np.argpartition(rng.random((n_sims, len(cand))), k - 1, axis=1)[:, :k] if k < len(cand) \
            else np.tile(np.arange(len(cand)), (n_sims, 1))
        sim += (direction * g[cand])[pick_].sum(1)
    if not any_:
        return None
    return {"p_value": (1 + int(np.sum(sim >= obs - 1e-12))) / (1 + n_sims), "median_random_bp_total": float(np.median(sim) * 1e4)}


def evaluate_set(stocks, ev, par, direction, e, rule, per, with_placebo=False, tag=""):
    picks, skipped = [], 0
    for s in stocks:
        idx, sk = pick(s, s.mask(ev, par), per, e, rule, direction)
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
    cal, r1 = day_series(stocks, picks, direction, e, rule, COSTS[0], per)
    _, r2 = day_series(stocks, picks, direction, e, rule, COSTS[1], per)
    out.update(sharpe=sharpe(r1) if n else 0.0, sharpe_2x=sharpe(r2) if n else 0.0, total=float(np.prod(1 + r1) - 1))
    if n:
        bydate = {}
        for s, idx in zip(stocks, picks):
            for dte, v in zip(s.dates[idx], net_return(direction, s.g(e, rule, direction)[idx], COSTS[0])):
                bydate.setdefault(dte, []).append(v)
        dm = np.array([np.mean(v) for v in bydate.values()])
        out["event_days"] = len(dm)
        out["p_day_t"] = tstat_p(dm)
        out["p_day_t_2x"] = tstat_p(dm - (COSTS[1] - COSTS[0]) * 2)
    if with_placebo and n:
        pools = [s.period(per) for s in stocks]
        cf = placebo(stocks, picks, pools, direction, e, rule, tag)
        out["coin_flip"] = cf
        out["p"] = cf["p_value"] if cf else 1.0
        if ev == "E1":      # news vs gaps: random days from gap-down days (news or not) of the same g
            gp = [s.e1(par, None) & s.period(per) for s in stocks]
            c2 = placebo(stocks, picks, gp, direction, e, rule, tag + "|gap")
            out["p_vs_gapdays"] = c2["p_value"] if c2 else None
    return out, picks, r1


def grid_rows(stocks, per, with_placebo):
    rows = []
    for sid, (ev, direction) in STRATS.items():
        for par, e, rule in itertools.product(GS if ev == "E1" else KS, ENTRIES, RULES):
            m, _, r1 = evaluate_set(stocks, ev, par, direction, e, rule, per, with_placebo, f"{sid}|{par}|{e}|{rule}")
            rows.append({"strategy": sid, "event": ev, "dir": direction, "par": par, "entry": e, "exit": rule, "m": m, "_r1": r1})
    return rows


def evaluate(disc):
    tune = grid_rows(disc, TUNE, False)
    test = grid_rows(disc, TEST, True)
    qs = bh_qvalues([r["m"].get("p", 1.0) for r in test])
    for r, q in zip(test, qs):
        r["m"]["q"] = float(q)
    tmap = {(r["strategy"], r["par"], r["entry"], r["exit"]): r for r in tune}
    tuned = []
    for sid in STRATS:
        cand = [r for r in tune if r["strategy"] == sid and r["entry"] == 0]
        ok = [r for r in cand if r["m"]["trades"] >= MIN_TUNE_TRADES] or cand
        b = max(ok, key=lambda r: r["m"]["sharpe"])
        tuned.append((sid, b, [x for x in cand]))
    return tune, test, tmap, tuned


def is_candidate(m):
    return bool(m.get("trades") and m["p"] < 0.05 and m["q"] < 0.10 and m["p_day_t"] < 0.05 and m["sharpe"] > 0
                and m["sharpe_2x"] > 0 and (m["mean_bp"] or 0) > 0)


def results_rows(disc, test, tune, tmap, tuned):
    tmap_test = {(r["strategy"], r["par"], r["entry"], r["exit"]): r for r in test}
    out = []
    for sid, b, cand in tuned:
        key = (sid, b["par"], 0, b["exit"])
        t0, t1 = tmap_test[key], tmap_test[(sid, b["par"], 1, b["exit"])]
        trial = [x["_r1"].mean() / x["_r1"].std(ddof=1) for x in cand if x["_r1"].std(ddof=1) > 1e-12]
        r1 = t0["_r1"]
        dsr = deflated_sharpe(r1, trial) if len(trial) > 1 and r1.std(ddof=1) > 1e-12 else None
        m = t0["m"]
        cd = is_candidate(m)
        act = bool(cd and (t1["m"]["mean_bp"] or 0) > 0 and t1["m"].get("p", 1) < 0.05)
        cum = np.cumprod(1 + r1)
        cal = pd.DatetimeIndex(sorted(set().union(*[set(s.dates[s.period(TEST)]) for s in disc])))
        out.append({"strategy": sid, "asset": "MEGACAP10", "event": b["event"], "direction": "short" if b["dir"] < 0 else "long",
                    "params": {"g_or_k": b["par"], "exit": str(b["exit"]), "entry": "09:30" if b["entry"] == 0 else "09:31"},
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
    return [{"strategy": r["strategy"], "param": r["par"], "entry": "09:30" if r["entry"] == 0 else "09:31", "exit": str(r["exit"]),
             **{k: v for k, v in r["m"].items() if k != "coin_flip"}, "median_random_bp_total": (r["m"].get("coin_flip") or {}).get("median_random_bp_total")}
            for r in rows]


def events_per_year(stocks):
    out = {"E1": {}, "E2": {}}
    for g in GS:
        for s in stocks:
            for y in np.unique(s.dates.year):
                m = s.dates.year == y
                t = out["E1"].setdefault(f"g{g}", {}).setdefault(str(y), {"news": 0, "nonews": 0, "all_gaps": 0})
                t["news"] += int((s.e1(g, True) & m).sum()); t["nonews"] += int((s.e1(g, False) & m).sum())
                t["all_gaps"] += int((s.e1(g, None) & m).sum())
    for k in KS:
        for s in stocks:
            for y in np.unique(s.dates.year):
                m = s.dates.year == y
                out["E2"].setdefault(f"k{k}", {}).setdefault(str(y), 0)
                out["E2"][f"k{k}"][str(y)] += int((s.e2(k) & m).sum())
    return out


def path_table(stocks, per):
    """Mean cumulative move (bp, price at 09:30+m vs the 09:30 open) for E1 test-year events vs controls."""
    def avg(masks):
        rows = {m: [] for m in PATH_MIN}
        for s, mk in zip(stocks, masks):
            ok = mk & s.period(per) & ~np.isnan(s.O[:, 0])
            for m in PATH_MIN:
                v = s.C[ok, m - 1] / s.O[ok, 0] - 1
                rows[m].extend(v[~np.isnan(v)])
        return {str(m): {"mean_bp": float(np.mean(v) * 1e4) if v else None, "n": len(v)} for m, v in rows.items()}
    out = {}
    for g in GS:
        out[f"E1_g{g}_news"] = avg([s.e1(g, True) for s in stocks])
        out[f"gap_g{g}_nonews"] = avg([s.e1(g, False) for s in stocks])
    out["all_days"] = avg([np.ones(len(s.dates), bool) for s in stocks])
    return out


def nvda_descriptive(nv):
    out = {"rules": [], "events": []}
    for sid, (ev, direction) in STRATS.items():
        for par, e, rule in itertools.product(GS if ev == "E1" else KS, ENTRIES, RULES):
            row = {"strategy": sid, "param": par, "entry": e, "exit": str(rule)}
            for name, per in (("tune", TUNE), ("test", TEST)):
                m = evaluate_set([nv], ev, par, direction, e, rule, per)[0]
                row[name] = {k: m[k] for k in ("trades", "mean_bp", "hit")}
            out["rules"].append(row)
    for ev, par in (("E1", 1), ("E1", 2), ("E2", 2), ("E2", 3)):
        mk = nv.mask(ev, par) & nv.period(TEST)
        for i in np.where(mk)[0]:
            pth = {str(m): float((nv.C[i, m - 1] / nv.O[i, 0] - 1) * 1e4) for m in PATH_MIN}
            gap = float((nv.O[i, 0] / nv.prev_close[i] - 1) * 100) if not np.isnan(nv.prev_close[i]) else None
            out["events"].append({"event": ev, "par": par, "date": str(nv.dates[i].date()), "gap_pct": gap,
                                  "n_articles": int(nv.n_news[i]), "path_bp_from_0930_open": pth,
                                  "short_net_bp_0930": {str(r): float(net_return(-1, nv.g(0, r, -1)[i], COST) * 1e4) for r in RULES}})
    # recent big down days
    dd = pd.DataFrame({"date": nv.dates, "gap": nv.O[:, 0] / nv.prev_close - 1})
    out["recent_days"] = []
    day = D.load_day("NVDA")
    ret = (day.close / day.close.shift(1) - 1)
    for dte, r in ret.tail(12).items():
        i = nv.dates.searchsorted(dte)
        has = i < len(nv.dates) and nv.dates[i] == dte
        out["recent_days"].append({"date": str(dte.date()), "close_to_close_pct": float(r * 100),
                                   "gap_pct": float(dd.gap.values[i] * 100) if has else None,
                                   "vol_pct": float(nv.vol[i] * 100) if has else None, "n_articles_overnight": int(nv.n_news[i]) if has else None,
                                   "E1_g1": bool(nv.e1(1, True)[i]) if has else None, "E1_g2": bool(nv.e1(2, True)[i]) if has else None,
                                   "path_bp_from_0930_open": {str(m): float((nv.C[i, m - 1] / nv.O[i, 0] - 1) * 1e4) for m in PATH_MIN} if has else None})
    return out


def lockbox(cands):
    import time
    t0 = time.time()
    for s in LOCK:
        D.download(s)
    dl = time.time() - t0
    lock = [Stock13(s) for s in LOCK]
    out = []
    for r in cands:
        ev, direction = STRATS[r["strategy"]]
        par, rule = r["params"]["g_or_k"], r["params"]["exit"]
        rule = rule if rule == "G" else int(rule)
        res = {}
        for e in ENTRIES:
            m, picks, _ = evaluate_set(lock, ev, par, direction, e, rule, LOCKPER, True, f"lock|{r['strategy']}|{e}")
            ps = []
            for s in lock:
                mm, _, _ = evaluate_set([s], ev, par, direction, e, rule, LOCKPER, True, f"lock|{r['strategy']}|{e}|{s.sym}")
                ps.append({"asset": s.sym, "trades": mm["trades"], "p_value": mm.get("p", 1.0)})
            fp = fisher_p([x["p_value"] for x in ps])
            res["0930" if e == 0 else "0931"] = {"assets": ps, "fisher_p": fp, "pooled": {k: v for k, v in m.items() if k != "coin_flip"},
                                                 "confirmed": bool(fp < 0.05 and m["sharpe"] > 0 and (m["mean_bp"] or 0) > 0)}
        out.append({"strategy": r["strategy"], "params": r["params"], "download_seconds": dl, **res})
    return out


def main():
    t0 = datetime.now()
    disc = [Stock13(s) for s in DISC]
    info = [{"asset": s.sym, "feed": D.feed(s.sym), "sessions": len(s.dates), "first": str(s.dates[0].date()), "last": str(s.dates[-1].date()),
             "bars_missing_entry_0930": int(np.isnan(s.O[:, 0]).sum())} for s in disc]
    print("loaded", [(i["asset"], i["sessions"], i["feed"]) for i in info], flush=True)
    tune, test, tmap, tuned = evaluate(disc)
    rows = results_rows(disc, test, tune, tmap, tuned)
    cands = [r for r in rows if r["candidate"]]
    lb = lockbox(cands) if cands else []
    for r in rows:
        r["lockbox"] = next((x for x in lb if x["strategy"] == r["strategy"]), None)
    nv = next(s for s in disc if s.sym == "NVDA")
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan": PLAN, "cost_per_side": COST,
           "stress_per_side": COSTS[1:], "n_sims": N_SIMS, "tune": list(TUNE), "test": [TEST[0], "end of data"],
           "universe": DISC, "lockbox_symbols": LOCK, "data": info,
           "strategies": [{"id": sid, "name": f"Study 13: {'overnight bad news gap-down' if ev == 'E1' else 'day after bad-news shock'}, "
                                              f"{'short' if d < 0 else 'buy'} the open, exit within minutes", "source": "Study 13 (NEWS_PLAN_13.md)",
                           "grid_size": 12} for sid, (ev, d) in STRATS.items()],
           "results": rows, "grid_tune": pub(tune), "grid": pub(test), "events_per_year": events_per_year(disc),
           "paths": path_table(disc, TEST), "nvda": nvda_descriptive(nv), "lockbox": lb, "candidates": [r["strategy"] for r in cands]}
    OUT.write_text(json.dumps(out, default=float))
    print(f"wrote {OUT}; candidates: {out['candidates'] or 'none'}; {(datetime.now() - t0).seconds}s")


if __name__ == "__main__":
    main()
