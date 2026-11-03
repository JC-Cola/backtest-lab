"""Intraday study (INTRADAY_PLAN.md): strategies holding from 1 minute up to 1 day.

Conventions (one clock, as in the daily engine):
- a day is a row of minute bars 09:30..15:59 New York (index m = 0..last; last < 389 on half days);
- a decision on the CLOSE of bar m is filled at the OPEN of bar m+1;
- price at "minute m" is the open of bar m; minute last+1 means the session's closing price;
- stops and targets fill at their level, or at the bar's open if the bar opens through them.

    python src/intraday.py            # discovery run on SPY and QQQ
"""
from __future__ import annotations

import json
import math
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import intraday_data
from run import fisher_p
from stats import bh_qvalues

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "intraday.json"
COST = 1e-4            # 1 bp per side
IS_END, OOS_START = "2020-12-31", "2021-01-01"
N_SIMS, SEED, MIN_IS_TRADES = 2000, 20261103, 50
DISCOVERY, LOCKBOX = ["SPY", "QQQ"], ["IWM", "DIA"]


# ---------------------------------------------------------------- data as a day x minute matrix
class Days:
    def __init__(self, symbol: str):
        df = intraday_data.load(symbol)
        day = df.index.normalize().tz_localize(None)
        m = (df.index.hour * 60 + df.index.minute - 570).values
        dates = pd.DatetimeIndex(sorted(set(day)))
        di = dates.get_indexer(day)
        n = len(dates)
        mats = {}
        for k in ("open", "high", "low", "close", "volume"):
            a = np.full((n, 390), np.nan)
            a[di, m] = df[k].values
            mats[k] = a
        C = pd.DataFrame(mats["close"]).ffill(axis=1).values
        last = np.array([np.flatnonzero(~np.isnan(mats["close"][d])).max() for d in range(n)])
        firstv = np.array([np.flatnonzero(~np.isnan(mats["open"][d])).min() for d in range(n)])
        keep = firstv == 0  # need a 09:30 bar
        O = np.where(np.isnan(mats["open"]), np.roll(C, 1, axis=1), mats["open"])
        H = np.where(np.isnan(mats["high"]), O, mats["high"])
        L = np.where(np.isnan(mats["low"]), O, mats["low"])
        V = np.nan_to_num(mats["volume"])
        self._derive(dates[keep], O[keep], H[keep], L[keep], C[keep], V[keep], last[keep])

    @classmethod
    def from_arrays(cls, dates, O, H, L, C, V, last):
        self = cls.__new__(cls)
        self._derive(pd.DatetimeIndex(dates), O, H, L, C, V, np.asarray(last))
        return self

    def _derive(self, dates, O, H, L, C, V, last):
        self.dates, self.O, self.H, self.L, self.C, self.V, self.last = dates, O, H, L, C, V, last
        n = len(self.dates)
        self.n = n
        # P[d, m] = price at minute m (open of bar m; m = last+1 -> session close)
        P = np.full((n, 391), np.nan)
        P[:, :390] = self.O
        for d in range(n):
            P[d, self.last[d] + 1:] = np.nan
            P[d, self.last[d] + 1] = self.C[d, self.last[d]]
        self.P = P
        self.close = self.C[np.arange(n), self.last]
        self.prev_close = np.r_[np.nan, self.close[:-1]]
        tp = (self.H + self.L + self.C) / 3
        cv = np.cumsum(V, axis=1)
        self.vwap = np.where(cv > 0, np.cumsum(tp * V, axis=1) / np.where(cv > 0, cv, 1), self.C)


# trades: dict of arrays day, entry_m, exit_m, dir, ret (net of costs). Overnight trades use exit day d+1.
def _trades(rows):
    if not rows:
        return {k: np.array([]) for k in ("day", "entry_m", "exit_m", "dir", "ret")}
    a = np.array(rows, dtype=float)
    return {"day": a[:, 0].astype(int), "entry_m": a[:, 1].astype(int), "exit_m": a[:, 2].astype(int),
            "dir": a[:, 3], "ret": a[:, 4]}


