"""Causal indicator helpers (trailing windows only)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n).mean()


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int) -> pd.Series:
    """Wilder RSI."""
    d = close.diff()
    au = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ad = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + au / ad)


def ann_factor(df: pd.DataFrame) -> int:
    """365 if the index trades weekends (crypto), else 252."""
    return 365 if (df.index.dayofweek >= 5).mean() > 0.1 else 252


def latch(enter, exit_) -> np.ndarray:
    """Long (1.0) from an enter signal until an exit signal; both decided on closes."""
    enter, exit_ = np.asarray(enter, bool), np.asarray(exit_, bool)
    out = np.zeros(len(enter))
    pos = 0.0
    for t in range(len(enter)):
        if pos:
            if exit_[t]:
                pos = 0.0
        elif enter[t]:
            pos = 1.0
        out[t] = pos
    return out
