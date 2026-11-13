"""Study 16 data (FINRL_PLAN_16.md): daily OHLCV of the fixed Dow-30-minus-2 list + SPY/DIA from yfinance, cached as one long
CSV (data/finrl16_ohlcv.csv, ignored by git via data/*.csv). FinRL format: date (str), tic, open, high, low, close, volume, day.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "finrl16_ohlcv.csv"
# FinRL's DOW_30_TICKER minus WBA (delisted 2025, no data) and DOW (no history before 2019-03)
TICKERS = ["AXP", "AMGN", "AAPL", "BA", "CAT", "CSCO", "CVX", "GS", "HD", "HON", "IBM", "INTC", "JNJ", "KO", "JPM", "MCD",
           "MMM", "MRK", "MSFT", "NKE", "PG", "TRV", "UNH", "CRM", "VZ", "V", "WMT", "DIS"]
BENCH = ["SPY", "DIA"]
START = "2009-01-01"


def last_complete_session(frame: pd.DataFrame, today: date | None = None) -> pd.Timestamp:
    """The last date in the frame that is strictly before today (a bar dated today may still be forming)."""
    today = pd.Timestamp(today or date.today())
    d = pd.DatetimeIndex(pd.to_datetime(frame["date"]).unique())
    return d[d < today].max()


def download(path: Path = CACHE) -> pd.DataFrame:
    import yfinance as yf
    syms = TICKERS + BENCH
    raw = yf.download(syms, start=START, interval="1d", auto_adjust=True, progress=False, group_by="ticker")
    parts = []
    for t in syms:
        d = raw[t].dropna(subset=["Close"]).copy()
        d.columns = [c.lower() for c in d.columns]
        d["tic"] = t
        d["date"] = d.index.strftime("%Y-%m-%d")
        parts.append(d[["date", "tic", "open", "high", "low", "close", "volume"]])
    df = pd.concat(parts, ignore_index=True)
    df.to_csv(path, index=False)
    return df


def load(refresh: bool = False) -> pd.DataFrame:
    """Long frame for TICKERS + BENCH, trimmed to the last completed session."""
    df = download() if refresh or not CACHE.exists() else pd.read_csv(CACHE)
    df = df[df["date"] <= last_complete_session(df).strftime("%Y-%m-%d")].copy()
    df["day"] = pd.to_datetime(df["date"]).dt.dayofweek
    return df.sort_values(["date", "tic"]).reset_index(drop=True)


def split_universe(df: pd.DataFrame):
    """(stocks long frame, benchmark close table date x [SPY, DIA])."""
    stocks = df[df["tic"].isin(TICKERS)].copy()
    bench = df[df["tic"].isin(BENCH)].pivot(index="date", columns="tic", values="close")
    return stocks, bench


if __name__ == "__main__":
    d = load(refresh=True)
    print(d.groupby("tic")["date"].agg(["min", "max", "count"]).to_string())