def _ret(direction, p_in, p_out):
    return direction * (p_out / p_in - 1) - 2 * COST


def runs_to_trades(D, d, side_by_minute):
    """side_by_minute[m] = position held DURING bar m (decided earlier). Turn runs into trades."""
    rows = []
    s = side_by_minute
    m = 0
    end = D.last[d] + 1
    while m < end:
        if s[m] == 0:
            m += 1
            continue
        j = m
        while j < end and s[j] == s[m]:
            j += 1
        rows.append((d, m, j, s[m], _ret(s[m], D.P[d, m], D.P[d, j])))
        m = j
    return rows


# ---------------------------------------------------------------- strategies
def overnight(D):
    rows = [(d + 1, -1, 0, 1.0, _ret(1.0, D.close[d], D.O[d + 1, 0])) for d in range(D.n - 1)]
    return _trades(rows)


def intraday_only(D):
    return _trades([(d, 0, D.last[d] + 1, 1.0, _ret(1.0, D.O[d, 0], D.close[d])) for d in range(D.n)])


def intraday_momentum(D, sig_min=30, hold=30):
    rows = []
    for d in range(1, D.n):
        r = D.C[d, sig_min - 1] / D.prev_close[d] - 1
        e = D.last[d] + 1 - hold
        if r == 0 or e <= sig_min - 1:
            continue
        s = 1.0 if r > 0 else -1.0
        rows.append((d, e, D.last[d] + 1, s, _ret(s, D.P[d, e], D.close[d])))
    return _trades(rows)


def orb(D, or_min=5, target=10.0):
    rows = []
    for d in range(D.n):
        o, c = D.O[d, 0], D.C[d, or_min - 1]
        if c == o:
            continue
        s = 1.0 if c > o else -1.0
        hi, lo = D.H[d, :or_min].max(), D.L[d, :or_min].min()
        e = or_min
        if e > D.last[d]:
            continue
        px = D.O[d, e]
        stop = lo if s > 0 else hi
        R = (px - stop) * s
        if R <= 0:
            continue
        tgt = px + s * target * R if target else None
        exit_m, exit_px = D.last[d] + 1, D.close[d]
        for m in range(e, D.last[d] + 1):
            Om, Hm, Lm = D.O[d, m], D.H[d, m], D.L[d, m]
            if s > 0:
                if m > e and Om <= stop: exit_m, exit_px = m, Om; break
                if Lm <= stop: exit_m, exit_px = m, stop; break
                if tgt and m > e and Om >= tgt: exit_m, exit_px = m, Om; break
                if tgt and Hm >= tgt: exit_m, exit_px = m, tgt; break
            else:
                if m > e and Om >= stop: exit_m, exit_px = m, Om; break
                if Hm >= stop: exit_m, exit_px = m, stop; break
                if tgt and m > e and Om <= tgt: exit_m, exit_px = m, Om; break
                if tgt and Lm <= tgt: exit_m, exit_px = m, tgt; break
        rows.append((d, e, max(exit_m, e + 1), s, _ret(s, px, exit_px)))
    return _trades(rows)


def vwap_trend(D, bar=5):
    rows = []
    for d in range(D.n):
        end = D.last[d] + 1
        side = np.zeros(end + 1)
        for b in range(bar - 1, end - 1, bar):  # decide on the close of each bar-size block
            side[b + 1: b + 1 + bar] = 1.0 if D.C[d, b] > D.vwap[d, b] else (-1.0 if D.C[d, b] < D.vwap[d, b] else 0.0)
        rows += runs_to_trades(D, d, side[:end])
    return _trades(rows)


