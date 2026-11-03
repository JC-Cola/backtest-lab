"""Minute bars from Alpaca's historical market data (full-market SIP feed, split- and dividend-
adjusted), cached in data/intraday/<SYMBOL>.pkl. Regular trading hours only for stocks
(09:30-16:00 New York), all hours for crypto. Timestamps are bar START times in New York time."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DIR = Path(__file__).resolve().parent.parent / "data" / "intraday"
NY = "America/New_York"


def _client(crypto):
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
    k, s = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
    return CryptoHistoricalDataClient(k, s) if crypto else StockHistoricalDataClient(k, s)


def download(symbol: str, start="2016-01-01", end=None):
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    crypto = "/" in symbol
    cl = _client(crypto)
    end = end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = []
    for y in range(int(start[:4]), int(end[:4]) + 1):
        a, b = max(start, f"{y}-01-01"), min(end, f"{y}-12-31")
        t0 = datetime.fromisoformat(a).replace(tzinfo=timezone.utc)
        t1 = datetime.fromisoformat(b).replace(hour=23, minute=59, tzinfo=timezone.utc)
        if crypto:
            req = CryptoBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, start=t0, end=t1)
            df = cl.get_crypto_bars(req).df
        else:
            req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, start=t0, end=t1,
                                   feed=DataFeed.SIP, adjustment=Adjustment.ALL)
            df = cl.get_stock_bars(req).df
        if df.empty:
            print(f"  {symbol} {y}: no bars", flush=True)
            continue
        df = df.droplevel(0)
        df.index = df.index.tz_convert(NY)
        if not crypto:
            t = df.index.hour * 60 + df.index.minute
            df = df[(t >= 570) & (t < 960)]  # 09:30 <= bar start < 16:00
        parts.append(df[["open", "high", "low", "close", "volume"]])
        print(f"  {symbol} {y}: {len(df):,} bars", flush=True)
    out = pd.concat(parts).sort_index()
    out = out[~out.index.duplicated()]
    DIR.mkdir(parents=True, exist_ok=True)
    out.to_pickle(DIR / f"{symbol.replace('/', '')}.pkl")
    return out


def load(symbol: str) -> pd.DataFrame:
    return pd.read_pickle(DIR / f"{symbol.replace('/', '')}.pkl")


if __name__ == "__main__":
    for s in sys.argv[1:] or ["SPY", "QQQ", "IWM", "DIA"]:
        print(s, flush=True)
        download(s)
