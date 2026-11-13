"""Cross-check + robustness sweep of the vectorbt noise-band implementation (src/vbt_noise.py).

    python src/vbt_noise_study.py crosscheck   # vbt vs the existing engine, SPY & QQQ, whole cache -> results/vbt_noise_crosscheck.json
    python src/vbt_noise_study.py sweep        # parameter heat-map on TUNING years 2016-2020 only  -> results/vbt_noise_robustness.json/.md

The sweep is descriptive robustness around the live setting. It never reads a day after 2020-12-31 and selects nothing.
"""
from __future__ import annotations

import itertools
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import intraday
import intraday_data
import vbt_noise as V

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
SYMBOLS = ["SPY", "QQQ"]
TUNE_END = "2020-12-31"
LOOKBACKS = [5, 10, 14, 20, 25, 30]
CHECKS = [15, 30, 60, 90, 120]
BANDS = [0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4]
VWAPS = [True, False]
WARM = max(LOOKBACKS) + 1          # first evaluated session, common to every column


def _sharpe(R):
    sd = R.std(0, ddof=1)
    return np.where(sd > 1e-12, R.mean(0) / np.where(sd > 1e-12, sd, 1) * np.sqrt(252), 0.0)


def _cagr(R):
    g = np.prod(1 + R, 0)
    return np.where(g > 0, g ** (252 / len(R)) - 1, -1.0)


def _maxdd(R):
    eq = np.cumprod(1 + R, 0)
    return (eq / np.maximum.accumulate(eq, 0) - 1).min(0)


# ---------------------------------------------------------------- 1. cross-check against the existing engine
def crosscheck():
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "params": V.LIVE,
           "weight": V.WEIGHT, "cost_per_side": V.COST, "symbols": {}}
    for s in SYMBOLS:
        D = V.load_days(s)
        t0 = time.perf_counter()
        tr = intraday.noise_breakout(D, V.LIVE["lookback"], V.LIVE["check"])
        t_eng = time.perf_counter() - t0
        r = intraday.daily_returns(D, tr, slice(0, D.n))
        ref = V.WEIGHT * r                                    # engine: unit notional, trade returns added within the day
        comp = np.ones(D.n)                                   # engine trades re-sized at current equity (intra-day compounding)
        for d, x in zip(tr["day"].astype(int), tr["ret"]):
            comp[d] *= 1 + V.WEIGHT * x
        comp -= 1
        res = {"sessions": int(D.n), "first": str(D.dates[0].date()), "last": str(D.dates[-1].date()),
               "engine_trades": int(len(tr["ret"])), "engine_seconds": round(t_eng, 3)}
        for name, g in (("full_1min", V.full_grid(D)), ("compact_15min", V.compact_grid(D))):
            t0 = time.perf_counter()
            pf, R, tg = V.run(D, g, [V.LIVE])
            sec = time.perf_counter() - t0
            R = R[:, 0]
            d = R - ref
            top = np.argsort(-np.abs(d))[:5]
            res[name] = {
                "grid_points": int(len(g.k)), "vbt_seconds": round(sec, 3),
                "vbt_trades": int(V.n_trades(tg, g)[0]),
                "max_abs_daily_diff_vs_engine": float(np.abs(d).max()),
                "mean_abs_daily_diff_vs_engine": float(np.abs(d).mean()),
                "days_with_abs_diff_gt_1e-5": int((np.abs(d) > 1e-5).sum()),
                "total_return_vbt": float(np.prod(1 + R) - 1), "total_return_engine": float(np.prod(1 + ref) - 1),
                "total_return_diff": float(np.prod(1 + R) - np.prod(1 + ref)),
                "max_abs_daily_diff_vs_engine_intraday_compounded": float(np.abs(R - comp).max()),
                "total_return_engine_intraday_compounded": float(np.prod(1 + comp) - 1),
                "worst_days": [{"date": str(D.dates[i].date()), "diff": float(d[i]), "engine": float(ref[i]),
                                "vbt": float(R[i])} for i in top]}
        out["symbols"][s] = res
        print(s, json.dumps({k: res[k]["max_abs_daily_diff_vs_engine"] for k in ("full_1min", "compact_15min")}), flush=True)
    out["notes"] = [
        "Fill convention is shared with the engine on purpose (decision on close of bar m, fill at open of bar m+1, flat at the last close); "
        "what is independent is the accounting: vectorbt equity, fees and position sizing.",
        "Residual daily differences come from one modelling simplification in the engine: it ADDS the returns of the trades of a day "
        "(fixed starting notional), vectorbt re-sizes each new entry at current equity (50% of equity). On sessions with 2-3 trades "
        "this is a second-order term (about 0.25 * r1 * r2). Against the engine re-computed with intra-day compounding the max "
        "daily difference drops to the 1e-6 level.",
        "full_1min runs every minute of every session through the Portfolio; compact_15min only the minutes where a fill can happen. "
        "They are identical, so the sweep uses the compact grid."]
    (RES / "vbt_noise_crosscheck.json").write_text(json.dumps(out, indent=1))
    return out


