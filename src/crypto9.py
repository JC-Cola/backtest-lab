"""Crypto study 9 (CRYPTO_PLAN_9.md) driver: runs the studies on BTC, ETH and SOL, the controls, the filters and the
risk-reducer readout, then the lockbox for any candidate, and writes results/crypto9.json.

    python src/crypto9.py              # discovery on BTC, ETH, SOL (+ controls, filters, risk-reducer readout)
    python src/crypto9.py --lockbox    # also run any candidate on LTC, BCH, LINK, AVAX (downloads them)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import numpy as np

import crypto_data as CD
import strategies_trend as tr
from crypto9_base import *  # noqa: F401,F403  (constants, Coin, STRATS, evaluate_single, filters, run_perf, buy_hold)
from crypto9_base import _seed  # noqa: F401
from crypto9_rot import *  # noqa: F401,F403  (Panel, rotation, controls)
from run import fisher_p
from stats import bh_qvalues


# ---------------------------------------------------------------------------------------------- driver

def load_coin(name):
    f = CD.load_funding(CD.FUNDING_SYMBOLS[name]) if name in CD.FUNDING_SYMBOLS else None
    return Coin(name, CD.load(name), f)


def verdict_of(row):
    cf = row.get("coin_flip")
    if row.get("control"):
        # plan: a control "fails" with p >= 0.05 or Sharpe <= 0 at 0.2%; a pass (with q < 0.10) is a leakage red flag
        passed = bool(cf and cf["p_value"] < 0.05 and cf["q_value"] < 0.10 and row["oos"]["sharpe"] > 0)
        return "CONTROL PASSED (red flag)" if passed else "CONTROL FAILED (expected)"
    if cf is None:
        return "N/A"
    if cf["p_value"] < 0.05:
        return "PASS" if row.get("candidate") else "WEAK"
    return "FAIL"


def lockbox(sid, params_row):
    """Fresh tuning + test + coin-flip of the same strategy on the lockbox coins (Fisher p, median Sharpe)."""
    out = []
    if sid == "c9_rotation":
        dfs = {}
        for c in LOCKBOX:
            CD.download(c)
            dfs[c] = CD.load(c)
        P = Panel(dfs)
        r, _, _ = evaluate_rotation(P, "ROT-lockbox")
        cf = r["coin_flip"]
        out.append({"ticker": "LTC+BCH+LINK+AVAX rotation", "p_value": cf["p_value"] if cf else 1.0, "oos_sharpe": r["oos"]["sharpe"],
                    "params": r["params"], "n_trades": r["oos"]["n_trades"], "oos_total": r["oos"]["total_return"]})
    else:
        for c in LOCKBOX:
            CD.download(c)
            coin = Coin(c, CD.load(c))
            if not coin.tunable:
                out.append({"ticker": c, "dropped": "no tuning-window bars after the 365-day warm-up"})
                continue
            r, _, _ = evaluate_single(sid, coin)
            cf = r["coin_flip"]
            out.append({"ticker": c, "p_value": cf["p_value"] if cf else 1.0, "oos_sharpe": r["oos"]["sharpe"], "params": r["params"],
                        "n_trades": r["oos"]["n_trades"], "oos_total": r["oos"]["total_return"],
                        "tune": r["windows"]["tune"], "test": r["windows"]["test"]})
    used = [a for a in out if "p_value" in a]
    ps = [a["p_value"] for a in used]
    med = float(np.median([a["oos_sharpe"] for a in used])) if used else 0.0
    fp = fisher_p(ps) if ps else 1.0
    return {"assets": out, "fisher_p": fp, "median_oos_sharpe": med, "confirmed": bool(ps and fp < 0.05 and med > 0),
            "rule": "Fisher combined p < 0.05 and median test Sharpe > 0 on LTC, BCH, LINK, AVAX (fresh tuning)"}


def main():
    do_lockbox = "--lockbox" in sys.argv
    coins = {n: load_coin(n) for n in DISCOVERY}
    rows, packs = [], {}
    for sid in STRATS:
        for n, coin in coins.items():
            row, pos, b = evaluate_single(sid, coin)
            rows.append(row)
            packs[(sid, n)] = (coin, pos, b)
            print(f"{sid:12s} {n:8s} {row['params']} test Sh {row['oos']['sharpe']:5.2f} p={row['coin_flip']['p_value'] if row['coin_flip'] else None}", flush=True)
    P = Panel({n: c.df for n, c in coins.items()})
    row, Ws, b = evaluate_rotation(P)
    rows.append(row)
    packs[("c9_rotation", "ROT")] = (P, Ws, b)
    print(f"c9_rotation  ROT      {row['params']} test Sh {row['oos']['sharpe']:5.2f} p={row['coin_flip']['p_value'] if row['coin_flip'] else None}", flush=True)
    h = CD.load("BTC-USD", hourly=True)
    d_close = CD.load("BTC-USD").Close
    ctl_rows = [evaluate_control("c9_ctl_hour", h, ctl_hour_positions(h.index)),
                evaluate_control("c9_ctl_macd", h, ctl_macd_positions(h, d_close))]
    for r in ctl_rows:
        print(f"{r['strategy']:12s} BTC-USD  test Sh {r['oos']['sharpe']:5.2f} p={r['coin_flip']['p_value']}", flush=True)
    allrows = rows + ctl_rows
    tested = [r for r in allrows if r["coin_flip"]]
    for r, q in zip(tested, bh_qvalues([r["coin_flip"]["p_value"] for r in tested])):
        r["coin_flip"]["q_value"] = float(q)
    for r in allrows:
        cf = r["coin_flip"]
        r["candidate"] = bool(cf and cf["p_value"] < 0.05 and cf["q_value"] < 0.10 and r["oos"]["sharpe"] > 0
                              and r["oos_stress"]["sharpe"] > 0)
        r["verdict"] = verdict_of(r)
    cands = [r for r in allrows if r["candidate"]]
    print(f"\n{len(tested)} tests in the BH pool; candidates: {[(r['strategy'], r['asset']) for r in cands] or 'none'}")
    if cands and do_lockbox:
        for r in cands:
            if r.get("control"):
                continue
            r["lockbox"] = lockbox(r["strategy"], r)
            lb = r["lockbox"]
            print(f"lockbox {r['strategy']} {r['asset']}: Fisher p={lb['fisher_p']:.3f} median Sharpe={lb['median_oos_sharpe']:.2f} -> "
                  f"{'CONFIRMED' if lb['confirmed'] else 'not confirmed'}")
            r["verdict"] = "PASS" if lb["confirmed"] else "WEAK"
    # secondary readouts
    flt = []
    for r in rows:
        if r["strategy"] in ("c9_donchian", "c9_trend"):
            coin = packs[(r["strategy"], r["asset"])][0]
            flt += filters(r["strategy"], coin, r)
    live = {}
    for n, coin in coins.items():
        s = tr.trend_filter(coin.df, 150).values.astype(float)
        live[n] = {"oos": run_perf(coin, s, coin.X, COST), "oos_stress": run_perf(coin, s, coin.X, STRESS)}
    data = {}
    for n, coin in coins.items():
        data[n] = {"source": "Coinbase Exchange daily candles (UTC)", "first": str(coin.dates[0].date()), "last": str(coin.dates[-1].date()),
                   "bars": coin.n, "gap_bars_filled": int((coin.df.Volume == 0).sum()), "tune_start": str(coin.dates[coin.I.start].date()),
                   "funding": None if coin.funding is None else {"symbol": CD.FUNDING_SYMBOLS[n], "first": str(coin.funding.index[0]),
                                                                   "last": str(coin.funding.index[-1]), "rows": int(len(coin.funding)),
                                                                   "first_active_day": funding_active_from(coin.dates, coin.funding)}}
    data["BTC-USD_1h"] = {"source": "Coinbase Exchange hourly candles (UTC)", "first": str(h.index[0]), "last": str(h.index[-1]),
                          "bars": len(h), "gap_bars_filled": int((h.Volume == 0).sum())}
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan": PLAN, "cost_per_side": COST / 1e4,
           "stress_per_side": STRESS / 1e4, "n_sims": N_SIMS, "discovery": DISCOVERY, "lockbox_symbols": LOCKBOX,
           "windows": {"tune": [IS_START, IS_END], "test": [OOS_START, str(coins["BTC-USD"].dates[-1].date())]},
           "data": data,
           "strategies": [{"id": k, "name": v["name"], "source": v["source"], "plain": v["plain"], "grid_size": len(v["grid"])}
                          for k, v in STRATS.items()]
                         + [{"id": "c9_rotation", "name": "Crypto Weekly Rotation", "source": "Cross-sectional momentum folklore (study 9)",
                             "plain": "Each week holds the 1 to 3 coins with the best 1, 2 or 4-week return, only if above their 50-day average.",
                             "grid_size": len(ROT_GRID)}],
           "results": rows, "controls": ctl_rows, "filters": flt, "live_trend_sma150": live,
           "buy_hold": {n: buy_hold(c) for n, c in coins.items()} | {"ROT": rot_bh(P)}}
    import crypto9_lab
    out["lab"] = crypto9_lab.build(rows, packs, STRATS, pool=len(tested))
    OUT.write_text(json.dumps(out, default=float))
    print(f"wrote {OUT}")
    report(out)


def rot_bh(P):
    W = rot_buy_hold(P)
    return rot_perf(P, W, COST, P.X) | {"stress": rot_perf(P, W, STRESS, P.X)}


def report(out):
    f = lambda v: "   -  " if v is None else f"{v:6.2f}"
    print("\nTEST YEARS (2023+), tuned setting. Sh=Sharpe  Tot=total return  DD=max drawdown  tr/y=trades per year")
    print(f"{'strategy':12s} {'mkt':8s} {'params':34s} | 0.2%: Sh   Tot%     DD%   tr/y | 0.5%: Sh   Tot%     DD% |   p      q   cand RR")
    for r in out["results"] + out["controls"]:
        o, s = r["oos"], r["oos_stress"]
        cf = r["coin_flip"]
        print(f"{r['strategy']:12s} {r['asset']:8s} {json.dumps(r['params'])[:34]:34s} | {o['sharpe']:5.2f} {o['total_return']*100:7.1f} {o['max_dd']*100:7.1f} {o['trades_per_year']:6.1f} | "
              f"{s['sharpe']:5.2f} {s['total_return']*100:7.1f} {s['max_dd']*100:7.1f} | "
              f"{('%.3f' % cf['p_value']) if cf else '  -  '} {('%.3f' % cf['q_value']) if cf else '  -  '} {'Y' if r['candidate'] else '-'}  "
              f"{'Y' if r.get('risk_reducer') else '-'}{('/' + r['risk_reducer_grid']) if 'risk_reducer_grid' in r else ''}")
    print("\nBUY & HOLD (test years)")
    for n, b in out["buy_hold"].items():
        print(f"  {n:8s} Sh {b['sharpe']:5.2f} Tot {b['total_return']*100:8.1f}% DD {b['max_dd']*100:6.1f}% Calmar {f(b['calmar'])} | 0.5%: Sh {b['stress']['sharpe']:5.2f} Tot {b['stress']['total_return']*100:8.1f}%")


if __name__ == "__main__":
    main()
