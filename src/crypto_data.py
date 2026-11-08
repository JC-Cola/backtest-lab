"""Crypto data for study 9 (CRYPTO_PLAN_9.md): Coinbase Exchange public candles (daily and hourly, UTC) and Binance
USD-M perpetual funding rates (data.binance.vision monthly files). Cached under data/crypto/ (git-ignored).

    python src/crypto_data.py BTC-USD ETH-USD SOL-USD          # daily + hourly (hourly for BTC-USD only with --hourly)
    python src/crypto_data.py --funding                        # BTCUSDT ETHUSDT SOLUSDT monthly funding zips
"""
from __future__ import annotations

import io
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

DIR = Path(__file__).resolve().parent.parent / "data" / "crypto"
FUNDING_DIR = DIR / "funding"
API = "https://api.exchange.coinbase.com/products/{p}/candles"
BIN = "https://data.binance.vision/data/futures/um/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip"
FUNDING_SYMBOLS = {"BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT"}
FUNDING_START = "2020-01"


def last_complete_day() -> pd.Timestamp:
    """The last COMPLETE UTC day (today's bar is still forming)."""
    return pd.Timestamp(datetime.now(timezone.utc).date() - timedelta(days=1))


def fetch_candles(product: str, gran: int, start: datetime, end: datetime) -> pd.DataFrame:
    """Page through Coinbase candles (max 300 per request). Returns Open/High/Low/Close/Volume indexed by UTC bar start."""
    step = timedelta(seconds=gran * 300)
    rows, t = [], start
    s = requests.Session()
    while t < end:
        t2 = min(t + step, end)
        for attempt in range(6):
            r = s.get(API.format(p=product), params={"granularity": gran, "start": t.isoformat(), "end": t2.isoformat()}, timeout=30)
            if r.status_code == 200:
                rows += r.json()
                break
            time.sleep(1.5 * (attempt + 1))
        else:
            raise RuntimeError(f"{product} {t}: HTTP {r.status_code} {r.text[:100]}")
        t = t2
        time.sleep(0.12)
    df = pd.DataFrame(rows, columns=["t", "Low", "High", "Open", "Close", "Volume"])
    df.index = pd.to_datetime(df.pop("t"), unit="s")
    df.index.name = "Date"
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)[~df.index.duplicated()].sort_index()


def fill_gaps(df: pd.DataFrame, freq: str) -> tuple[pd.DataFrame, int]:
    """Reindex to every bar; a missing bar becomes a zero-return bar at the previous close. Returns (frame, n filled)."""
    full = df.reindex(pd.date_range(df.index[0], df.index[-1], freq=freq))
    miss = full.Close.isna()
    prev = full.Close.ffill()
    for c in ("Open", "High", "Low", "Close"):
        full[c] = full[c].where(~miss, prev)
    full["Volume"] = full["Volume"].fillna(0.0)
    full.index.name = "Date"
    return full, int(miss.sum())


def download(product: str, hourly: bool = False, first: str = "2015-01-01") -> Path:
    """Download the full history to data/crypto/<product>[_1h].csv (re-download when the cache is older than a day)."""
    DIR.mkdir(parents=True, exist_ok=True)
    gran, freq, suffix = (3600, "h", "_1h") if hourly else (86400, "D", "")
    path = DIR / f"{product}{suffix}.csv"
    if path.exists() and time.time() - path.stat().st_mtime < 86400:
        return path
    end = datetime.combine(last_complete_day().date() + timedelta(days=1), datetime.min.time())  # exclusive
    start = datetime.fromisoformat(first)
    df = fetch_candles(product, gran, start, end)
    df, filled = fill_gaps(df, freq)
    df = df[df.index < pd.Timestamp(end)]
    df.to_csv(path)
    print(f"{product}{suffix}: {len(df)} bars {df.index[0]} -> {df.index[-1]}, {filled} gap bars filled")
    return path


def load(product: str, hourly: bool = False) -> pd.DataFrame:
    path = DIR / f"{product}{'_1h' if hourly else ''}.csv"
    if not path.exists():
        download(product, hourly)
    return pd.read_csv(path, index_col=0, parse_dates=True)


def download_funding(symbol: str, through: pd.Timestamp | None = None) -> pd.DataFrame:
    """Monthly funding zips from FUNDING_START to the last month that exists; cached as one CSV per symbol."""
    FUNDING_DIR.mkdir(parents=True, exist_ok=True)
    out = FUNDING_DIR / f"{symbol}.csv"
    parts = []
    for m in pd.period_range(FUNDING_START, (through or last_complete_day()).to_period("M"), freq="M"):
        zp = FUNDING_DIR / f"{symbol}-{m}.zip"
        if not zp.exists():
            r = requests.get(BIN.format(s=symbol, m=m), timeout=60)
            if r.status_code != 200:
                print(f"  {symbol} {m}: HTTP {r.status_code} (not published yet or not listed)")
                continue
            zp.write_bytes(r.content)
        with zipfile.ZipFile(zp) as z:
            parts.append(pd.read_csv(io.BytesIO(z.read(z.namelist()[0]))))
    f = pd.concat(parts, ignore_index=True)
    f["time"] = pd.to_datetime(f["calc_time"], unit="ms")
    f = f.set_index("time")[["last_funding_rate"]].sort_index()
    f = f[~f.index.duplicated()]
    f.to_csv(out)
    print(f"{symbol}: {len(f)} funding rows {f.index[0]} -> {f.index[-1]}")
    return f


def load_funding(symbol: str) -> pd.Series:
    path = FUNDING_DIR / f"{symbol}.csv"
    if not path.exists():
        download_funding(symbol)
    return pd.read_csv(path, index_col=0, parse_dates=True)["last_funding_rate"]


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if "--funding" in sys.argv:
        for s in (a or FUNDING_SYMBOLS.values()):
            download_funding(s)
    for p in a if "--funding" not in sys.argv else []:
        download(p)
        if "--hourly" in sys.argv:
            download(p, hourly=True)
