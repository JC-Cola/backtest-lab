"""Study 14 data: full regular-session 1-minute bars (bar starts 09:30-15:59 NY) as open/high/low/close arrays per
session, cached in data/news14/ (gitignored). Reuses study 13's Alpaca fetch (SIP, split-adjusted).

    python src/news14_data.py NVDA AAPL ...
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import news13_data as D13

DIR = Path(__file__).resolve().parent.parent / "data" / "news14"
NB = 390


def to_arrays(df):
    """df: minute bars indexed by NY tz timestamps -> (dates, O, H, L, C) with shape (n_sessions, 390); NaN = no bar."""
    df = df[~df.index.duplicated()]
    t = df.index.hour * 60 + df.index.minute
    df = df[(t >= 570) & (t < 960)]
    j = (df.index.hour * 60 + df.index.minute - 570).values
    sess = pd.DatetimeIndex(df.index.tz_localize(None).normalize())
    dates = pd.DatetimeIndex(sorted(set(sess)))
    pos = np.searchsorted(dates.asi8, sess.asi8)
    out = {k: np.full((len(dates), NB), np.nan, np.float32) for k in "ohlc"}
    for k, col in zip("ohlc", ("open", "high", "low", "close")):
        out[k][pos, j] = df[col].values
    return dates, out["o"], out["h"], out["l"], out["c"]


def download(sym, end=None):
    DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    end_d = end or (now - timedelta(days=1)).strftime("%Y-%m-%d")
    t_start = time.time()
    parts = []
    for y in range(int(D13.START[:4]), int(end_d[:4]) + 1):
        cache = DIR / f"full_{sym}_{y}.npz"
        if cache.exists() and y < now.year:
            z = np.load(cache)
            parts.append((pd.DatetimeIndex(z["dates"]), z["o"], z["h"], z["l"], z["c"])); continue
        a, b = max(D13.START, f"{y}-01-01"), min(end_d, f"{y}-12-31")
        t0 = datetime.fromisoformat(a).replace(tzinfo=timezone.utc)
        t1 = datetime.fromisoformat(b).replace(hour=23, minute=59, tzinfo=timezone.utc)
        df = D13._get(sym, "min", t0, t1)
        if df.empty:
            continue
        df = df.droplevel(0)
        df.index = df.index.tz_convert(D13.NY)
        d, o, h, l, c = to_arrays(df)
        np.savez_compressed(cache, dates=d.values, o=o, h=h, l=l, c=c)
        parts.append((d, o, h, l, c))
    dates = pd.DatetimeIndex(np.concatenate([p[0].values for p in parts]))
    arr = [np.concatenate([p[i] for p in parts]) for i in range(1, 5)]
    np.savez_compressed(DIR / f"full_{sym}.npz", dates=dates.values, o=arr[0], h=arr[1], l=arr[2], c=arr[3])
    return time.time() - t_start, len(dates)


def load_full(sym):
    z = np.load(DIR / f"full_{sym}.npz")
    return pd.DatetimeIndex(z["dates"]), z["o"].astype(float), z["h"].astype(float), z["l"].astype(float), z["c"].astype(float)


if __name__ == "__main__":
    for s in sys.argv[1:]:
        secs, n = download(s)
        print(f"{s}: {n} sessions, {secs:.0f}s", flush=True)
