"""qlib Alpha158 features + LightGBM, with the normalisation written out (so training, backtest and the nightly job share it).

Features: qlib's own `Alpha158` handler with NO processors (raw). Then exactly what the benchmark config's processors do:
  inputs   RobustZScoreNorm (median / 1.4826*MAD per feature, fitted on the TRAIN rows only, clipped to +-3), Fillna(0)
  label    CSRankNorm per day: (percent rank - 0.5) * 3.46; rows without a label are dropped from training
  model    LightGBM, the `workflow_config_lightgbm_Alpha158` hyper-parameters unchanged, early stopping 50 on the validation rows
Label (the one change from the benchmark, to match "signal after the close, fill at the next open"):
  Ref($open,-2)/Ref($open,-1) - 1   = return from the next open to the open after it.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

LABEL_CFG = (["Ref($open, -2)/Ref($open, -1) - 1"], ["LABEL0"])
LGB_PARAMS = {"objective": "regression", "colsample_bytree": 0.8879, "learning_rate": 0.2, "subsample": 0.8789,
              "lambda_l1": 205.6999, "lambda_l2": 580.9768, "max_depth": 8, "num_leaves": 210, "num_threads": 8,
              "verbosity": -1, "seed": 0}
ROUNDS, EARLY = 1000, 50
CLIP = 3.0
MAD_SCALE = 1.4826


def raw_frame(start, end, instruments="sp500", with_label=True) -> pd.DataFrame:
    """Alpha158 raw features (+ column LABEL0), index (datetime, instrument), float32. Needs qlib_data.init_qlib() first."""
    from qlib.contrib.data.handler import Alpha158
    h = Alpha158(instruments=instruments, start_time=str(start)[:10], end_time=str(end)[:10], infer_processors=[],
                 learn_processors=[], label=LABEL_CFG)
    df = h.fetch(col_set=["feature", "label"])
    df.columns = [c[1] if isinstance(c, tuple) else c for c in df.columns]
    if not with_label:
        df = df.drop(columns=["LABEL0"])
    return df.astype("float32")


def days_of(df):
    return df.index.get_level_values(0)


def between(df, start, end):
    d = days_of(df)
    return df[(d >= pd.Timestamp(start)) & (d <= pd.Timestamp(end))]


@dataclass
class Norm:
    median: np.ndarray
    mad: np.ndarray

    @classmethod
    def fit(cls, X: np.ndarray) -> "Norm":
        med = np.nanmedian(X, axis=0)
        mad = np.nanmedian(np.abs(X - med), axis=0) * MAD_SCALE + 1e-12
        return cls(med.astype("float32"), mad.astype("float32"))

    def transform(self, X: np.ndarray) -> np.ndarray:
        Z = np.clip((X - self.median) / self.mad, -CLIP, CLIP)
        return np.nan_to_num(Z, nan=0.0, posinf=0.0, neginf=0.0).astype("float32")


def rank_norm(label: pd.Series) -> pd.Series:
    """qlib CSRankNorm: per day percent rank, centred, scaled to unit-ish variance."""
    r = label.groupby(level=0).rank(pct=True)
    return (r - 0.5) * 3.46


@dataclass
class Bundle:
    model_str: str
    norm: Norm
    columns: list
    meta: dict = field(default_factory=dict)
    _booster: object = None

    def booster(self):
        if self._booster is None:
            import lightgbm as lgb
            self._booster = lgb.Booster(model_str=self.model_str)
        return self._booster

    def predict(self, X: pd.DataFrame) -> pd.Series:
        Z = self.norm.transform(X[self.columns].to_numpy("float32"))
        return pd.Series(self.booster().predict(Z), index=X.index, name="score")

    def save(self, path):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            pickle.dump({"model_str": self.model_str, "norm": (self.norm.median, self.norm.mad), "columns": self.columns,
                         "meta": self.meta}, f)

    @classmethod
    def load(cls, path) -> "Bundle":
        with open(path, "rb") as f:
            d = pickle.load(f)
        return cls(d["model_str"], Norm(*d["norm"]), d["columns"], d["meta"])


def fit(frame: pd.DataFrame, train: tuple, valid: tuple, params=None, meta=None) -> Bundle:
    """Train on frame rows in `train` (start, end), early-stop on `valid`. Normalisation is fitted on the train rows only."""
    import lightgbm as lgb
    cols = [c for c in frame.columns if c != "LABEL0"]
    tr, va = between(frame, *train), between(frame, *valid)
    tr, va = tr[tr["LABEL0"].notna()], va[va["LABEL0"].notna()]
    norm = Norm.fit(tr[cols].to_numpy("float32"))
    Xtr, Xva = norm.transform(tr[cols].to_numpy("float32")), norm.transform(va[cols].to_numpy("float32"))
    ytr, yva = rank_norm(tr["LABEL0"]).to_numpy("float32"), rank_norm(va["LABEL0"]).to_numpy("float32")
    p = dict(LGB_PARAMS, **(params or {}))
    b = lgb.train(p, lgb.Dataset(Xtr, ytr), num_boost_round=ROUNDS, valid_sets=[lgb.Dataset(Xva, yva)],
                  callbacks=[lgb.early_stopping(EARLY, verbose=False)])
    m = {"train": list(train), "valid": list(valid), "rows": int(len(tr)), "best_iteration": int(b.best_iteration)}
    m.update(meta or {})
    return Bundle(b.model_to_string(), norm, cols, m, b)


def ic_by_day(score: pd.Series, label: pd.Series) -> pd.DataFrame:
    """Daily Pearson IC and Spearman rank IC between score and the (raw) label."""
    df = pd.concat({"s": score, "y": label}, axis=1).dropna()
    g = df.groupby(level=0)
    ic = g.apply(lambda d: d["s"].corr(d["y"]) if len(d) > 10 else np.nan)
    ric = g.apply(lambda d: d["s"].corr(d["y"], method="spearman") if len(d) > 10 else np.nan)
    return pd.DataFrame({"ic": ic, "rank_ic": ric}).dropna()
