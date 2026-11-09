"""Study 12 (NEWS_PLAN_12.md): does trading big news-coincident price moves work?

    python src/news12.py            # needs data/news/<SYM>.jsonl (python src/news_data.py ...) and daily prices

Event: day t is a shock if |close-to-close return| >= k x the sample std of the 60 returns ending day t-1, and there is
>= 1 news article in (16:00 ET of the previous trading day, 16:00 ET of day t]. Trade from the NEXT open, hold h days.
"""
from __future__ import annotations

import itertools
import json
import re
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import data
import news_data
from run import fisher_p
from stats import bh_qvalues, deflated_sharpe

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "news12.json"
PLAN = "NEWS_PLAN_12.md"
DISC = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO"]
LOCK = ["CRM", "ORCL", "ADBE", "INTC", "QCOM"]
KS, HS = (2, 3), (1, 3, 5, 20)
TYPES = {"a": {-1: -1}, "b": {-1: 1}, "c": {1: 1}, "d": {1: -1},
         "MOM": {-1: -1, 1: 1}, "REV": {-1: 1, 1: -1}}
NAMES = {"a": "short after bad news (momentum)", "b": "buy after bad news (reversal)",
         "c": "buy after good news (momentum)", "d": "short after good news (reversal)",
         "MOM": "momentum both ways (short bad, buy good)", "REV": "reversal both ways (buy bad, short good)"}
SCOPES = ("all", "earn")
COST, STRESS, BORROW = 0.0005, 0.0010, 0.005      # per side; per side; per year on shorts
VOL_N, MIN_TUNE_TRADES = 60, 30
TUNE = ("2016-01-01", "2021-12-31")
TEST = ("2022-01-01", "2100-01-01")
LOCKPER = ("2016-01-01", "2100-01-01")
N_SIMS, SEED = 2000, 20261109
EARN_RE = re.compile(r"\bearnings\b|\bEPS\b|\bquarterly (results|report)\b|\b(Q[1-4]|first|second|third|fourth)[- ]quarter\b",
                     re.I)


# ------------------------------------------------------------------ events
def shock_arrays(close, k, n=VOL_N):
    """(ret, vol, sign): sign[t] = +1/-1 if |ret_t| >= k*vol_t else 0. vol_t uses the n returns ending day t-1 only."""
    c = np.asarray(close, float)
    ret = np.full(len(c), np.nan)
    ret[1:] = c[1:] / c[:-1] - 1
    vol = pd.Series(ret).rolling(n).std(ddof=1).shift(1).values
    with np.errstate(invalid="ignore"):
        big = np.abs(ret) >= k * vol
    sign = np.where(big & (vol > 0), np.sign(ret), 0).astype(int)
    return ret, vol, sign


def news_counts(dates, created_at, texts=None):
    """Per trading day t: number of articles with 16:00 ET(prev trading day) < ts <= 16:00 ET(day t); and how many of
    those match the earnings rule. `created_at`: tz-aware timestamps; `texts`: matching strings (headline+summary+tags)."""
    closes = (pd.DatetimeIndex(dates) + pd.Timedelta(hours=16)).tz_localize("US/Eastern")
    cl = closes.tz_convert("UTC").asi8
    ts = pd.DatetimeIndex(created_at).tz_convert("UTC").asi8 if len(created_at) else np.array([], dtype=np.int64)
    idx = np.searchsorted(cl, ts, side="left")          # first close >= ts  =>  closes[idx-1] < ts <= closes[idx]
    ok = idx < len(cl)
    n_news = np.bincount(idx[ok], minlength=len(cl))[:len(cl)]
    if texts is None:
        return n_news, np.zeros(len(cl), int)
    em = np.array([bool(EARN_RE.search(t)) for t in texts], bool) if len(texts) else np.zeros(0, bool)
    n_earn = np.bincount(idx[ok & em], minlength=len(cl))[:len(cl)]
    return n_news, n_earn


def _text(df):
    def one(r):
        extra = " ".join(str(x) for col in ("tags", "channels") for x in (r[col] if isinstance(r[col], list) else [])
                         for x in [x.get("name", x) if isinstance(x, dict) else x])
        return f"{r['headline']} {r['summary']} {extra}"
    return [one(r) for _, r in df.iterrows()] if len(df) else []