def noise_breakout(D, lookback=14, check=30):
    move = np.abs(D.C / D.O[:, :1] - 1)
    rows = []
    checks = [m for m in range(29, 389) if (m + 1 - 30) % check == 0]
    for d in range(lookback + 1, D.n):
        sig = move[d - lookback:d].mean(axis=0)
        up = max(D.O[d, 0], D.prev_close[d]) * (1 + sig)
        dn = min(D.O[d, 0], D.prev_close[d]) * (1 - sig)
        end = D.last[d] + 1
        side = np.zeros(end)
        cur = 0.0
        for i, m in enumerate(checks):
            if m >= end - 1:
                break
            p, vw = D.C[d, m], D.vwap[d, m]
            cur = 1.0 if p > max(up[m], vw) else (-1.0 if p < min(dn[m], vw) else 0.0)
            nxt = checks[i + 1] if i + 1 < len(checks) and checks[i + 1] < end - 1 else end - 1
            side[m + 1: nxt + 1] = cur
        rows += runs_to_trades(D, d, side)
    return _trades(rows)


def gap_fade(D, gap=0.005, exit_="fill"):
    rows = []
    for d in range(1, D.n):
        pc = D.prev_close[d]
        g = D.O[d, 0] / pc - 1
        if abs(g) <= gap or D.last[d] < 2:
            continue
        s = -1.0 if g > 0 else 1.0
        e, px = 1, D.O[d, 1]
        exit_m, exit_px = D.last[d] + 1, D.close[d]
        if exit_ == "fill":
            for m in range(e, D.last[d] + 1):
                if s > 0:
                    if m > e and D.O[d, m] >= pc: exit_m, exit_px = m, D.O[d, m]; break
                    if D.H[d, m] >= pc: exit_m, exit_px = m, pc; break
                else:
                    if m > e and D.O[d, m] <= pc: exit_m, exit_px = m, D.O[d, m]; break
                    if D.L[d, m] <= pc: exit_m, exit_px = m, pc; break
        rows.append((d, e, max(exit_m, e + 1), s, _ret(s, px, exit_px)))
    return _trades(rows)


def halfhour_periodicity(D, lookback=20, mode="ls"):
    # slot k covers minutes 30k .. 30k+29; R[d, k] = P(30k+30)/P(30k) - 1 (last slot ends at the close)
    R = np.full((D.n, 13), np.nan)
    for k in range(13):
        a, b = 30 * k, min(30 * k + 30, 390)
        for d in range(D.n):
            if a > D.last[d]:
                continue
            bb = min(b, D.last[d] + 1)
            R[d, k] = D.P[d, bb] / D.P[d, a] - 1
    rows = []
    for d in range(lookback, D.n):
        sig = np.nanmean(R[d - lookback:d], axis=0)
        end = D.last[d] + 1
        side = np.zeros(end)
        for k in range(13):
            a = 30 * k
            if a >= end:
                break
            s = 1.0 if sig[k] > 0 else (-1.0 if sig[k] < 0 and mode == "ls" else 0.0)
            side[a: min(a + 30, end)] = s
        rows += runs_to_trades(D, d, side)
    return _trades(rows)


def grid(**axes):
    import itertools
    return [dict(zip(axes, v)) for v in itertools.product(*axes.values())]