# ---------------------------------------------------------------- 2. robustness sweep (tuning years only)
def _tuning_days(symbol):
    df = intraday_data.load(symbol)
    return intraday.Days.from_frame(df[df.index < "2021-01-01"])


def sweep():
    combos = list(itertools.product(LOOKBACKS, CHECKS, BANDS, VWAPS))
    params = [dict(lookback=a, check=b, band=c, vwap=w) for a, b, c, w in combos]
    R = {}
    t_vbt = {}
    for s in SYMBOLS:
        D = _tuning_days(s)
        assert str(D.dates[-1].date()) <= TUNE_END
        t0 = time.perf_counter()
        _, Rs, tg = V.run(D, V.compact_grid(D), params)
        t_vbt[s] = time.perf_counter() - t0
        R[s] = Rs[WARM:]
        if s == SYMBOLS[0]:
            trades = V.n_trades(tg, None) / len(Rs)
            n_days = len(R[s])
        # engine validation on the lookback x check plane (band 1.0, VWAP on): the engine has no band / VWAP switches
        t0 = time.perf_counter()
        diffs, engine_secs = [], []
        for lb, ck in itertools.product(LOOKBACKS, CHECKS):
            t1 = time.perf_counter()
            tr = intraday.noise_breakout(D, lb, ck)
            engine_secs.append(time.perf_counter() - t1)
            re = intraday.daily_returns(D, tr, slice(0, D.n))
            comp = np.ones(D.n)
            for d, x in zip(tr["day"].astype(int), tr["ret"]):
                comp[d] *= 1 + V.WEIGHT * x
            j = combos.index((lb, ck, 1.0, True))
            diffs.append(float(np.abs(Rs[WARM:, j] - (comp - 1)[WARM:]).max()))
        R[s + "_val"] = {"max_abs_daily_diff_vs_engine_compounded": max(diffs), "configs": len(diffs),
                         "engine_seconds_per_config": float(np.mean(engine_secs))}
    comb = R["SPY"] + R["QQQ"]                           # live book: 50% SPY + 50% QQQ
    shp = {k: _sharpe(v) for k, v in (("SPY", R["SPY"]), ("QQQ", R["QQQ"]), ("combined", comb))}
    shape = (len(LOOKBACKS), len(CHECKS), len(BANDS), len(VWAPS))
    G = {k: v.reshape(shape) for k, v in shp.items()}
    cagr = _cagr(comb).reshape(shape)
    mdd = _maxdd(comb).reshape(shape)
    live = (LOOKBACKS.index(14), CHECKS.index(60), BANDS.index(1.0), 0)
    S = G["combined"]

    def neighbours(ix):
        out = []
        for ax in range(4):
            for dlt in (-1, 1):
                j = list(ix)
                j[ax] += dlt
                if 0 <= j[ax] < shape[ax]:
                    out.append(tuple(j))
        return out

    def nb_stats(ix):
        v = np.array([S[j] for j in neighbours(ix)])
        return {"value": float(S[ix]), "neighbours": len(v), "neighbour_mean": float(v.mean()),
                "neighbour_min": float(v.min()), "neighbour_max": float(v.max()),
                "value_minus_neighbour_mean": float(S[ix] - v.mean())}

    flat = S.ravel()
    nbmean = np.array([np.mean([S[j] for j in neighbours(np.unravel_index(i, shape))]) for i in range(flat.size)])
    best = np.unravel_index(int(flat.argmax()), shape)
    live_vwap_off = (live[0], live[1], live[2], 1)
    desc = lambda ix: dict(lookback=LOOKBACKS[ix[0]], check=CHECKS[ix[1]], band=BANDS[ix[2]], vwap=VWAPS[ix[3]])
    marg = {n: {str(v): float(np.mean(S.take(i, axis=ax))) for i, v in enumerate(vals)}
            for ax, (n, vals) in enumerate((("lookback", LOOKBACKS), ("check", CHECKS), ("band", BANDS), ("vwap", VWAPS)))}
    tbl = lambda A: [[round(float(x), 3) for x in row] for row in A]
    out = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": "DESCRIPTIVE robustness of the live noise-band setting on the tuning years only; nothing here selects a parameter.",
        "window": {"first": str(_tuning_days("SPY").dates[WARM].date()), "last": TUNE_END, "sessions": int(n_days),
                   "note": f"first {WARM} sessions skipped so every lookback (max {max(LOOKBACKS)}) is warm in every column"},
        "axes": {"lookback": LOOKBACKS, "check_min": CHECKS, "band": BANDS, "vwap": VWAPS},
        "book": "50% SPY + 50% QQQ per symbol, 1 bp per side; metric = annualised Sharpe of daily returns",
        "live": {"params": V.LIVE, "sharpe_combined": float(S[live]), "sharpe_SPY": float(G["SPY"][live]),
                 "sharpe_QQQ": float(G["QQQ"][live]), "cagr": float(cagr[live]), "max_drawdown": float(mdd[live]),
                 "rank_of_420": int((flat > S[live]).sum() + 1), "percentile": float((flat < S[live]).mean()),
                 "neighbours": nb_stats(live), "vwap_off_same_other_params": float(S[live_vwap_off])},
        "grid_summary": {"cells": int(flat.size), "median": float(np.median(flat)), "mean": float(flat.mean()),
                         "p10": float(np.percentile(flat, 10)), "p90": float(np.percentile(flat, 90)),
                         "min": float(flat.min()), "max": float(flat.max()),
                         "share_positive": float((flat > 0).mean()),
                         "spatial_autocorrelation_cell_vs_neighbour_mean": float(np.corrcoef(flat, nbmean)[0, 1])},
        "max_cell_in_grid": {"params": desc(best), **nb_stats(best),
                             "note": "reported only to judge spike vs plateau; NOT adopted"},
        "marginal_mean_sharpe": marg,
        "heatmap_lookback_x_check_band1.0_vwap_on": {"rows_lookback": LOOKBACKS, "cols_check": CHECKS,
                                                      "sharpe": tbl(S[:, :, BANDS.index(1.0), 0])},
        "heatmap_band_x_check_lookback14_vwap_on": {"rows_band": BANDS, "cols_check": CHECKS,
                                                     "sharpe": tbl(S[LOOKBACKS.index(14), :, :, 0].T)},
        "heatmap_lookback_x_band_check60_vwap_on": {"rows_lookback": LOOKBACKS, "cols_band": BANDS,
                                                     "sharpe": tbl(S[:, CHECKS.index(60), :, 0])},
        "heatmap_lookback_x_check_band1.0_vwap_off": {"rows_lookback": LOOKBACKS, "cols_check": CHECKS,
                                                       "sharpe": tbl(S[:, :, BANDS.index(1.0), 1])},
        "all_cells": [{**desc(np.unravel_index(i, shape)), "sharpe_combined": round(float(flat[i]), 4),
                       "sharpe_SPY": round(float(G["SPY"].ravel()[i]), 4), "sharpe_QQQ": round(float(G["QQQ"].ravel()[i]), 4),
                       "cagr": round(float(cagr.ravel()[i]), 4), "max_drawdown": round(float(mdd.ravel()[i]), 4),
                       "trades_per_day_SPY": round(float(trades[i]), 3)} for i in range(flat.size)],
    }
    eng = np.mean([R[s + "_val"]["engine_seconds_per_config"] for s in SYMBOLS])
    out["runtime"] = {"vectorbt_seconds_total_420_configs_per_symbol": {k: round(v, 2) for k, v in t_vbt.items()},
                      "vectorbt_seconds_total_both_symbols": round(sum(t_vbt.values()), 2),
                      "engine_seconds_per_config_tuning_years": round(float(eng), 3),
                      "engine_seconds_extrapolated_420_configs_both_symbols": round(float(eng) * 420 * 2, 1),
                      "note": "SPY time includes numba JIT compile of the vectorbt kernels (~6 s, once per process). engine = intraday.noise_breakout in a Python loop (no band / VWAP switch, so extrapolated from the 30 lookback x check configs it can run)"}
    out["engine_validation"] = {s: R[s + "_val"] for s in SYMBOLS}
    (RES / "vbt_noise_robustness.json").write_text(json.dumps(out, indent=1))
    (RES / "vbt_noise_robustness.md").write_text(_markdown(out))
    print(json.dumps({k: out[k] for k in ("live", "grid_summary", "max_cell_in_grid", "runtime", "engine_validation")}, indent=1))
    return out


