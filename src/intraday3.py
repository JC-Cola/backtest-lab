"""Intraday study 3 (INTRADAY_PLAN_3.md): seven rules not tested in studies 1 or 2, on SPY and QQQ.
Same clock, costs, windows and tests as intraday.py (decision on a bar's close, fill at the next bar's open).

    python src/intraday3.py              # discovery on SPY and QQQ
    python src/intraday3.py --lockbox    # also run any candidate on XLK, SMH, XLF, XLE (downloads them)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

import intraday as I
from intraday import _ret, _trades, grid, runs_to_trades
from stats import bh_qvalues

DISCOVERY, LOCKBOX = ["SPY", "QQQ"], ["XLK", "SMH", "XLF", "XLE"]
OUT = I.ROOT / "results" / "intraday3.json"


def _day_hl(D):
    H = np.array([D.H[d, :D.last[d] + 1].max() for d in range(D.n)])
    L = np.array([D.L[d, :D.last[d] + 1].min() for d in range(D.n)])
    return H, L


def _hold_to_close(D, d, m, s):
    """Signal on the close of bar m, side s: fill at bar m+1's open, exit at the session close."""
    e = m + 1
    return (d, e, D.last[d] + 1, s, _ret(s, D.P[d, e], D.close[d]))


def vol_breakout(D, k=0.5, mode="ls"):
    """Williams: bands at today's open +/- k x yesterday's range; the first close beyond one is traded to the close."""
    H, L = _day_hl(D)
    rows = []
    for d in range(1, D.n):
        rng = H[d - 1] - L[d - 1]
        up, dn = D.O[d, 0] + k * rng, (D.O[d, 0] - k * rng if mode == "ls" else -np.inf)
        c = D.C[d, :D.last[d]]
        hit = np.flatnonzero((c > up) | (c < dn))
        if len(hit):
            m = hit[0]
            rows.append(_hold_to_close(D, d, m, 1.0 if c[m] > up else -1.0))
    return _trades(rows)


def prev_day_breakout(D, start=0, stop="none"):
    """First close above yesterday's high (long) or below its low (short), traded to the close. With
    stop="level" the stop is the broken level; a trade whose entry is already back through it is skipped."""
    H, L = _day_hl(D)
    rows = []
    for d in range(1, D.n):
        ph, pl = H[d - 1], L[d - 1]
        first = max(start - 1, 0)
        c = D.C[d, first:D.last[d]]
        hit = np.flatnonzero((c > ph) | (c < pl))
        if not len(hit):
            continue
        m = first + hit[0]
        s = 1.0 if D.C[d, m] > ph else -1.0
        if stop == "none":
            rows.append(_hold_to_close(D, d, m, s))
            continue
        e, lvl = m + 1, (ph if s > 0 else pl)
        px = D.O[d, e]
        if (px - lvl) * s <= 0:
            continue
        exit_m, exit_px = D.last[d] + 1, D.close[d]
        for j in range(e, D.last[d] + 1):
            if j > e and (D.O[d, j] - lvl) * s <= 0: exit_m, exit_px = j, D.O[d, j]; break
            if (D.L[d, j] if s > 0 else D.H[d, j]) * s <= lvl * s: exit_m, exit_px = j, lvl; break
        rows.append((d, e, max(exit_m, e + 1), s, _ret(s, px, exit_px)))
    return _trades(rows)


def vwap_reversion(D, thr=0.005, exit_="vwap"):
    """From 10:00 to 15:30, fade closes more than thr from VWAP; exit when a close crosses VWAP, or at the close."""
    rows = []
    for d in range(D.n):
        end = D.last[d] + 1
        side = np.zeros(end)
        pos, e = 0.0, 0
        for m in range(29, end - 1):
            c, vw = D.C[d, m], D.vwap[d, m]
            if pos == 0 and m <= 359:
                dev = c / vw - 1
                if abs(dev) > thr:
                    pos, e = (-1.0 if dev > 0 else 1.0), m + 1
            elif pos != 0 and exit_ == "vwap" and (c - vw) * pos >= 0:
                side[e:m + 1] = pos
                pos = 0.0
        if pos != 0:
            side[e:end] = pos
        rows += runs_to_trades(D, d, side)
    return _trades(rows)