STRATEGIES = [
    {"id": "id_overnight", "name": "Overnight Hold", "fn": overnight, "grid": [{}], "coin": False,
     "source": "Cliff, Cooper & Gulen (2008); Lou, Polk & Skouras (2019)",
     "plain": "Buy at the close, sell at the next morning's open."},
    {"id": "id_intraday_only", "name": "Daytime Hold", "fn": intraday_only, "grid": [{}], "coin": False,
     "source": "Cliff, Cooper & Gulen (2008), the contrast",
     "plain": "Buy at the open, sell at the close, every day."},
    {"id": "id_intraday_momentum", "name": "Market Intraday Momentum", "fn": intraday_momentum,
     "grid": grid(sig_min=[30, 60], hold=[30, 60]), "coin": True,
     "source": "Gao, Han, Li & Zhou (2018), Journal of Financial Economics",
     "plain": "If the market is up from yesterday's close by 10:00, buy for the last half hour; if down, sell short."},
    {"id": "id_orb", "name": "Opening Range Breakout", "fn": orb,
     "grid": grid(or_min=[5, 15, 30], target=[10.0, 0.0]), "coin": True,
     "source": "Zarattini, Barbon & Aziz (2023), SSRN",
     "plain": "Trade in the direction of the first 5-30 minutes, stop at the other end of that range."},
    {"id": "id_vwap_trend", "name": "VWAP Trend", "fn": vwap_trend, "grid": grid(bar=[1, 5, 15]), "coin": True,
     "source": "Zarattini & Aziz (2023), SSRN",
     "plain": "Long while the price is above the day's volume-weighted average price, short below it."},
    {"id": "id_noise_breakout", "name": "Noise-Band Breakout", "fn": noise_breakout,
     "grid": grid(lookback=[10, 14], check=[30, 60]), "coin": True,
     "source": "Zarattini, Aziz & Barbon (2024), Swiss Finance Institute",
     "plain": "Trade only when the price leaves its normal daily range and is on the right side of VWAP."},
    {"id": "id_gap_fade", "name": "Gap Fade", "fn": gap_fade,
     "grid": grid(gap=[0.0025, 0.005, 0.01], exit_=["fill", "close"]), "coin": True,
     "source": "Practitioner gap-fill folklore",
     "plain": "When the market opens far from yesterday's close, bet it moves back."},
    {"id": "id_halfhour_periodicity", "name": "Same Half-Hour Effect", "fn": halfhour_periodicity,
     "grid": grid(lookback=[10, 20, 40], mode=["ls", "long"]), "coin": True,
     "source": "Heston, Korajczyk & Sadka (2010), Journal of Finance (adapted to one fund)",
     "plain": "Each half hour of the day, bet the same way that half hour went on average recently."},
]


# ---------------------------------------------------------------- evaluation
def daily_returns(D, t, sl: slice):
    r = np.zeros(D.n)
    np.add.at(r, t["day"], t["ret"])
    return r[sl]


def sharpe(r):
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(252)) if sd > 1e-12 else 0.0


def cagr(r):
    g = float(np.prod(1 + r))
    return g ** (252 / len(r)) - 1 if g > 0 and len(r) else -1.0


def in_window(t, sl):
    return (t["day"] >= sl.start) & (t["day"] < sl.stop)


def coin_vals(D, t, seed):
    """N_SIMS random-timing values for a trade list (see coin_flip)."""
    k = len(t["ret"])
    if k == 0:
        return np.zeros(N_SIMS)
    rng = np.random.default_rng(seed)
    day, h = t["day"], np.maximum(1, t["exit_m"] - t["entry_m"])
    span = D.last[day] + 2 - h          # valid entry minutes: 0 .. last+1-h
    vals = np.empty(N_SIMS)
    for a in range(0, N_SIMS, 200):
        b = min(N_SIMS, a + 200)
        e = (rng.random((b - a, k)) * np.maximum(span, 1)).astype(int)
        p_in = D.P[day, e]
        p_out = D.P[day, e + h]
        dirs = np.take_along_axis(np.tile(t["dir"], (b - a, 1)), np.argsort(rng.random((b - a, k)), axis=1), axis=1)
        vals[a:b] = np.nansum(dirs * (p_out / p_in - 1) - 2 * COST, axis=1)
    return vals


def coin_flip(D, t, seed, vals=None):
    """Same days, same number of trades, same holding times, shuffled long/short mix; only the entry
    minute moves (uniform within the session). Value = sum of trade returns net of costs."""
    k = len(t["ret"])
    if k == 0:
        return None
    strat = float(t["ret"].sum())
    vals = coin_vals(D, t, seed) if vals is None else vals
    counts, edges = np.histogram(vals, bins=30)
    return {"p_value": (1 + int(np.sum(vals >= strat))) / (1 + N_SIMS), "strategy_value": strat,
            "median_random": float(np.median(vals)), "hist": {"edges": edges.tolist(), "counts": counts.tolist()}}


