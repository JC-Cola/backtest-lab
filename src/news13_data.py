"""Study 13 data: Alpaca 1-minute bars (first 45 minutes of each session, 09:30-10:14 NY bar starts) and daily bars,
split-adjusted, cached in data/news13/ (gitignored).

    python src/news13_data.py NVDA AAPL ...        # needs Alpaca data keys (src/alpaca_keys.py); never prints them

Feed: SIP; falls back to IEX when SIP is refused (the feed used is recorded in data/news13/feed_<SYM>.txt).
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

DIR = Path(__file__).resolve().parent.parent / "data" / "news13"
NY = "America/New_York"
START = "2016-01-01"
DAILY_START = "2015-09-01"
FIRST = 570            # 09:30
LAST = 615             # bar starts < 10:15 are kept
_client_cache = {}


def _client():
    if "c" not in _client_cache:
        import alpaca_keys
        from alpaca.data.historical import StockHistoricalDataClient
        k, s = alpaca_keys.data_keys()
        if not (k and s):
            raise RuntimeError("no Alpaca data keys")
        _client_cache["c"] = StockHistoricalDataClient(k, s)
    return _client_cache["c"]


def keep_open_window(df):
    """Keep bars whose START (NY) is 09:30 <= t < 10:15."""
    t = df.index.hour * 60 + df.index.minute
    return df[(t >= FIRST) & (t < LAST)]


def _fetch(req_cls, getter, sym, tf, t0, t1, feed):
    from alpaca.data.enums import Adjustment
    req = req_cls(symbol_or_symbols=sym, timeframe=tf, start=t0, end=t1, feed=feed, adjustment=Adjustment.SPLIT)
    return getattr(_client(), getter)(req).df


def _get(sym, kind, t0, t1):
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    tf = TimeFrame.Minute if kind == "min" else TimeFrame.Day
    feeds = [DataFeed.SIP, DataFeed.IEX]
    path = DIR / f"feed_{sym}.txt"
    if path.exists() and path.read_text().strip() == "iex":
        feeds = [DataFeed.IEX]
    last = None
    for f in feeds:
        for i in range(6):
            try:
                df = _fetch(StockBarsRequest, "get_stock_bars", sym, tf, t0, t1, f)
                if f == DataFeed.IEX:
                    path.write_text("iex")
                elif not path.exists():
                    path.write_text("sip")
                return df
            except Exception as e:           # noqa: BLE001
                last = e
                msg = str(e).lower()
                if "subscription" in msg or "sip" in msg and ("not" in msg or "forbidden" in msg) or "403" in msg:
                    break                     # SIP refused: next feed
                time.sleep(min(30, 2 ** i))
    raise RuntimeError(f"{sym} {kind} {t0}: {type(last).__name__}")


def download(sym, end=None):
    DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    end_d = end or (now - timedelta(days=1)).strftime("%Y-%m-%d")      # last fully completed session at the latest
    t_start = time.time()
    parts = []
    for y in range(int(START[:4]), int(end_d[:4]) + 1):
        cache = DIR / f"min_{sym}_{y}.pkl"
        if cache.exists() and y < now.year:
            parts.append(pd.read_pickle(cache)); continue
        a, b = max(START, f"{y}-01-01"), min(end_d, f"{y}-12-31")
        t0 = datetime.fromisoformat(a).replace(tzinfo=timezone.utc)
        t1 = datetime.fromisoformat(b).replace(hour=23, minute=59, tzinfo=timezone.utc)
        df = _get(sym, "min", t0, t1)
        if df.empty:
            continue
        df = df.droplevel(0)
        df.index = df.index.tz_convert(NY)
        df = keep_open_window(df)[["open", "high", "low", "close", "volume"]]
        df.to_pickle(cache)
        parts.append(df)
    out = pd.concat(parts).sort_index()
    out = out[~out.index.duplicated()]
    out.to_pickle(DIR / f"min_{sym}.pkl")
    d = _get(sym, "day", datetime.fromisoformat(DAILY_START).replace(tzinfo=timezone.utc),
             datetime.fromisoformat(end_d).replace(hour=23, minute=59, tzinfo=timezone.utc)).droplevel(0)
    d.index = pd.DatetimeIndex([x.tz_convert(NY).tz_localize(None).normalize() for x in d.index])
    d[["open", "high", "low", "close", "volume"]].to_pickle(DIR / f"day_{sym}.pkl")
    return time.time() - t_start, len(out)


def feed(sym):
    p = DIR / f"feed_{sym}.txt"
    return p.read_text().strip() if p.exists() else "unknown"


def load_min(sym):
    return pd.read_pickle(DIR / f"min_{sym}.pkl")


def load_day(sym):
    return pd.read_pickle(DIR / f"day_{sym}.pkl")


if __name__ == "__main__":
    for s in sys.argv[1:]:
        secs, n = download(s)
        print(f"{s}: {n:,} bars, {secs:.0f}s, feed {feed(s)}", flush=True)
