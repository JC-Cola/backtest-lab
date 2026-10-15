"""Daily OHLC download + 1-day CSV cache."""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
COLS = ["Open", "High", "Low", "Close"]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df[COLS].apply(pd.to_numeric, errors="coerce")
    df = df.replace(0, float("nan")).dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "Date"
    return df[~df.index.duplicated()].sort_index()


def load(ticker: str, max_age_s: float = 86400, cache_dir: Path = DATA_DIR) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{ticker}.csv"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        return _clean(pd.read_csv(path, index_col=0, parse_dates=True))
    try:
        import yfinance as yf

        raw = yf.download(ticker, period="max", interval="1d", auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = _clean(raw)
        if df.empty:
            raise RuntimeError("empty download")
        df.to_csv(path)
        return df
    except Exception as e:  # fall back to stale cache rather than die
        if path.exists():
            print(f"  [warn] download failed for {ticker} ({e}); using stale cache")
            return _clean(pd.read_csv(path, index_col=0, parse_dates=True))
        raise


CTX_TICKERS = ["^VIX", "^VIX3M", "^IRX", "^TNX", "HYG", "IEF", "LQD", "UUP", "SPY", "TLT", "AGG", "EFA"]


def load_close(ticker: str, max_age_s: float = 86400, cache_dir: Path = DATA_DIR) -> pd.Series:
    """Daily close of a context series, cached as data/ctx_<ticker>.csv (same 1-day cache policy)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"ctx_{ticker.replace('^', '')}.csv"

    def read():
        s = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
        s.index = pd.to_datetime(s.index).tz_localize(None)
        return s.rename(ticker)

    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        return read()
    try:
        import yfinance as yf

        raw = yf.download(ticker, period="max", interval="1d", auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        s = pd.to_numeric(raw["Close"], errors="coerce").dropna()
        s = s[s > 0]
        s.index = pd.to_datetime(s.index).tz_localize(None)
        s.index.name = "Date"
        s = s[~s.index.duplicated()].sort_index()
        if s.empty:
            raise RuntimeError("empty download")
        s.rename("Close").to_csv(path)
        return s.rename(ticker)
    except Exception as e:
        if path.exists():
            print(f"  [warn] download failed for {ticker} ({e}); using stale cache")
            return read()
        raise


def load_context(tickers=CTX_TICKERS) -> pd.DataFrame:
    """Daily closes of the cross-asset context series (VIX etc.), one column per ticker."""
    return pd.concat([load_close(t) for t in tickers], axis=1).sort_index()


def align_ctx(ctx: pd.DataFrame | None, index: pd.DatetimeIndex) -> pd.DataFrame | None:
    """Context aligned to an asset's index using only PAST values (forward-fill, never back-fill).
    Values before a series starts stay NaN (strategies treat NaN as 'no signal' -> flat)."""
    if ctx is None:
        return None
    full = ctx.reindex(ctx.index.union(index)).ffill()
    return full.reindex(index)
