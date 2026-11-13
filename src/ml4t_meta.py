"""Meta-labeling of the intraday noise-band breakout (ML4T / Lopez de Prado; ML4T_PLAN_17.md part a).

The primary rule is intraday.noise_breakout (unchanged). Each of its trades (runs) becomes one row: features known at the
CLOSE of bar entry_m - 1 (the bar the rule decided on), label from the trade's own outcome (fill at the open of bar entry_m
onward). A LightGBM classifier with fixed hyper-parameters predicts whether the trade pays; trades with P <= threshold are skipped.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import intraday as I

ROOT = Path(__file__).resolve().parent.parent
VIX_CSV = ROOT / "data" / "ctx_VIX.csv"
FEATURES = ["tod", "dist_sigma", "vwap_dist", "ret5", "ret15", "ret60", "range_sofar", "vix_prev", "gap", "prior_ret", "dir"]
LGB_PARAMS = dict(objective="binary", num_leaves=7, n_estimators=150, learning_rate=0.03, min_child_samples=50, subsample=0.8,
                  subsample_freq=1, colsample_bytree=0.8, random_state=0, n_jobs=2, verbose=-1, deterministic=True, force_row_wise=True)


def load_vix(path: Path = VIX_CSV) -> pd.Series:
    s = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s.sort_index()


def vix_prior_close(vix: pd.Series, dates) -> np.ndarray:
    """VIX close of the last date strictly before each session date (NaN if none)."""
    idx = vix.index.searchsorted(pd.DatetimeIndex(dates), side="left") - 1
    return np.where(idx >= 0, vix.values[np.clip(idx, 0, None)], np.nan)


def trade_features(D, d, m, direction, lookback, vix_prev):
    """Features of a decision on the close of bar m of session d. Uses only bars <= m of day d and days < d."""
    move = np.abs(D.C[:d] / D.O[:d, :1] - 1)
    sig = move[d - lookback:d].mean(axis=0)[m]
    up = max(D.O[d, 0], D.prev_close[d]) * (1 + sig)
    dn = min(D.O[d, 0], D.prev_close[d]) * (1 - sig)
    p = D.C[d, m]
    dist = ((p / up - 1) if direction > 0 else (dn / p - 1)) / sig if sig > 0 else np.nan
    base = lambda k: D.O[d, 0] if m - k < 0 else D.C[d, m - k]  # look-back clipped at the open
    hi, lo = D.H[d, :m + 1].max(), D.L[d, :m + 1].min()
    prior = D.close[d - 1] / D.close[d - 2] - 1 if d >= 2 else np.nan
    return [m, dist, direction * (p / D.vwap[d, m] - 1), direction * (p / base(5) - 1), direction * (p / base(15) - 1),
            direction * (p / base(60) - 1), (hi - lo) / D.O[d, 0], vix_prev, direction * (D.O[d, 0] / D.prev_close[d] - 1),
            direction * prior, direction]


def trade_label(D, d, entry_m, exit_m, direction):
    """(y, y_close) from the entry fill onward only: net trade return > 0; and holding to the close > 0 (net of 2 bp)."""
    pin = D.P[d, entry_m]
    pout = D.P[d, exit_m]
    y = direction * (pout / pin - 1) - 2 * I.COST > 0
    yc = direction * (D.P[d, D.last[d] + 1] / pin - 1) - 2 * I.COST > 0
    return int(y), int(yc)


def trade_table(D, vix, lookback=14, check=60, trades=None) -> pd.DataFrame:
    t = I.noise_breakout(D, lookback=lookback, check=check) if trades is None else trades
    vp = vix_prior_close(vix, D.dates)
    rows = []
    for i in range(len(t["ret"])):
        d, em, xm, di = int(t["day"][i]), int(t["entry_m"][i]), int(t["exit_m"][i]), float(t["dir"][i])
        f = trade_features(D, d, em - 1, di, lookback, vp[d])
        y, yc = trade_label(D, d, em, xm, di)
        rows.append([d, em, xm, float(t["ret"][i]), y, yc] + f)
    return pd.DataFrame(rows, columns=["day", "entry_m", "exit_m", "ret", "y", "y_close"] + FEATURES)


def make_model():
    import lightgbm as lgb
    return lgb.LGBMClassifier(**LGB_PARAMS)


def fit_predict(Xtr, ytr, Xte):
    if len(np.unique(ytr)) < 2:
        return np.full(len(Xte), float(np.mean(ytr)))
    m = make_model().fit(Xtr, ytr)
    return m.predict_proba(Xte)[:, 1]


# ------------------------------------------------------------------ evaluation helpers
def daily_from_mask(D, tab, mask, sl: slice, mult=1):
    """Daily returns on `sl` of the taken trades; mult 2 = one more round trip of costs per trade."""
    r = np.zeros(D.n)
    rets = tab["ret"].values - (2 * I.COST if mult == 2 else 0.0)
    np.add.at(r, tab["day"].values[mask], rets[mask])
    return r[sl]


def max_dd(r):
    eq = np.cumprod(1 + np.asarray(r))
    return float((eq / np.maximum.accumulate(np.r_[1.0, eq])[1:] - 1).min())


def summarize(r, ntr=None):
    return {"sharpe": I.sharpe(r), "total": float(np.prod(1 + r) - 1), "max_drawdown": max_dd(r), "worst_day": float(r.min()),
            **({"trades": int(ntr)} if ntr is not None else {})}
