"""Intraday study 11 (INTRADAY_PLAN_11.md): volatility-targeted sizing and a VIX regime filter on the live noise-band
breakout (lookback 14, check 60). The signal is intraday.noise_breakout, unchanged; every variant uses the same trades
and only changes which days are traded and how big each day's position is.

    python src/intraday11.py              # discovery on SPY and QQQ
    python src/intraday11.py --lockbox    # also run a candidate on IWM, DIA (downloads them)
"""
from __future__ import annotations

import itertools
import json
import sys
import zlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import intraday as I
from intraday import in_window, noise_breakout, sharpe
from run import fisher_p
from stats import bh_qvalues, deflated_sharpe

DISCOVERY, LOCKBOX = ["SPY", "QQQ"], ["IWM", "DIA"]
OUT = I.ROOT / "results" / "intraday11.json"
LOOKBACK, CHECK, VOL_N, VIX_N = 14, 60, 14, 20
SIZINGS = [None, (0.01, 1.0), (0.01, 2.0), (0.02, 1.0), (0.02, 2.0)]   # None = size 1.0
FILTERS = ["none", "high", "low"]
GRID = [{"target": s[0] if s else None, "cap": s[1] if s else None, "filter": f}
        for s, f in itertools.product(SIZINGS, FILTERS) if not (s is None and f == "none")]
PLAIN = {"target": None, "cap": None, "filter": "none"}
N_BOOT, BLOCK, BOOT_SEED = 10000, 10, 20261107


# ---------------------------------------------------------------- inputs known before each day
def realized_vol(D, n=VOL_N):
    """vol[d] = sample std of the n daily close-to-close returns of sessions d-n .. d-1 (nothing from day d)."""
    ret = np.r_[np.nan, D.close[1:] / D.close[:-1] - 1]
    vol = np.full(D.n, np.nan)
    for d in range(n + 1, D.n):
        vol[d] = ret[d - n:d].std(ddof=1)
    return vol


def load_vix():
    s = pd.read_csv(I.ROOT / "data" / "ctx_VIX.csv", parse_dates=["Date"], index_col="Date")["Close"].dropna()
    return s.sort_index()


def vix_state(dates, vix, n=VIX_N):
    """(v, m) per day: the VIX close of the last trading day strictly before the date, and the mean of the n closes
    ending that day. NaN without enough history."""
    vals, vd = vix.values, vix.index
    v = np.full(len(dates), np.nan)
    m = np.full(len(dates), np.nan)
    for i, dt in enumerate(dates):
        j = int(vd.searchsorted(pd.Timestamp(dt), "left")) - 1
        if j >= n - 1:
            v[i], m[i] = vals[j], vals[j - n + 1:j + 1].mean()
    return v, m


def day_weights(vol, v, m, target, cap, flt):
    """Per-day multiplier: 0 on a day the filter skips, else the size (1.0 without sizing)."""
    ok = np.ones(len(vol), bool)
    if flt == "high":
        ok = v > m            # NaN compares False: not traded
    elif flt == "low":
        ok = v <= m
    if target is None:
        size = np.ones(len(vol))
    else:
        with np.errstate(divide="ignore", invalid="ignore"):
            size = np.minimum(cap, target / vol)
        size = np.where(np.isfinite(size), size, 0.0)
    return np.where(ok, size, 0.0)


# ---------------------------------------------------------------- returns of a variant
def daily_w(D, t, w, sl, mult=1):
    """Daily returns in window sl of trades t scaled by the day weights w (costs scale too); mult 2 charges one more
    round trip per trade, also scaled."""
    r = np.zeros(D.n)
    if len(t["ret"]):
        extra = 2 * I.COST if mult == 2 else 0.0
        np.add.at(r, t["day"], (t["ret"] - extra) * w[t["day"]])
    return r[sl]


def max_dd(r):
    eq = np.cumprod(1 + r)
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def stats(r):
    return {"sharpe": sharpe(r), "total": float(np.prod(1 + r) - 1), "worst_day": float(r.min()), "max_drawdown": max_dd(r)}