class Stock:
    def __init__(self, sym, px=None, news=None):
        self.sym = sym
        df = data.load(sym) if px is None else px
        self.dates = pd.DatetimeIndex(df.index)
        self.open, self.close = df.Open.values.astype(float), df.Close.values.astype(float)
        art = news_data.load(sym) if news is None else news
        self.n_art = len(art)
        self.n_news, self.n_earn = news_counts(self.dates, art["created_at"], _text(art))
        self._sig = {}

    def sig(self, k, scope):
        """sign (+1/-1/0) of the event on each day for scope: 'raw' (any shock), 'all', 'earn', 'nonews'."""
        key = (k, scope)
        if key not in self._sig:
            sign = shock_arrays(self.close, k)[2]
            m = {"raw": True, "all": self.n_news >= 1, "earn": self.n_earn >= 1, "nonews": self.n_news == 0}[scope]
            self._sig[key] = np.where(m, sign, 0)
        return self._sig[key]

    def window(self, per):
        lo = int(self.dates.searchsorted(pd.Timestamp(per[0]), "left"))
        hi = int(self.dates.searchsorted(pd.Timestamp(per[1]), "right")) - 1       # last day inside the window
        return lo, hi


# ------------------------------------------------------------------ trades
def take_trades(st, sig, mapping, h, per):
    """Non-overlapping trades. Event on day t: entry = Open_{t+1}, exit = Open_{t+1+h}. Signal day in `per`, exit day in
    `per`. An event whose entry is before the open position's exit is skipped (entry on the exit day is allowed)."""
    lo, hi = st.window(per)
    t_, e_, x_, d_ = [], [], [], []
    last_exit = -1
    for t in range(max(lo, 0), hi + 1):
        s = sig[t]
        if s == 0 or s not in mapping:
            continue
        ent, ex = t + 1, t + 1 + h
        if ex > hi or ex >= len(st.open):
            continue
        if ent < last_exit:
            continue
        t_.append(t); e_.append(ent); x_.append(ex); d_.append(mapping[s]); last_exit = ex
    return {"t": np.array(t_, int), "ent": np.array(e_, int), "ex": np.array(x_, int), "dir": np.array(d_, int), "h": h}


def net_returns(st, tr, cost):
    """Net return per trade: long = O_x/O_e - 1 - 2c; short = -(O_x/O_e - 1) - 2c - borrow*h/252."""
    if not len(tr["t"]):
        return np.array([])
    g = st.open[tr["ex"]] / st.open[tr["ent"]] - 1
    return tr["dir"] * g - 2 * cost - (tr["dir"] < 0) * BORROW * tr["h"] / 252


def daily_parts(st, tr, cal_pos, n_cal):
    """Per-calendar-day gross return and number of cost sides for one stock's trades (open-to-open daily returns, borrow
    included in gross; the entry day carries one side and the day before the exit open carries one side)."""
    gross, sides = np.zeros(n_cal), np.zeros(n_cal)
    o = st.open
    for e, x, d in zip(tr["ent"], tr["ex"], tr["dir"]):
        for day in range(e, x):
            r = o[day + 1] / o[day] - 1
            gross[cal_pos[day]] += d * r - (BORROW / 252 if d < 0 else 0.0)
        sides[cal_pos[e]] += 1
        sides[cal_pos[x - 1]] += 1
    return gross, sides


def calendar(stocks, per):
    cal = sorted(set().union(*[set(s.dates[s.window(per)[0]:s.window(per)[1] + 1]) for s in stocks]))
    return pd.DatetimeIndex(cal)


def sharpe(r):
    r = np.asarray(r, float)
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return float(r.mean() / sd * np.sqrt(252)) if sd > 1e-12 else 0.0


class Group:
    """A set of stocks with a calendar for a period; evaluates one (type, scope, k, h)."""

    def __init__(self, stocks, per):
        self.stocks, self.per = stocks, per
        self.cal = calendar(stocks, per)
        self.pos = [np.searchsorted(self.cal.asi8, s.dates.asi8) .clip(0, len(self.cal) - 1) for s in stocks]
        self.w = 1.0 / len(stocks)

    def run(self, typ, scope, k, h):
        trades = [take_trades(s, s.sig(k, scope), TYPES[typ], h, self.per) for s in self.stocks]
        gross, sides = np.zeros(len(self.cal)), np.zeros(len(self.cal))
        for s, tr, p in zip(self.stocks, trades, self.pos):
            if len(tr["t"]):
                g, sd = daily_parts(s, tr, p, len(self.cal))
                gross += g * self.w; sides += sd * self.w
        return {"trades": trades, "r1": gross - COST * sides, "r2": gross - STRESS * sides}

    def net(self, res, cost):
        rets = [net_returns(s, tr, cost) for s, tr in zip(self.stocks, res["trades"])]
        return np.concatenate(rets) if rets else np.array([])

    def buy_hold(self):
        r = np.zeros(len(self.cal))
        for s, p in zip(self.stocks, self.pos):
            lo, hi = s.window(self.per)
            for d in range(lo, hi + 1):
                if d + 1 < len(s.open):
                    r[p[d]] += (s.open[d + 1] / s.open[d] - 1) * self.w
        return r