def evaluate(st, symbol, D):
    I = slice(0, int(D.dates.searchsorted(pd.Timestamp(IS_END), "right")))
    X = slice(int(D.dates.searchsorted(pd.Timestamp(OOS_START))), D.n)
    runs = [st["fn"](D, **p) for p in st["grid"]]
    is_sh = [sharpe(daily_returns(D, t, I)) for t in runs]
    is_n = [int(in_window(t, I).sum()) for t in runs]
    ok = [i for i in range(len(runs)) if is_n[i] >= MIN_IS_TRADES] or list(range(len(runs)))
    b = max(ok, key=lambda i: is_sh[i])
    t = runs[b]
    r = daily_returns(D, t, X)
    tx = {k: v[in_window(t, X)] for k, v in t.items()}
    # 2x costs: subtract one more round trip per trade
    r2 = r.copy()
    np.subtract.at(r2, tx["day"] - X.start, 2 * COST)
    bh = np.r_[0.0, D.close[1:] / D.close[:-1] - 1][X]
    cf = coin_flip(D, tx, SEED + zlib.crc32(f"{st['id']}{symbol}".encode()) % 100000) if st["coin"] else None
    import ftmo
    challenge = ftmo.table(D, t, X.start)
    hold_min = (tx["exit_m"] - tx["entry_m"]).astype(float)
    return {"_t": t, "strategy": st["id"], "asset": symbol, "params": st["grid"][b], "n_trials": len(runs),
            "is_sharpe": is_sh[b], "is_trades": is_n[b],
            "oos": {"sharpe": sharpe(r), "cagr": cagr(r), "total": float(np.prod(1 + r) - 1), "trades": int(len(tx["ret"])),
                    "win_rate": float((tx["ret"] > 0).mean()) if len(tx["ret"]) else None,
                    "avg_trade_bp": float(tx["ret"].mean() * 1e4) if len(tx["ret"]) else None,
                    "avg_hold_min": float(hold_min[hold_min > 0].mean()) if (hold_min > 0).any() else None,
                    "days_traded": float(len(set(tx["day"].tolist())) / max(1, X.stop - X.start))},
            "oos_cost2x_sharpe": sharpe(r2),
            "grid_median_oos_sharpe": float(np.median([sharpe(daily_returns(D, u, X)) for u in runs])),
            "buy_hold_oos": {"sharpe": sharpe(bh), "cagr": cagr(bh)},
            "coin_flip": cf, "ftmo": challenge, "window": [str(D.dates[X.start].date()), str(D.dates[-1].date())],
            "equity": {"dates": [str(x.date()) for x in D.dates[X][::5]],
                       "strategy": [round(float(v), 4) for v in np.cumprod(1 + r)[::5]],
                       "buy_hold": [round(float(v), 4) for v in np.cumprod(1 + bh)[::5]]}}


def main(markets=DISCOVERY):
    rows = []
    for sym in markets:
        D = Days(sym)
        print(f"== {sym}: {D.n} days {D.dates[0].date()}..{D.dates[-1].date()}", flush=True)
        for st in STRATEGIES:
            row = evaluate(st, sym, D)
            rows.append(row)
            cf = row["coin_flip"]
            print(f"  {st['id']:24s} {str(row['params']):34s} IS Sh {row['is_sharpe']:5.2f} | OOS Sh {row['oos']['sharpe']:5.2f} "
                  f"(hold {row['buy_hold_oos']['sharpe']:4.2f}) 2x {row['oos_cost2x_sharpe']:5.2f} trades {row['oos']['trades']:5d} "
                  f"p={'-' if cf is None else format(cf['p_value'], '.3f')}", flush=True)
    tested = [r for r in rows if r["coin_flip"]]
    for r, q in zip(tested, bh_qvalues([r["coin_flip"]["p_value"] for r in tested])):
        r["coin_flip"]["q_value"] = float(q)
        o = r["oos"]
        r["candidate"] = bool(r["coin_flip"]["p_value"] < 0.05 and q < 0.10 and o["sharpe"] > 0 and r["oos_cost2x_sharpe"] > 0)
    return rows