def coin_vals_w(D, t, w, seed):
    """I.coin_vals with each trade weighted by its day's size (same days, count, holds, mix and exposure)."""
    k = len(t["ret"])
    if k == 0:
        return np.zeros(I.N_SIMS)
    rng = np.random.default_rng(seed)
    day, h = t["day"], np.maximum(1, t["exit_m"] - t["entry_m"])
    span = D.last[day] + 2 - h
    wt = w[day]
    vals = np.empty(I.N_SIMS)
    for a in range(0, I.N_SIMS, 200):
        b = min(I.N_SIMS, a + 200)
        e = (rng.random((b - a, k)) * np.maximum(span, 1)).astype(int)
        p_in, p_out = D.P[day, e], D.P[day, e + h]
        dirs = np.take_along_axis(np.tile(t["dir"], (b - a, 1)), np.argsort(rng.random((b - a, k)), axis=1), axis=1)
        vals[a:b] = np.nansum(wt * (dirs * (p_out / p_in - 1) - 2 * I.COST), axis=1)
    return vals


def block_index(n, rng, b=N_BOOT, L=BLOCK):
    """Circular block bootstrap indices, b x n."""
    nb = -(-n // L)
    start = rng.integers(0, n, (b, nb))
    return ((start[:, :, None] + np.arange(L)) % n).reshape(b, nb * L)[:, :n]


def paired_p(a, base, idx):
    """One-sided p that a (variant) does not beat base: bootstrap of the mean daily difference and of the Sharpe
    difference, each centred at its observed value (null: no difference)."""
    d = a - base
    obs_m = d.mean()
    obs_s = sharpe(a) - sharpe(base)
    bm = np.empty(len(idx)); bs = np.empty(len(idx))
    for i in range(0, len(idx), 2000):
        ix = idx[i:i + 2000]
        A, B = a[ix], base[ix]
        bm[i:i + 2000] = (A - B).mean(axis=1)
        sa = A.std(axis=1, ddof=1); sb = B.std(axis=1, ddof=1)
        bs[i:i + 2000] = (np.where(sa > 1e-12, A.mean(axis=1) / np.where(sa > 1e-12, sa, 1), 0)
                          - np.where(sb > 1e-12, B.mean(axis=1) / np.where(sb > 1e-12, sb, 1), 0)) * np.sqrt(252)
    n = len(idx)
    return {"mean_diff_bp": float(obs_m * 1e4), "p_mean": float((1 + np.sum(bm - obs_m >= obs_m)) / (1 + n)),
            "sharpe_diff": float(obs_s), "p_sharpe": float((1 + np.sum(bs - obs_s >= obs_s)) / (1 + n))}


# ---------------------------------------------------------------- one market
def evaluate_market(sym, D, vix):
    import ftmo
    I_ = slice(0, int(D.dates.searchsorted(pd.Timestamp(I.IS_END), "right")))
    X = slice(int(D.dates.searchsorted(pd.Timestamp(I.OOS_START))), D.n)
    t = noise_breakout(D, lookback=LOOKBACK, check=CHECK)
    tx = {k: v[in_window(t, X)] for k, v in t.items()}
    vol = realized_vol(D)
    v, m = vix_state(D.dates, vix)
    one = np.ones(D.n)
    bh = np.r_[0.0, D.close[1:] / D.close[:-1] - 1][X]
    plain = {mult: daily_w(D, t, one, X, mult) for mult in (1, 2)}
    plain_row = {"asset": sym, "params": PLAIN, "trades": int(len(tx["ret"])), "cost1x": stats(plain[1]), "cost2x": stats(plain[2]),
                 "buy_hold": stats(bh)}
    idx = block_index(len(plain[1]), np.random.default_rng(BOOT_SEED + zlib.crc32(sym.encode()) % 100000))
    rows, is_sh_daily = [], []
    for p in GRID:
        w = day_weights(vol, v, m, p["target"], p["cap"], p["filter"])
        r_is = daily_w(D, t, w, I_)
        is_sh_daily.append(r_is.mean() / r_is.std(ddof=1) if r_is.std(ddof=1) > 1e-12 else 0.0)
        is_n = int((in_window(t, I_) & (w[t["day"]] > 0)).sum()) if len(t["ret"]) else 0
        r1, r2 = daily_w(D, t, w, X, 1), daily_w(D, t, w, X, 2)
        keep = w[tx["day"]] > 0
        tw = {k: x[keep] for k, x in tx.items()}
        wd = w[tw["day"]]
        seed = I.SEED + zlib.crc32(f"id_noise_volsize_vix{sym}{p}".encode()) % 100000
        vals = coin_vals_w(D, tw, w, seed)
        strat = float((tw["ret"] * wd).sum())
        counts, edges = np.histogram(vals, bins=30)
        cf = {"p_value": (1 + int(np.sum(vals >= strat))) / (1 + I.N_SIMS), "strategy_value": strat,
              "median_random": float(np.median(vals)), "hist": {"edges": edges.tolist(), "counts": counts.tolist()}} if len(wd) else None
        fin, low = ftmo.day_paths(D, t)
        hold = (tw["exit_m"] - tw["entry_m"]).astype(float)
        rows.append({
            "strategy": "id_noise_volsize_vix", "asset": sym, "params": p, "n_trials": len(GRID),
            "is_sharpe": sharpe(r_is), "is_trades": is_n,
            "oos": {"sharpe": sharpe(r1), "cagr": I.cagr(r1), "total": float(np.prod(1 + r1) - 1), "trades": int(len(wd)),
                    "win_rate": float((tw["ret"] > 0).mean()) if len(wd) else None,
                    "avg_trade_bp": float((tw["ret"] * wd).mean() * 1e4) if len(wd) else None,
                    "avg_hold_min": float(hold[hold > 0].mean()) if (hold > 0).any() else None,
                    "days_traded": float((w[X] > 0).mean()), "avg_size": float(w[X][w[X] > 0].mean()) if (w[X] > 0).any() else 0.0},
            "oos_cost2x_sharpe": sharpe(r2), "buy_hold_oos": {"sharpe": sharpe(bh), "cagr": I.cagr(bh)},
            "coin_flip": cf, "ftmo": ftmo.table_from_paths(fin * w, low * w, X.start), "window": [str(D.dates[X.start].date()), str(D.dates[-1].date())],
            "equity": {"dates": [str(x.date()) for x in D.dates[X][::5]], "strategy": [round(float(x), 4) for x in np.cumprod(1 + r1)[::5]],
                       "buy_hold": [round(float(x), 4) for x in np.cumprod(1 + bh)[::5]]},
            "cost1x": stats(r1), "cost2x": stats(r2),
            "vs_plain_cost1x": paired_p(r1, plain[1], idx), "vs_plain_cost2x": paired_p(r2, plain[2], idx),
            "plain_cost2x_sharpe": plain_row["cost2x"]["sharpe"], "plain_cost2x_max_drawdown": plain_row["cost2x"]["max_drawdown"],
            "_r1": r1})
    ok = [i for i in range(len(rows)) if rows[i]["is_trades"] >= I.MIN_IS_TRADES] or list(range(len(rows)))
    b = max(ok, key=lambda i: rows[i]["is_sharpe"])
    med = float(np.median([r["oos"]["sharpe"] for r in rows]))
    for i, r in enumerate(rows):
        r["tuned"] = i == b
        r["grid_median_oos_sharpe"] = med
        r["beats_plain"] = bool(r["cost2x"]["sharpe"] > plain_row["cost2x"]["sharpe"]
                                and r["cost2x"]["max_drawdown"] >= plain_row["cost2x"]["max_drawdown"])
    rows[b]["deflated_sharpe"] = deflated_sharpe(rows[b].pop("_r1"), np.array(is_sh_daily))
    for r in rows:
        r.pop("_r1", None)
    return rows, plain_row


def finish(rows):
    """BH q over all coin-flip tests, then the candidate flag on the tuned variants."""
    tested = [r for r in rows if r["coin_flip"]]
    for r, q in zip(tested, bh_qvalues([r["coin_flip"]["p_value"] for r in tested])):
        r["coin_flip"]["q_value"] = float(q)
        r["candidate"] = bool(r["tuned"] and r["coin_flip"]["p_value"] < 0.05 and q < 0.10 and r["oos"]["sharpe"] > 0
                              and r["oos_cost2x_sharpe"] > 0 and r["beats_plain"])
    return rows


def lockbox(markets=LOCKBOX):
    import intraday_data
    out = []
    for s in markets:
        intraday_data.download(s)
        rows, plain = evaluate_market(s, I.Days(s), load_vix())
        r = next(x for x in rows if x["tuned"])
        out.append({"asset": s, "p_value": r["coin_flip"]["p_value"] if r["coin_flip"] else 1.0, "oos_sharpe": r["oos"]["sharpe"],
                    "params": r["params"], "cost2x_sharpe": r["cost2x"]["sharpe"], "plain_cost2x_sharpe": plain["cost2x"]["sharpe"],
                    "max_drawdown_2x": r["cost2x"]["max_drawdown"], "plain_max_drawdown_2x": plain["cost2x"]["max_drawdown"]})
    fp = fisher_p([o["p_value"] for o in out])
    med = float(np.median([o["oos_sharpe"] for o in out]))
    return {"markets": out, "fisher_p": fp, "median_oos_sharpe": med, "confirmed": bool(fp < 0.05 and med > 0)}


def main():
    vix = load_vix()
    rows, plains = [], []
    for sym in DISCOVERY:
        D = I.Days(sym)
        print(f"== {sym}: {D.n} days {D.dates[0].date()}..{D.dates[-1].date()}", flush=True)
        r, pl = evaluate_market(sym, D, vix)
        rows += r; plains.append(pl)
    finish(rows)
    for pl in plains:
        print(f"\n{pl['asset']} plain: 1x Sh {pl['cost1x']['sharpe']:.2f} tot {pl['cost1x']['total']*100:.1f}% dd {pl['cost1x']['max_drawdown']*100:.1f}% | "
              f"2x Sh {pl['cost2x']['sharpe']:.2f} dd {pl['cost2x']['max_drawdown']*100:.1f}% | B&H Sh {pl['buy_hold']['sharpe']:.2f}")
        for r in [x for x in rows if x["asset"] == pl["asset"]]:
            p, a, b = r["params"], r["cost1x"], r["cost2x"]
            print(f"  {'*' if r['tuned'] else ' '} tgt {str(p['target']):5s} cap {str(p['cap']):4s} {p['filter']:4s} IS {r['is_sharpe']:5.2f} | "
                  f"1x Sh {a['sharpe']:5.2f} tot {a['total']*100:7.1f}% dd {a['max_drawdown']*100:6.1f}% worst {a['worst_day']*100:5.2f}% | "
                  f"2x Sh {b['sharpe']:5.2f} dd {b['max_drawdown']*100:6.1f}% | p_coin {r['coin_flip']['p_value']:.3f} q {r['coin_flip']['q_value']:.3f} | "
                  f"pair2x p_mean {r['vs_plain_cost2x']['p_mean']:.3f} p_sh {r['vs_plain_cost2x']['p_sharpe']:.3f} {'BEATS' if r['beats_plain'] else ''}")
    cands = [r for r in rows if r.get("candidate")]
    print(f"\ncandidates: {[(r['asset'], r['params']) for r in cands] or 'none'}")
    lb = None
    if cands and "--lockbox" in sys.argv:
        lb = lockbox()
        for r in cands:
            r["lockbox"] = lb
        print(f"lockbox: Fisher p={lb['fisher_p']:.3f} median Sharpe={lb['median_oos_sharpe']:.2f} -> {'CONFIRMED' if lb['confirmed'] else 'not confirmed'}")
    OUT.write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan": "INTRADAY_PLAN_11.md",
        "cost_per_side": I.COST, "n_sims": I.N_SIMS, "n_boot": N_BOOT, "boot_block": BLOCK, "discovery": DISCOVERY, "lockbox_symbols": LOCKBOX,
        "strategies": [{"id": "id_noise_volsize_vix", "name": "Noise-Band Vol Sizing + VIX Filter",
                        "source": "Zarattini, Aziz & Barbon (2024) vol targeting; VIX regime filter (study 11)",
                        "plain": "The live noise-band breakout, sized by recent volatility and/or traded only in high- or low-VIX regimes.",
                        "grid_size": len(GRID)}],
        "plain_rule": plains, "results": rows}, default=float))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