def summarize(g, res):
    n1, n2 = g.net(res, COST), g.net(res, STRESS)
    return {"trades": int(len(n1)), "mean_bp": float(n1.mean() * 1e4) if len(n1) else None,
            "mean_bp_2x": float(n2.mean() * 1e4) if len(n2) else None,
            "hit": float((n1 > 0).mean()) if len(n1) else None,
            "sharpe": sharpe(res["r1"]) if len(n1) else 0.0, "sharpe_2x": sharpe(res["r2"]) if len(n1) else 0.0,
            "total": float(np.prod(1 + res["r1"]) - 1)}


# ------------------------------------------------------------------ coin flip
def coin_flip(stocks, trades, per, cost, seed, n_sims=N_SIMS):
    """Pooled random-entry test: per stock the same number of trades, same hold, same direction mix at random
    non-overlapping entries in the window; value = sum of net returns pooled. Returns None if no trades."""
    total_obs, total_sim, any_ = 0.0, np.zeros(n_sims), False
    for i, (s, tr) in enumerate(zip(stocks, trades)):
        k = len(tr["t"])
        if not k:
            continue
        any_ = True
        lo, hi = s.window(per)
        o = s.open[lo + 1:hi + 1]
        L = len(o)
        h, d = tr["ex"] - tr["ent"], tr["dir"]
        total_obs += float(net_returns(s, tr, cost).sum())
        free = (L - 1) - int(h.sum())
        if free < 0:
            return None
        rng = np.random.default_rng(seed + 7919 * i)
        perm = np.argsort(rng.random((n_sims, k)), axis=1)
        hp, dp = h[perm], d[perm]
        cuts = np.sort(rng.integers(0, free + 1, size=(n_sims, k)), axis=1)
        st_ = cuts + np.cumsum(hp, axis=1) - hp
        g = o[st_ + hp] / o[st_] - 1
        total_sim += np.sum(dp * g - 2 * cost - (dp < 0) * BORROW * hp / 252, axis=1)
    if not any_:
        return None
    counts, edges = np.histogram(total_sim, bins=30)
    return {"p_value": (1 + int(np.sum(total_sim >= total_obs))) / (1 + n_sims), "strategy_value": total_obs,
            "median_random": float(np.median(total_sim)), "hist": {"edges": edges.tolist(), "counts": counts.tolist()}}


def _seed(*parts):
    return SEED + zlib.crc32("|".join(map(str, parts)).encode()) % 1000003