def portfolio(name, members, days, rows_by_key):
    """Equal-weight portfolio of (strategy, market) members in ONE account, at their tuning-year settings.
    Daily return = average of the members' daily returns. Random baseline: every member's trades are
    re-timed at random (same seeds as their own tests) and the members' random values averaged.
    FTMO: the day's worst moment is taken as the average of the members' own worst moments, which is
    never better than the true combined low (members rarely hit their lows at the same minute)."""
    import ftmo
    D0 = days[members[0][1]]
    X = slice(int(D0.dates.searchsorted(pd.Timestamp(OOS_START))), D0.n)
    I_ = slice(0, int(D0.dates.searchsorted(pd.Timestamp(IS_END), "right")))
    n = len(members)
    r_full = np.zeros(D0.n); r2_full = np.zeros(D0.n); fin = np.zeros(D0.n); low = np.zeros(D0.n)
    vals = np.zeros(N_SIMS); strat = 0.0
    for sid, sym in members:
        D, row = days[sym], rows_by_key[(sid, sym)]
        assert D.dates.equals(D0.dates)
        t = row["_t"]
        r = np.zeros(D.n); np.add.at(r, t["day"], t["ret"])
        r2 = r.copy(); np.subtract.at(r2, t["day"], 2 * COST)
        r_full += r / n; r2_full += r2 / n
        f, lo = ftmo.day_paths(D, t)
        fin += f / n; low += lo / n
        k = in_window(t, X)
        tx = {kk: v[k] for kk, v in t.items()}
        vals += coin_vals(D, tx, SEED + zlib.crc32(f"{sid}{sym}".encode()) % 100000) / n
        strat += float(tx["ret"].sum()) / n
    bh = np.r_[0.0, D0.close[1:] / D0.close[:-1] - 1]
    out = []
    starts = range(X.start, D0.n - 20)
    for lev in ftmo.LEVERAGES:
        res = [ftmo.simulate(fin, low, st, lev) for st in starts]
        passed = [d for rr, d in res if rr == "passed"]
        m = len(res)
        out.append({"leverage": lev, "attempts": m, "pass_rate": len(passed) / m,
                    "fail_daily": sum(rr == "failed (daily loss)" for rr, _ in res) / m,
                    "fail_max": sum(rr == "failed (max loss)" for rr, _ in res) / m,
                    "unfinished": sum(rr == "unfinished" for rr, _ in res) / m,
                    "median_days_to_pass": float(np.median(passed)) if passed else None})
    counts, edges = np.histogram(vals, bins=30)
    rx = r_full[X]
    return {"name": name, "members": [list(m) for m in members], "is_sharpe": sharpe(r_full[I_]),
            "oos": {"sharpe": sharpe(rx), "cagr": cagr(rx), "total": float(np.prod(1 + rx) - 1)},
            "oos_cost2x_sharpe": sharpe(r2_full[X]), "buy_hold_oos": {"sharpe": sharpe(bh[X]), "cagr": cagr(bh[X])},
            "coin_flip": {"p_value": (1 + int(np.sum(vals >= strat))) / (1 + N_SIMS), "strategy_value": strat,
                          "median_random": float(np.median(vals)), "hist": {"edges": edges.tolist(), "counts": counts.tolist()}},
            "ftmo": out, "_r": r_full}


def lockbox(st, markets=LOCKBOX):
    out = []
    for sym in markets:
        D = Days(sym)
        r = evaluate(st, sym, D)
        out.append({"asset": sym, "p_value": r["coin_flip"]["p_value"] if r["coin_flip"] else 1.0,
                    "oos_sharpe": r["oos"]["sharpe"], "params": r["params"]})
    fp = fisher_p([o["p_value"] for o in out])
    med = float(np.median([o["oos_sharpe"] for o in out]))
    return {"markets": out, "fisher_p": fp, "median_oos_sharpe": med, "confirmed": bool(fp < 0.05 and med > 0)}


