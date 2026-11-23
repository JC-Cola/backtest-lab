"""Minute-grid price history for the universe names. SPY/QQQ (and any name with a full minute cache) use the 1-minute bars of
intraday_data; every other name is stored as 5-minute bars (about 4 MB per name gzip'd, vs 100 MB at 1 minute) and expanded
onto the 390-minute grid the engine uses:

  5-min bar j covers minutes 5j..5j+4. Minute 5j carries the bar's open, high, low and volume; minutes 5j+1..5j+3 are flat at
  the open; minute 5j+4 closes at the bar's close. So every price the engine reads at a 5-minute boundary (fills P[d, m],
  C[m-1] features, cumulative highs/lows) is a real 5-minute price, and stops/targets see a bar's full range at its start.
Timestamps are bar START times in New York time, regular hours only, like intraday_data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import intraday as I
import intraday_data

DIR5 = intraday_data.DIR / "5m"
NY = intraday_data.NY


def path5(symbol: str):
    return DIR5 / f"{symbol.replace('/', '')}.pkl.gz"


def expand(df: pd.DataFrame) -> pd.DataFrame:
    """5-minute bars (open/high/low/close/volume, bar-start NY index) -> the 1-minute frame described above."""
    n = len(df)
    idx = (df.index.values[:, None] + (np.arange(5) * np.timedelta64(1, "m"))[None, :]).ravel()
    o = np.repeat(df["open"].to_numpy(float), 5)
    c = o.copy()
    h, lo, v = o.copy(), o.copy(), np.zeros(n * 5)
    h[0::5], lo[0::5], v[0::5] = df["high"].to_numpy(float), df["low"].to_numpy(float), df["volume"].to_numpy(float)
    cl = df["close"].to_numpy(float)
    c[4::5] = cl
    h[4::5], lo[4::5] = np.maximum(o[4::5], cl), np.minimum(o[4::5], cl)
    ix = pd.DatetimeIndex(idx)                                    # UTC clock when df is tz-aware, wall clock when naive
    ix = ix.tz_localize("UTC").tz_convert(NY) if df.index.tz is not None else ix.tz_localize(NY)
    out = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v}, index=ix)
    return out


def has_minute(symbol: str) -> bool:
    return (intraday_data.DIR / f"{symbol.replace('/', '')}.pkl").exists()


def load_days(symbol: str, download: bool = False) -> "I.Days":
    """Days for any universe name: 1-minute cache when there is one (SPY, QQQ), else the 5-minute file. Research runs only
    READ (the nightly job prefetches, see prefetch()), so a missing file raises FileNotFoundError and the caller skips the
    name; download=True fetches it on the spot (needs Alpaca keys)."""
    if has_minute(symbol):
        return I.Days(symbol)
    p = path5(symbol)
    if not p.exists():
        if not download:
            raise FileNotFoundError(f"no 5-minute bars cached for {symbol} (the nightly prefetch fetches them)")
        download5(symbol)
    df = pd.read_pickle(p)
    return I.Days.from_frame(expand(df))


def download5(symbol: str, start: str = "2016-01-01") -> pd.DataFrame:
    """5-minute SIP bars (split/dividend adjusted), regular hours, cached in data/intraday/5m/. Re-running appends the new days."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    cl = intraday_data._client(False)
    p = path5(symbol)
    old = pd.read_pickle(p) if p.exists() else None
    if old is not None:
        start = str((old.index[-1] + timedelta(days=1)).date())
    now_cap = datetime.now(timezone.utc) - timedelta(minutes=20)
    if start > now_cap.strftime("%Y-%m-%d"):
        return old
    parts = [] if old is None else [old]
    for y in range(int(start[:4]), now_cap.year + 1):
        a = datetime.fromisoformat(max(start, f"{y}-01-01")).replace(tzinfo=timezone.utc)
        b = min(datetime.fromisoformat(f"{y}-12-31").replace(hour=23, minute=59, tzinfo=timezone.utc), now_cap)
        df = cl.get_stock_bars(StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                                                start=a, end=b, feed=DataFeed.SIP, adjustment=Adjustment.ALL)).df
        if df.empty:
            continue
        df = df.droplevel(0)
        df.index = df.index.tz_convert(NY)
        t = df.index.hour * 60 + df.index.minute
        parts.append(df[(t >= 570) & (t < 960)][["open", "high", "low", "close", "volume"]].astype("float32"))
    if not parts:
        raise RuntimeError(f"no 5-minute bars for {symbol}")
    out = pd.concat(parts).sort_index()
    out = out[~out.index.duplicated()]
    DIR5.mkdir(parents=True, exist_ok=True)
    out.to_pickle(p, compression="gzip")
    return out


def prefetch(symbols, log=print) -> dict:
    """Create or extend (new days only) the 5-minute file of every symbol without a 1-minute cache. One failing name never
    stops the others. Returns {symbol: "ok" | "skipped" | error text}."""
    out = {}
    for sym in symbols:
        if has_minute(sym):
            out[sym] = "skipped"
            continue
        try:
            df = download5(sym)
            out[sym] = "ok"
            log(f"  {sym}: {len(df):,} bars to {df.index[-1]:%Y-%m-%d}")
        except Exception as ex:
            out[sym] = f"{type(ex).__name__}: {str(ex)[:100]}"
            log(f"  {sym}: FAILED {out[sym]}")
    return out


def main(root=None) -> dict:
    """Nightly: refresh the universe file when it is due, then prefetch 5-minute bars for every search and lockbox name."""
    from iagent import universe as U
    from iagent.memory import IMemory
    root = root or IMemory().root
    uni = U.ensure(root)
    names = sorted(set(uni["search"]) | set(uni["lockbox"]))
    print(f"universe refreshed {uni.get('refreshed')}: {len(names)} names", flush=True)
    res = prefetch(names)
    bad = {k: v for k, v in res.items() if v not in ("ok", "skipped")}
    print(f"prefetch: {len(res) - len(bad)} ok, {len(bad)} failed", flush=True)
    return res


if __name__ == "__main__":
    main()