# ------------------------------------------------------------------ the study
def evaluate(disc):
    gt, gx = Group(disc, TUNE), Group(disc, TEST)
    bh = gx.buy_hold()
    grid, rows = [], []
    for typ, scope in itertools.product(TYPES, SCOPES):
        sid = f"news_{typ}_{scope}"
        cand = []
        for k, h in itertools.product(KS, HS):
            rt = gt.run(typ, scope, k, h)
            ms = summarize(gt, rt)
            rx = gx.run(typ, scope, k, h)
            mx = summarize(gx, rx)
            cf = coin_flip(disc, rx["trades"], TEST, COST, _seed(sid, k, h))
            row = {"strategy": sid, "type": typ, "scope": scope, "k": k, "h": h, "tune": ms, "test": mx,
                   "coin_flip": cf, "p": cf["p_value"] if cf else 1.0, "_rx": rx, "_rt": rt}
            grid.append(row); cand.append(row)
        ok = [r for r in cand if r["tune"]["trades"] >= MIN_TUNE_TRADES] or cand
        best = max(ok, key=lambda r: r["tune"]["sharpe"])
        best["tuned"] = True
        best["n_trials"] = len(cand)
        rows.append(best)
    qs = bh_qvalues([r["p"] for r in grid])
    for r, q in zip(grid, qs):
        r["q"] = float(q)
        r.setdefault("tuned", False)
    out_rows = []
    for r in rows:
        mx, rx = r["test"], r["_rx"]
        trial = [x["_rt"]["r1"].mean() / x["_rt"]["r1"].std(ddof=1) for x in grid
                 if x["strategy"] == r["strategy"] and x["_rt"]["r1"].std(ddof=1) > 1e-12]
        dsr = deflated_sharpe(rx["r1"], trial) if len(trial) > 1 and rx["r1"].std(ddof=1) > 1e-12 else None
        cand = bool(r["coin_flip"] and r["p"] < 0.05 and r["q"] < 0.10 and mx["sharpe"] > 0 and mx["sharpe_2x"] > 0
                    and (mx["mean_bp"] or 0) > 0)
        cum = np.cumprod(1 + rx["r1"])
        out_rows.append({
            "strategy": r["strategy"], "asset": "MEGACAP10", "type": r["type"], "scope": r["scope"],
            "params": {"k": r["k"], "h": r["h"]}, "n_trials": r["n_trials"], "is_sharpe": r["tune"]["sharpe"],
            "is_trades": r["tune"]["trades"],
            "oos": {"sharpe": mx["sharpe"], "total": mx["total"], "trades": mx["trades"], "win_rate": mx["hit"],
                    "avg_trade_bp": mx["mean_bp"], "mean_bp_2x": mx["mean_bp_2x"]},
            "oos_cost2x_sharpe": mx["sharpe_2x"],
            "buy_hold_oos": {"sharpe": sharpe(bh), "total": float(np.prod(1 + bh) - 1)},
            "coin_flip": dict(r["coin_flip"], q_value=r["q"]) if r["coin_flip"] else None,
            "deflated_sharpe": dsr, "tuned": True, "candidate": cand,
            "window": [str(gx.cal[0].date()), str(gx.cal[-1].date())],
            "equity": {"dates": [str(x.date()) for x in gx.cal[::5]], "strategy": [round(float(x), 4) for x in cum[::5]],
                       "buy_hold": [round(float(x), 4) for x in np.cumprod(1 + bh)[::5]]}})
    return grid, out_rows, gt, gx, bh


def grid_public(grid):
    return [{"strategy": r["strategy"], "type": r["type"], "scope": r["scope"], "k": r["k"], "h": r["h"],
             "tuned": r["tuned"], "tune": r["tune"], "test": dict(r["test"], p=r["p"], q=r["q"],
                                                                  median_random=(r["coin_flip"] or {}).get("median_random"))}
            for r in grid]


def events_per_year(stocks):
    """Raw shock counts by year per k: any shock / with news / earnings / no news, split positive and negative."""
    out = {}
    for k in KS:
        tab = {}
        for s in stocks:
            yrs = s.dates.year.values
            for scope in ("raw", "all", "earn", "nonews"):
                sg = s.sig(k, scope)
                for y in np.unique(yrs[yrs >= 2016]):
                    m = yrs == y
                    t = tab.setdefault(str(y), {}).setdefault(scope, {"pos": 0, "neg": 0})
                    t["pos"] += int((sg[m] > 0).sum()); t["neg"] += int((sg[m] < 0).sum())
                    t2 = tab[str(y)].setdefault("by_stock", {}).setdefault(s.sym, {"all": 0})
                    if scope == "all":
                        t2["all"] += int((sg[m] != 0).sum())
        out[f"k{k}"] = tab
    return out


def news_share(stocks):
    out = {}
    for k in KS:
        raw = sum(int((s.sig(k, "raw")[s.dates >= "2016-01-01"] != 0).sum()) for s in stocks)
        allv = sum(int((s.sig(k, "all")[s.dates >= "2016-01-01"] != 0).sum()) for s in stocks)
        out[f"k{k}"] = {"shock_days": raw, "with_news": allv, "share": allv / raw if raw else None}
    return out


def nonews_control(gx):
    rows = []
    for typ, k, h in itertools.product("abcd", KS, HS):
        res = gx.run(typ, "nonews", k, h)
        m = summarize(gx, res)
        rows.append({"type": typ, "k": k, "h": h, "trades": m["trades"], "mean_bp": m["mean_bp"], "hit": m["hit"], "sharpe": m["sharpe"]})
    return rows


def nvda_descriptive(nv):
    """NVDA alone: tune/test/full-period counts, mean net event return, hit rate; coin-flip p (descriptive)."""
    out = []
    for typ, k, h in itertools.product("abcd", KS, HS):
        row = {"type": typ, "k": k, "h": h}
        for name, per in (("tune", TUNE), ("test", TEST), ("full", ("2016-01-01", "2100-01-01"))):
            g = Group([nv], per)
            res = g.run(typ, "all", k, h)
            m = summarize(g, res)
            cf = coin_flip([nv], res["trades"], per, COST, _seed("nvda", typ, k, h, name))
            row[name] = {"trades": m["trades"], "mean_bp": m["mean_bp"], "hit": m["hit"], "sharpe": m["sharpe"],
                         "p": cf["p_value"] if cf else None}
        out.append(row)
    return out