if __name__ == "__main__":
    rows = main()
    by_id = {s["id"]: s for s in STRATEGIES}
    for r in rows:
        r["lockbox"] = lockbox(by_id[r["strategy"]]) if r.get("candidate") else None
    # portfolios, members chosen from TUNING-YEAR results only
    days = {s: Days(s) for s in DISCOVERY}
    key = {(r["strategy"], r["asset"]): r for r in rows}
    tested = [(r["strategy"], r["asset"]) for r in rows if r["coin_flip"]]
    pos_is = [(r["strategy"], r["asset"]) for r in rows if r["coin_flip"] and r["is_sharpe"] > 0]
    ports = [portfolio("A: positive on the tuning years", pos_is, days, key),
             portfolio("B: all tested intraday strategies", tested, days, key)]
    for pf in ports:
        lb_days = {s: Days(s) for s in LOCKBOX}
        lb_rows = []
        for s_ in LOCKBOX:
            for st in STRATEGIES:
                if st["coin"]:
                    lb_rows.append(evaluate(st, s_, lb_days[s_]))
        lkey = {(r["strategy"], r["asset"]): r for r in lb_rows}
        lps = []
        for s_ in LOCKBOX:
            mem = [(r["strategy"], s_) for r in lb_rows if r["asset"] == s_ and
                   ((pf["name"].startswith("A") and r["is_sharpe"] > 0) or pf["name"].startswith("B"))]
            if mem:
                lp = portfolio(pf["name"] + " (" + s_ + ")", mem, {s_: lb_days[s_]}, lkey)
                lps.append({"asset": s_, "members": mem, "p_value": lp["coin_flip"]["p_value"], "oos_sharpe": lp["oos"]["sharpe"]})
        fp = fisher_p([x["p_value"] for x in lps]) if lps else 1.0
        med = float(np.median([x["oos_sharpe"] for x in lps])) if lps else 0.0
        pf["lockbox"] = {"markets": lps, "fisher_p": fp, "median_oos_sharpe": med, "confirmed": bool(fp < 0.05 and med > 0)}
        best = max(pf["ftmo"], key=lambda x: x["pass_rate"])
        print(f"\nportfolio {pf['name']} ({len(pf['members'])} members): IS Sh {pf['is_sharpe']:.2f} | OOS Sh {pf['oos']['sharpe']:.2f} "
              f"2x {pf['oos_cost2x_sharpe']:.2f} p={pf['coin_flip']['p_value']:.3f} | lockbox Fisher p={fp:.3f} median Sh={med:.2f} | "
              f"FTMO best {best['pass_rate']:.0%} at {best['leverage']}x")
    print("\ncandidates (p<0.05, q<0.10, Sharpe>0 at 1x and 2x costs):")
    for r in rows:
        if r.get("candidate"):
            lb = r["lockbox"]
            print(f"  {r['strategy']} {r['asset']}: q={r['coin_flip']['q_value']:.3f}; lockbox Fisher p={lb['fisher_p']:.3f} "
                  f"median Sh={lb['median_oos_sharpe']:.2f} -> {'CONFIRMED' if lb['confirmed'] else 'not confirmed'}")
    if not any(r.get("candidate") for r in rows):
        print("  none")
    for r in rows:
        r.pop("_t", None)
    for pf in ports:
        pf.pop("_r", None)
    OUT.write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "portfolios": ports,
                               "plan": "INTRADAY_PLAN.md", "cost_per_side": COST, "n_sims": N_SIMS,
                               "strategies": [{k: s[k] for k in ("id", "name", "source", "plain")} | {"grid_size": len(s["grid"])}
                                              for s in STRATEGIES], "results": rows}, default=float))
    print(f"wrote {OUT}")
