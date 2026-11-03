"""FTMO 2-Step challenge simulator for the intraday strategies (rules as published for FTMO / OANDA,
checked via search on 2026-11-03; the FTMO site itself was not reachable from this environment):

- Phase 1 (Challenge): reach +10% of initial capital; Phase 2 (Verification): reach +5%;
- maximum daily loss: equity (open trades included) may not fall below the day's starting balance
  minus 5% of INITIAL capital at any moment;
- maximum loss: equity may never fall below 90% of initial capital (static);
- at least 4 trading days per phase; no time limit (we stop counting after 250 trading days);
- Standard account: no weekend holding on the funded account (intraday strategies are flat every night,
  so this only matters for the overnight strategy, which is reported but should not be used on Standard).

Each strategy is replayed from every possible start day of the test window (2021 on), at several sizes:
leverage L means the position is L x account equity in the index (CFD), so a 1% index move is L%.
The intraday low of every day is computed from the minute path of the strategy's positions, so a day
that dips below the daily-loss floor and recovers still FAILS, as it would at FTMO.
Choosing a leverage after seeing this table is itself a choice made with hindsight.
"""
from __future__ import annotations

import numpy as np

import intraday as I

LEVERAGES = [1, 2, 4, 6, 10, 15, 20]
MAX_DAYS = 250


def day_paths(D, t):
    """Per day: (close-to-close return, worst intraday return) of holding the strategy at 1x."""
    n = D.n
    final = np.zeros(n)
    low = np.zeros(n)
    by_day = {}
    for i in range(len(t["ret"])):
        by_day.setdefault(int(t["day"][i]), []).append(i)
    for d, idx in by_day.items():
        cum, worst = 0.0, 0.0
        for i in sorted(idx, key=lambda j: t["entry_m"][j]):
            e, x, s = int(t["entry_m"][i]), int(t["exit_m"][i]), t["dir"][i]
            if e < 0:  # overnight trade: entered at the previous close, exits at this open
                cum += t["ret"][i]
                worst = min(worst, cum)
                continue
            p0 = D.P[d, e]
            seg = D.P[d, e:x + 1]
            lows = np.minimum(D.L[d, e:min(x, 390)], D.P[d, e:min(x, 390)]) if s > 0 else \
                np.maximum(D.H[d, e:min(x, 390)], D.P[d, e:min(x, 390)])
            path = s * (lows / p0 - 1) if len(lows) else np.array([0.0])
            worst = min(worst, cum + float(np.min(path)) - I.COST)
            cum += t["ret"][i]
            worst = min(worst, cum)
        final[d], low[d] = cum, worst
    return final, low


def simulate(final, low, start, lev):
    """Run both phases from day index `start`. Returns (result, days_used)."""
    eq, init = 1.0, 1.0
    d = start
    for target in (0.10, 0.05):
        base = eq if target == 0.05 else init  # phase 2 starts on a fresh account of the same size
        eq = init
        traded = 0
        for k in range(MAX_DAYS):
            if d >= len(final):
                return "unfinished", d - start
            day_start = eq
            lo = day_start * (1 + lev * low[d])
            if lo <= day_start - 0.05 * init or lo <= 0.90 * init:
                return "failed (daily loss)" if lo <= day_start - 0.05 * init else "failed (max loss)", d - start + 1
            eq = day_start * (1 + lev * final[d])
            if final[d] != 0:
                traded += 1
            d += 1
            if eq >= init * (1 + target) and traded >= 4:
                break
        else:
            return "unfinished", d - start
    return "passed", d - start


def table(D, t, oos_start_idx):
    final, low = day_paths(D, t)
    out = []
    starts = range(oos_start_idx, D.n - 20)
    for lev in LEVERAGES:
        res = [simulate(final, low, s, lev) for s in starts]
        n = len(res)
        passed = [days for r, days in res if r == "passed"]
        out.append({"leverage": lev, "attempts": n,
                    "pass_rate": len(passed) / n if n else 0.0,
                    "fail_daily": sum(r == "failed (daily loss)" for r, _ in res) / n if n else 0.0,
                    "fail_max": sum(r == "failed (max loss)" for r, _ in res) / n if n else 0.0,
                    "unfinished": sum(r == "unfinished" for r, _ in res) / n if n else 0.0,
                    "median_days_to_pass": float(np.median(passed)) if passed else None})
    return out