def lockbox(cands):
    lock = [Stock(s) for s in LOCK]
    g = Group(lock, LOCKPER)
    out = []
    for r in cands:
        k, h = r["params"]["k"], r["params"]["h"]
        res = g.run(r["type"], r["scope"], k, h)
        ps = []
        for s in lock:
            gs = Group([s], LOCKPER)
            rs = gs.run(r["type"], r["scope"], k, h)
            cf = coin_flip([s], rs["trades"], LOCKPER, COST, _seed("lock", r["strategy"], s.sym))
            ps.append({"asset": s.sym, "trades": int(len(rs["trades"][0]["t"])), "p_value": cf["p_value"] if cf else 1.0})
        m = summarize(g, res)
        fp = fisher_p([x["p_value"] for x in ps])
        out.append({"strategy": r["strategy"], "params": r["params"], "assets": ps, "fisher_p": fp, "pooled": m,
                    "confirmed": bool(fp < 0.05 and m["sharpe"] > 0 and (m["mean_bp"] or 0) > 0),
                    "rule": "Fisher p < 0.05 and pooled Sharpe > 0 and pooled mean net event return > 0"})
    return out


def main():
    disc = [Stock(s) for s in DISC]
    info = [{"asset": s.sym, "articles": s.n_art, "first_price": str(s.dates[0].date()), "last_price": str(s.dates[-1].date())}
            for s in disc]
    print("loaded", [(i["asset"], i["articles"]) for i in info], flush=True)
    grid, rows, gt, gx, bh = evaluate(disc)
    cands = [r for r in rows if r["candidate"]]
    lb = lockbox(cands) if cands else []
    for r in rows:
        r["lockbox"] = next((x for x in lb if x["strategy"] == r["strategy"]), None)
    nv = next(s for s in disc if s.sym == "NVDA")
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan": PLAN, "cost_per_side": COST,
           "stress_per_side": STRESS, "borrow_per_year": BORROW, "n_sims": N_SIMS, "tune": list(TUNE), "test": [TEST[0], "end of data"],
           "universe": DISC, "lockbox_symbols": LOCK, "data": info,
           "strategies": [{"id": f"news_{t}_{sc}", "name": f"News shock: {NAMES[t]}" + (" (earnings days)" if sc == "earn" else ""),
                           "source": "Study 12 (NEWS_PLAN_12.md)", "grid_size": len(KS) * len(HS)} for t in TYPES for sc in SCOPES],
           "results": rows, "grid": grid_public(grid), "events_per_year": events_per_year(disc), "news_share": news_share(disc),
           "nonews_control": nonews_control(gx), "nvda": nvda_descriptive(nv), "lockbox": lb,
           "candidates": [r["strategy"] for r in cands]}
    OUT.write_text(json.dumps(out, default=float))
    print(f"wrote {OUT}; candidates: {out['candidates'] or 'none'}")
    report(out)


def report(out):
    print("\nnews share", out["news_share"])
    for k in ("k2", "k3"):
        print(f"\nevents per year {k} (raw shocks pos/neg | with news | earnings | no news)")
        for y, t in sorted(out["events_per_year"][k].items()):
            print(f"  {y}: raw {t['raw']['pos']}/{t['raw']['neg']} | news {t['all']['pos']}/{t['all']['neg']} | "
                  f"earn {t['earn']['pos']}/{t['earn']['neg']} | none {t['nonews']['pos']}/{t['nonews']['neg']}")
    print("\nTEST years, scope all: type k h | trades mean_bp hit Sh1 Sh2 p q")
    for r in out["grid"]:
        if r["scope"] == "all" and r["type"] in "abcd" and len(r["type"]) == 1:
            t = r["test"]
            print(f"  {r['type']} k{r['k']} h{r['h']:2d} | {t['trades']:4d} {t['mean_bp'] if t['mean_bp'] is not None else float('nan'):7.1f} "
                  f"{(t['hit'] or 0):.2f} {t['sharpe']:5.2f} {t['sharpe_2x']:5.2f} p={t['p']:.3f} q={t['q']:.3f}")


if __name__ == "__main__":
    main()