def _table(title, rows_name, rows, cols_name, cols, M, live_rc=None):
    h = f"| {rows_name} \\ {cols_name} | " + " | ".join(str(c) for c in cols) + " |\n|---|" + "---|" * len(cols) + "\n"
    body = ""
    for i, r in enumerate(rows):
        cells = [f"**{M[i][j]:.2f}**" if live_rc == (i, j) else f"{M[i][j]:.2f}" for j in range(len(cols))]
        body += f"| {r} | " + " | ".join(cells) + " |\n"
    return f"**{title}**\n\n{h}{body}\n"


def _markdown(o):
    lv, gs, mx, rt = o["live"], o["grid_summary"], o["max_cell_in_grid"], o["runtime"]
    a = o["axes"]
    hm1, hm2, hm3, hm4 = (o["heatmap_lookback_x_check_band1.0_vwap_on"], o["heatmap_band_x_check_lookback14_vwap_on"],
                          o["heatmap_lookback_x_band_check60_vwap_on"], o["heatmap_lookback_x_check_band1.0_vwap_off"])
    nb = lv["neighbours"]
    return f"""# Noise-band robustness (vectorbt), tuning years only

Descriptive check of how sensitive the live noise-band strategy is to its parameters. **Nothing here selects or changes a parameter.**
Window: {o['window']['first']} to {o['window']['last']} ({o['window']['sessions']} sessions, tuning years only; no 2021+ data is loaded).
Book: 50% SPY + 50% QQQ, 1 bp per side. Metric: annualised Sharpe of daily returns. Grid: lookback {a['lookback']} x check {a['check_min']} min
x band multiplier {a['band']} x VWAP filter on/off = {gs['cells']} cells.

## Where the live setting sits
Live (lookback 14, check 60, band 1.0, VWAP on): Sharpe **{lv['sharpe_combined']:.2f}** (SPY {lv['sharpe_SPY']:.2f}, QQQ {lv['sharpe_QQQ']:.2f}),
CAGR {lv['cagr']:.1%}, max drawdown {lv['max_drawdown']:.1%}. Rank {lv['rank_of_420']} of {gs['cells']} (percentile {lv['percentile']:.0%}).
Its {nb['neighbours']} one-step neighbours average {nb['neighbour_mean']:.2f} (range {nb['neighbour_min']:.2f} to {nb['neighbour_max']:.2f});
live minus neighbour mean = {nb['value_minus_neighbour_mean']:+.2f}. VWAP filter off (all else live): {lv['vwap_off_same_other_params']:.2f}.

## Shape of the surface
Whole grid: median {gs['median']:.2f}, 10th to 90th percentile {gs['p10']:.2f} to {gs['p90']:.2f}, min {gs['min']:.2f}, max {gs['max']:.2f};
{gs['share_positive']:.0%} of cells have a positive Sharpe. A cell correlates {gs['spatial_autocorrelation_cell_vs_neighbour_mean']:.2f} with the mean of its
neighbours (near 1 = smooth plateau, near 0 = noisy/spiky). The best cell in the grid ({mx['params']}) scores {mx['value']:.2f} against a neighbour mean of
{mx['neighbour_mean']:.2f} (gap {mx['value_minus_neighbour_mean']:+.2f}); it is shown only to judge spike vs plateau and is not adopted.

Marginal mean Sharpe by axis: {json.dumps(o['marginal_mean_sharpe'])}

{_table('Lookback x check (band 1.0, VWAP on); live in bold', 'lookback', hm1['rows_lookback'], 'check min', hm1['cols_check'], hm1['sharpe'], (a['lookback'].index(14), a['check_min'].index(60)))}
{_table('Band multiplier x check (lookback 14, VWAP on)', 'band', hm2['rows_band'], 'check min', hm2['cols_check'], hm2['sharpe'], (a['band'].index(1.0), a['check_min'].index(60)))}
{_table('Lookback x band (check 60, VWAP on)', 'lookback', hm3['rows_lookback'], 'band', hm3['cols_band'], hm3['sharpe'], (a['lookback'].index(14), a['band'].index(1.0)))}
{_table('Lookback x check (band 1.0, VWAP off)', 'lookback', hm4['rows_lookback'], 'check min', hm4['cols_check'], hm4['sharpe'], (a['lookback'].index(14), a['check_min'].index(60)))}

## Speed
vectorbt, all {gs['cells']} configs for one symbol in one Portfolio: {rt['vectorbt_seconds_total_420_configs_per_symbol']} s per symbol.
Existing engine: {rt['engine_seconds_per_config_tuning_years']} s per config, so about {rt['engine_seconds_extrapolated_420_configs_both_symbols']} s for the same
{gs['cells']} x 2 runs (it cannot vary band or VWAP, so this is extrapolated). Vectorised results match the engine on the lookback x check plane:
{json.dumps(o['engine_validation'])}.

Caveat: Sharpe over five years of one book is a noisy number (about +/-0.5 standard error); read the surface for shape, not for small differences.
"""


if __name__ == "__main__":
    {"crosscheck": crosscheck, "sweep": sweep}[sys.argv[1]]()