def bollinger_5m(D, n=20, k=2.0):
    """5-minute closes; a close outside mean +/- k sd of the last n closes is faded until a close crosses the mean."""
    marks = [(d, m) for d in range(D.n) for m in range(4, D.last[d], 5)]
    dd, mm = np.array(marks).T
    s = pd.Series(D.C[dd, mm])
    mid, sd = s.rolling(n).mean().values, s.rolling(n).std(ddof=0).values
    rows = []
    i = 0
    while i < len(dd):
        d = dd[i]
        end = D.last[d] + 1
        side = np.zeros(end)
        pos, e = 0.0, 0
        while i < len(dd) and dd[i] == d:
            m, c = mm[i], D.C[d, mm[i]]
            if not np.isnan(sd[i]) and sd[i] > 0:
                if pos == 0:
                    if c < mid[i] - k * sd[i]: pos, e = 1.0, m + 1
                    elif c > mid[i] + k * sd[i]: pos, e = -1.0, m + 1
                elif (c - mid[i]) * pos >= 0:
                    side[e:m + 1] = pos
                    pos = 0.0
            i += 1
        if pos != 0:
            side[e:end] = pos
        rows += runs_to_trades(D, d, side)
    return _trades(rows)


def donchian(D, N=60):
    """Stop-and-reverse on a close beyond the previous N minutes' high or low; flat at the close."""
    rows = []
    for d in range(D.n):
        end = D.last[d] + 1
        if end <= N + 1:
            continue
        hh = sliding_window_view(D.H[d, :end - 1], N).max(axis=1)  # hh[i] = max H[i .. i+N-1]
        ll = sliding_window_view(D.L[d, :end - 1], N).min(axis=1)
        side = np.zeros(end)
        cur = 0.0
        for m in range(N, end - 1):
            c = D.C[d, m]
            if c > hh[m - N]: cur = 1.0
            elif c < ll[m - N]: cur = -1.0
            side[m + 1] = cur
        rows += runs_to_trades(D, d, side)
    return _trades(rows)


def late_momentum(D, ref="close", sig_end=360):
    """Baltussen et al. (2021): sign of the return from yesterday's close (or today's open) to sig_end
    (330 = 15:00, 360 = 15:30), traded from then to the close."""
    rows = []
    for d in range(1, D.n):
        if D.last[d] + 1 <= sig_end:
            continue
        base = D.prev_close[d] if ref == "close" else D.O[d, 0]
        r = D.C[d, sig_end - 1] / base - 1
        if r != 0:
            rows.append(_hold_to_close(D, d, sig_end - 1, 1.0 if r > 0 else -1.0))
    return _trades(rows)


_FOMC = set(pd.read_csv(I.ROOT / "data" / "fomc_dates.csv")["date"])


def fomc_drift(D, entry=0):
    """Lucca & Moench (2015): on FOMC announcement days, long from `entry` (minutes after 09:30) to 14:00."""
    x = 270  # 14:00
    rows = [(d, entry, x, 1.0, _ret(1.0, D.P[d, entry], D.P[d, x]))
            for d in range(D.n) if str(D.dates[d].date()) in _FOMC and D.last[d] >= x]
    return _trades(rows)


