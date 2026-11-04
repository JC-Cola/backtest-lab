"""Intraday study 5 (INTRADAY_PLAN_5.md): the Darvas box on minute bars, SPY and QQQ.
Same clock, costs, windows and tests as intraday.py; the runner is intraday3.main.

    python src/intraday5.py              # discovery on SPY and QQQ
    python src/intraday5.py --lockbox    # also run a candidate on XLK, SMH, XLF, XLE (downloads them)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import intraday as I
import intraday3 as S3
from indicators import latch
from intraday import _trades, grid, runs_to_trades
from strategies_trend import find_boxes

LOCKBOX = ["XLK", "SMH", "XLF", "XLE"]
OUT = I.ROOT / "results" / "intraday5.json"


def darvas_intraday(D, bar=5, hold=3):
    """Darvas (1960) inside each session on `bar`-minute bars: long on a close above the last box top confirmed
    before that bar, out on a close below the latest confirmed box bottom; boxes never span days; flat at the close."""
    rows = []
    for d in range(D.n):
        end = D.last[d] + 1
        nb = end // bar
        if nb < 2 * hold + 2:
            continue
        H = D.H[d, :nb * bar].reshape(nb, bar).max(axis=1)
        L = D.L[d, :nb * bar].reshape(nb, bar).min(axis=1)
        C = D.C[d, bar - 1:nb * bar:bar]
        top_at, bot_at = np.full(nb, np.nan), np.full(nb, np.nan)
        for ci, top, bot in find_boxes(H, L, hold):
            top_at[ci], bot_at[ci] = top, bot
        top_at, bot_at = pd.Series(top_at).ffill().values, pd.Series(bot_at).ffill().values
        top_prev = np.r_[np.nan, top_at[:-1]]  # the box must be confirmed before the breakout bar
        with np.errstate(invalid="ignore"):
            pos = latch(C > top_prev, C < bot_at)
        side = np.zeros(end)
        for b in range(nb):  # decided on the close of bar b, held from the next minute
            side[(b + 1) * bar: min((b + 2) * bar if b < nb - 1 else end, end)] = pos[b]
        rows += runs_to_trades(D, d, side)
    return _trades(rows)


STRATEGIES = [
    {"id": "id_darvas_intraday", "name": "Darvas Box (intraday)", "fn": darvas_intraday, "grid": grid(bar=[5, 15], hold=[3, 5]),
     "coin": True, "study": 5, "source": "Nicolas Darvas, How I Made $2,000,000 in the Stock Market (1960), on minute bars",
     "plain": "Buy a break above the day's latest price box; sell if it falls below the box's floor."},
]


if __name__ == "__main__":
    S3.main(STRATEGIES, LOCKBOX, OUT, "INTRADAY_PLAN_5.md")
