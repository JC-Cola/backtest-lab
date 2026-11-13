"""Study 17 runner (ML4T_PLAN_17.md): (a) meta-labeling filter on the intraday noise band, (b) IC diagnostic of System C's
ratings, (c) factor evaluation of qlib's predictions (waits for results/qlib15.json).

    python src/ml4t17.py              # (a) discovery on SPY, QQQ + (b) + (c)
    python src/ml4t17.py --lockbox    # also run (a) on IWM, DIA when the discovery produced a candidate
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import intraday as I
import ml4t_ic as IC
import ml4t_meta as M
from ml4t_cv import oof_predict
from run import fisher_p
from stats import bh_qvalues, deflated_sharpe

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "ml4t17.json"
DISC, LOCK = ["SPY", "QQQ"], ["IWM", "DIA"]
THRESHOLDS = [0.0, 0.45, 0.48, 0.50, 0.52, 0.54, 0.56, 0.58, 0.60]
MIN_KEEP = 0.25
N_SIMS, SEED = 2000, 20261114
LOOKBACK, CHECK = 14, 60


def keep_mask(p, t):
    return np.ones(len(p), bool) if t == 0 else np.asarray(p) > t


def is_slice(D):
    return slice(0, int(D.dates.searchsorted(pd.Timestamp(I.IS_END), "right")))


def oos_slice(D):
    return slice(int(D.dates.searchsorted(pd.Timestamp(I.OOS_START))), D.n)


def veto_coin_flip(D, tab_x, kept, sl, n_sims=N_SIMS, seed=SEED):
    """2x-cost Sharpe of the filter vs 2,000 random vetoes keeping the same number of the plain test trades."""
    n, k = len(tab_x), int(kept.sum())
    ret2 = tab_x["ret"].values - 2 * I.COST
    day = tab_x["day"].values - sl.start
    L = sl.stop - sl.start

    def sh(mask_idx):
        r = np.bincount(day[mask_idx], weights=ret2[mask_idx], minlength=L)
        return I.sharpe(r)

    act = sh(np.flatnonzero(kept))
    if k == n:
        return 1.0, act
    rng = np.random.default_rng(seed)
    vals = np.array([sh(rng.choice(n, k, replace=False)) for _ in range(n_sims)])
    return (1 + int((vals >= act - 1e-12).sum())) / (n_sims + 1), act


def run_set(syms, label="y", log=print):
    """The whole procedure on a pool of markets: build trades, purged-CV tuning of the threshold on 2016-2020, one frozen test."""
    from sklearn.metrics import roc_auc_score
    vix = M.load_vix()
    Ds = {s: I.Days(s) for s in syms}
    tabs = {}
    for s, D in Ds.items():
        t = M.trade_table(D, vix, LOOKBACK, CHECK)
        t["date"] = D.dates[t["day"].values]
        tabs[s] = t
    all_dates = sorted(set().union(*[set(D.dates) for D in Ds.values()]))
    rank = {d: i for i, d in enumerate(all_dates)}
    is_end, oos = pd.Timestamp(I.IS_END), pd.Timestamp(I.OOS_START)
    tune = {s: t[t["date"] <= is_end].reset_index(drop=True) for s, t in tabs.items()}
    test = {s: t[t["date"] >= oos].reset_index(drop=True) for s, t in tabs.items()}
    Xt = pd.concat([tune[s] for s in syms], ignore_index=True)
    pos = np.array([rank[d] for d in Xt["date"]])
    X, y = Xt[M.FEATURES].values, Xt[label].values
    oof = oof_predict(X, y, pos, M.fit_predict, n_blocks=5, purge=5)
    # tuning: pooled equal-weight daily series of the filtered markets at 2x costs
    off, grid = 0, []
    parts = {}
    for s in syms:
        parts[s] = oof[off:off + len(tune[s])]
        off += len(tune[s])
    for t in THRESHOLDS:
        series, kept, tot = [], 0, 0
        for s in syms:
            D = Ds[s]
            m = keep_mask(parts[s], t)
            r2 = pd.Series(M.daily_from_mask(D, tune[s], m, is_slice(D), 2), index=D.dates[is_slice(D)])
            r1 = pd.Series(M.daily_from_mask(D, tune[s], m, is_slice(D), 1), index=D.dates[is_slice(D)])
            series.append((r2, r1))
            kept += int(m.sum()); tot += len(m)
        r2 = pd.concat([a for a, _ in series], axis=1).fillna(0).mean(axis=1).values
        r1 = pd.concat([b for _, b in series], axis=1).fillna(0).mean(axis=1).values
        grid.append({"threshold": t, "keep_share": kept / tot, "tuning_sharpe_2x": I.sharpe(r2), "tuning_sharpe_1x": I.sharpe(r1),
                     "tuning_sharpe_1x_daily": float(r1.mean() / r1.std(ddof=1)) if r1.std(ddof=1) > 0 else 0.0})
    ok = [g for g in grid if g["keep_share"] >= MIN_KEEP]
    best = max(ok, key=lambda g: g["tuning_sharpe_2x"])
    thr = best["threshold"]
    model = M.make_model().fit(X, y)
    imp = model.booster_.feature_importance("gain")
    res = {"label": label, "markets": syms, "grid": grid, "threshold": thr, "n_tuning_trades": int(len(Xt)),
           "oof_auc": float(roc_auc_score(y, oof)) if len(np.unique(y)) > 1 else None,
           "feature_gain_share": dict(zip(M.FEATURES, (imp / imp.sum()).round(4).tolist())), "test": {}}
    trial_sh = np.array([g["tuning_sharpe_1x_daily"] for g in grid])
    for s in syms:
        D, tx = Ds[s], test[s]
        sl = oos_slice(D)
        p = model.predict_proba(tx[M.FEATURES].values)[:, 1] if len(tx) else np.array([])
        kept = keep_mask(p, thr)
        plain = np.ones(len(tx), bool)
        row = {"trades_plain": int(len(tx)), "trades_kept": int(kept.sum()), "keep_share": float(kept.mean()) if len(tx) else None,
               "test_auc": float(roc_auc_score(tx[label], p)) if len(tx) and tx[label].nunique() > 1 else None,
               "win_rate_plain": float((tx["ret"] > 0).mean()), "win_rate_kept": float((tx["ret"][kept] > 0).mean()) if kept.any() else None}
        for mult, key in ((1, "cost1x"), (2, "cost2x")):
            row[key] = {"plain": M.summarize(M.daily_from_mask(D, tx, plain, sl, mult), len(tx)),
                        "filtered": M.summarize(M.daily_from_mask(D, tx, kept, sl, mult), int(kept.sum()))}
        pval, _ = veto_coin_flip(D, tx, kept, sl)
        row["coin_flip_p_2x"] = pval
        row["deflated_sharpe_1x"] = deflated_sharpe(M.daily_from_mask(D, tx, kept, sl, 1), trial_sh)
        c2 = row["cost2x"]
        row["beats_plain_2x_sharpe"] = bool(thr > 0 and c2["filtered"]["sharpe"] > c2["plain"]["sharpe"])
        row["dd_no_worse"] = bool(c2["filtered"]["max_drawdown"] >= c2["plain"]["max_drawdown"])
        row["positive_2x"] = bool(c2["filtered"]["sharpe"] > 0)
        res["test"][s] = row
    q = bh_qvalues([res["test"][s]["coin_flip_p_2x"] for s in syms])
    for s, qq in zip(syms, q):
        res["test"][s]["coin_flip_q"] = float(qq)
        r = res["test"][s]
        r["market_pass"] = bool(r["beats_plain_2x_sharpe"] and r["dd_no_worse"] and r["positive_2x"] and r["coin_flip_p_2x"] < 0.05 and qq < 0.10)
    res["candidate"] = bool(thr > 0 and all(res["test"][s]["market_pass"] for s in syms))
    res["sharpe_beat_and_dd_ok_all"] = bool(all(res["test"][s]["beats_plain_2x_sharpe"] and res["test"][s]["dd_no_worse"] for s in syms))
    res["fisher_p_coin_flip"] = fisher_p([res["test"][s]["coin_flip_p_2x"] for s in syms])
    return res


# ------------------------------------------------------------------ (b) System C
def system_c_part():
    import data as dailydata
    dec = {}
    live = json.loads((ROOT / "agent_memory" / "system_ta.json").read_text())
    bt = json.loads((ROOT / "results" / "system_ta_backtest.json").read_text())
    cutoff = bt.get("cutoff", "2026-03-31")
    for d in bt["decisions"]:
        dec[(d["date"], d["symbol"])] = {"date": d["date"], "symbol": d["symbol"], "rating": d["rating"], "clean": d["date"] > cutoff, "source": "backtest"}
    for d in live["decisions"]:
        dec[(d["date"], d["symbol"])] = {"date": d["date"], "symbol": d["symbol"], "rating": d["rating"], "clean": d["date"] > cutoff, "source": "live"}
    cache = {}

    def price_for(sym):
        if sym not in cache:
            px = dailydata.load(sym)
            cache[sym] = px[px.index < pd.Timestamp.today().normalize()]   # a bar dated today may still be forming
        return cache[sym]
    out = IC.rating_ic_table(sorted(dec.values(), key=lambda x: (x["date"], x["symbol"])), price_for)
    out["cutoff"] = cutoff
    return out


# ------------------------------------------------------------------ (c) qlib
def qlib_part():
    """Needs daily scores: results/qlib15_scores.csv (date, instrument, score) or a `scores` block inside results/qlib15.json."""
    p, pc = ROOT / "results" / "qlib15.json", ROOT / "results" / "qlib15_scores.csv"
    scores, why = None, "results/qlib15.json does not exist yet"
    if pc.exists():
        r = pd.read_csv(pc, parse_dates=["date"])
        scores = r.pivot(index="date", columns="instrument", values="score").sort_index().astype(float)
    elif p.exists():
        try:
            d = json.loads(p.read_text())
            scores = load_qlib_scores(d)
            why = f"results/qlib15.json has no daily scores (keys: {sorted(d)[:12]}); a results/qlib15_scores.csv (date, instrument, score) would do"
        except Exception as e:  # noqa: BLE001
            why = f"results/qlib15.json unreadable: {e}"
    if scores is None:
        return {"status": "waiting on qlib15", "detail": why + "; the evaluator is src/ml4t_ic.py:factor_report (tested on synthetic data)"}
    close = load_closes(list(scores.columns))
    scores = scores[scores.index.isin(close.index)]
    return {"status": "evaluated", "report": IC.factor_report(scores, close.reindex(close.index))}


def load_qlib_scores(d):
    """Daily scores as a date x instrument frame from results/qlib15.json, whichever of the plausible layouts it uses."""
    for k in ("daily_scores", "scores", "predictions", "pred"):
        v = d.get(k)
        if not v:
            continue
        if isinstance(v, dict):
            df = pd.DataFrame(v).T if isinstance(next(iter(v.values())), dict) else None
            if df is not None:
                df.index = pd.to_datetime(df.index)
                return df.sort_index().astype(float)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            r = pd.DataFrame(v)
            dc = next((c for c in ("date", "datetime") if c in r), None)
            ic_ = next((c for c in ("instrument", "symbol", "ticker") if c in r), None)
            sc = next((c for c in ("score", "pred", "prediction") if c in r), None)
            if dc and ic_ and sc:
                r[dc] = pd.to_datetime(r[dc])
                return r.pivot(index=dc, columns=ic_, values=sc).sort_index().astype(float)
    return None


def load_closes(insts):
    base = ROOT / "data" / "qlib" / "csv"
    cols = {}
    for s in insts:
        f = base / f"{str(s).upper()}.csv"
        if f.exists():
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            c = "Close" if "Close" in df else ("close" if "close" in df else df.columns[3])
            cols[s] = df[c]
    return pd.DataFrame(cols).sort_index()


def main(lockbox=False):
    res = {"study": 17, "plan": "ML4T_PLAN_17.md", "generated": datetime.now(timezone.utc).isoformat()}
    a = {"discovery": run_set(DISC), "secondary_label_close": run_set(DISC, "y_close")}
    if lockbox and a["discovery"]["candidate"]:
        import intraday_data
        for s in LOCK:
            if not (intraday_data.DIR / f"{s}.pkl").exists():
                intraday_data.download(s)
        lb = run_set(LOCK)
        lb["confirmed"] = bool(lb["sharpe_beat_and_dd_ok_all"] and lb["fisher_p_coin_flip"] < 0.05)
        a["lockbox"] = lb
    elif lockbox:
        a["lockbox"] = {"skipped": "no candidate in discovery"}
    res["a_noise_band_meta_label"] = a
    res["b_system_c_ic"] = system_c_part()
    res["c_qlib_factor_eval"] = qlib_part()
    OUT.write_text(json.dumps(res, default=float))
    return res


if __name__ == "__main__":
    r = main("--lockbox" in sys.argv)
    d = r["a_noise_band_meta_label"]["discovery"]
    print("threshold", d["threshold"], "oof auc", d["oof_auc"], "candidate", d["candidate"])
    for g in d["grid"]:
        print(g)
    for s, t in d["test"].items():
        print(s, t["keep_share"], "test auc", t["test_auc"], "p", t["coin_flip_p_2x"])
        for k in ("cost1x", "cost2x"):
            print("  ", k, "plain", t[k]["plain"], "\n      filt ", t[k]["filtered"])