STRATEGIES = [
    {"id": "id_vol_breakout", "name": "Volatility Breakout", "fn": vol_breakout, "grid": grid(k=[0.25, 0.5, 0.75], mode=["ls", "long"]),
     "coin": True, "study": 3, "source": "Larry Williams, Long-Term Secrets to Short-Term Trading (1999)",
     "plain": "Trade the day's first move beyond the open plus or minus part of yesterday's range."},
    {"id": "id_prev_day_breakout", "name": "Prior-Day High/Low Breakout", "fn": prev_day_breakout, "grid": grid(start=[0, 30], stop=["none", "level"]),
     "coin": True, "study": 3, "source": "Practitioner (prior-day high/low breakout)",
     "plain": "Buy a break of yesterday's high, short a break of yesterday's low."},
    {"id": "id_vwap_reversion", "name": "VWAP Reversion", "fn": vwap_reversion, "grid": grid(thr=[0.003, 0.005, 0.008], exit_=["vwap", "close"]),
     "coin": True, "study": 3, "source": "Practitioner (VWAP mean reversion)",
     "plain": "When the price is stretched far from VWAP, bet it comes back."},
    {"id": "id_bollinger_5m", "name": "Bollinger Reversion (5-min)", "fn": bollinger_5m, "grid": grid(n=[20, 40], k=[2.0, 2.5]),
     "coin": True, "study": 3, "source": "John Bollinger, Bollinger on Bollinger Bands (2001)",
     "plain": "Fade 5-minute closes outside the Bollinger Bands until the price is back at the average."},
    {"id": "id_donchian", "name": "Minute Channel Breakout", "fn": donchian, "grid": grid(N=[30, 60, 120]),
     "coin": True, "study": 3, "source": "Richard Donchian / Turtle channel breakout (on minute bars)",
     "plain": "Go with any break of the last 30-120 minutes' high or low."},
    {"id": "id_late_momentum", "name": "Late-Day Momentum", "fn": late_momentum, "grid": grid(ref=["close", "open"], sig_end=[330, 360]),
     "coin": True, "study": 3, "source": "Baltussen, Da, Lammers & Martens (2021), Journal of Financial Economics",
     "plain": "If the market is up on the day by 15:00 or 15:30, buy into the close; if down, short."},
    {"id": "id_fomc_drift", "name": "Pre-FOMC Drift", "fn": fomc_drift, "grid": grid(entry=[0, 30]),
     "coin": True, "study": 3, "source": "Lucca & Moench (2015), Journal of Finance",
     "plain": "On Fed announcement days, buy in the morning and sell at 14:00, just before the statement."},
]


def run(symbols, strategies=STRATEGIES):
    rows = []
    for sym in symbols:
        D = I.Days(sym)
        print(f"== {sym}: {D.n} days {D.dates[0].date()}..{D.dates[-1].date()}", flush=True)
        for st in strategies:
            r = I.evaluate(st, sym, D)
            r.pop("_t", None)
            rows.append(r)
            cf = r["coin_flip"]
            print(f"  {st['id']:22s} {str(r['params']):34s} IS Sh {r['is_sharpe']:5.2f} | OOS Sh {r['oos']['sharpe']:5.2f} "
                  f"(hold {r['buy_hold_oos']['sharpe']:4.2f}) 2x {r['oos_cost2x_sharpe']:5.2f} trades {r['oos']['trades']:5d} "
                  f"p={'-' if cf is None else format(cf['p_value'], '.3f')}", flush=True)
    return rows


def main(strategies=STRATEGIES, lockbox=LOCKBOX, out=OUT, plan="INTRADAY_PLAN_3.md", extra=None):
    """Discovery on SPY and QQQ, BH q-values, candidates; with --lockbox, any candidate on the lockbox markets.
    `extra(row) -> bool` is an additional candidate condition (it may also annotate the row)."""
    rows = run(DISCOVERY, strategies)
    tested = [r for r in rows if r["coin_flip"]]
    for r, q in zip(tested, bh_qvalues([r["coin_flip"]["p_value"] for r in tested])):
        r["coin_flip"]["q_value"] = float(q)
        ok = extra(r) if extra else True
        r["candidate"] = bool(r["coin_flip"]["p_value"] < 0.05 and q < 0.10 and r["oos"]["sharpe"] > 0 and r["oos_cost2x_sharpe"] > 0 and ok)
    cands = [r for r in rows if r.get("candidate")]
    print(f"\n{len(tested)} tests; candidates: {[(r['strategy'], r['asset']) for r in cands] or 'none'}")
    if cands and "--lockbox" in sys.argv:
        import intraday_data
        for s in lockbox:
            intraday_data.download(s)
        by_id = {s["id"]: s for s in strategies}
        for sid in {r["strategy"] for r in cands}:
            lb = I.lockbox(by_id[sid], lockbox)
            for r in rows:
                if r["strategy"] == sid and r.get("candidate"):
                    r["lockbox"] = lb
            print(f"lockbox {sid}: Fisher p={lb['fisher_p']:.3f} median Sharpe={lb['median_oos_sharpe']:.2f} -> "
                  f"{'CONFIRMED' if lb['confirmed'] else 'not confirmed'}")
    out.write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "plan": plan,
                               "cost_per_side": I.COST, "n_sims": I.N_SIMS, "discovery": DISCOVERY, "lockbox_symbols": lockbox,
                               "strategies": [{k: s[k] for k in ("id", "name", "source", "plain")} | {"grid_size": len(s["grid"])}
                                              for s in strategies], "results": rows}, default=float))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
