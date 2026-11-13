"""Alphalens-style factor evaluation (ML4T): information coefficient, quantile returns, turnover, IC decay, and the System C
rating diagnostic (IC of TradingAgents' ratings vs forward returns, significance sample size). Pure numpy/pandas.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

RATING = {"Buy": 2.0, "Overweight": 1.0, "Hold": 0.0, "Underweight": -1.0, "Sell": -2.0}
ND = NormalDist()


def rating_score(r) -> float:
    return RATING[str(r).strip().title()]


def spearman(a, b) -> float:
    a, b = pd.Series(np.asarray(a, float)), pd.Series(np.asarray(b, float))
    ok = a.notna() & b.notna()
    if ok.sum() < 3:
        return float("nan")
    ra, rb = a[ok].rank(), b[ok].rank()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def pearson(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = ~(np.isnan(a) | np.isnan(b))
    if ok.sum() < 3 or a[ok].std() == 0 or b[ok].std() == 0:
        return float("nan")
    return float(np.corrcoef(a[ok], b[ok])[0, 1])


def required_n(ic, alpha=0.05, power=0.80) -> float:
    """Sample size for a one-sided test of IC > 0 at `alpha` with `power`: ((z_a + z_b)/atanh(IC))^2 + 3 (Fisher z)."""
    if not ic or math.isnan(ic) or abs(ic) < 1e-9:
        return float("inf")
    return ((ND.inv_cdf(1 - alpha) + ND.inv_cdf(power)) / math.atanh(abs(ic))) ** 2 + 3


def permutation_p(score, ret, n=10000, seed=0) -> float:
    """One-sided p that the Spearman IC is this high by chance (shuffle the returns)."""
    s, r = np.asarray(score, float), np.asarray(ret, float)
    ok = ~(np.isnan(s) | np.isnan(r))
    s, r = s[ok], r[ok]
    if len(s) < 4:
        return float("nan")
    obs = spearman(s, r)
    if math.isnan(obs):
        return float("nan")
    rng = np.random.default_rng(seed)
    rs = pd.Series(r).rank().values
    ss = pd.Series(s).rank().values
    cnt = 0
    for _ in range(n):
        cnt += np.corrcoef(ss, rng.permutation(rs))[0, 1] >= obs - 1e-12
    return (1 + cnt) / (n + 1)


# ------------------------------------------------------------------ System C diagnostic
def forward_returns(prices: pd.DataFrame, symbol, date, h, mode="next_open"):
    """prices: daily frame with Open/Close indexed by date. next_open: open of the next session after `date` to the close of
    the h-th session after `date` (the system's own fill rule); close_close: close(date) to close(date + h sessions).
    NaN when the horizon is not yet realised."""
    idx = prices.index
    i = idx.searchsorted(pd.Timestamp(date), side="right") - 1
    if i < 0:
        return float("nan")
    if mode == "next_open":
        j0, j1 = i + 1, i + h
        if j1 >= len(idx) or j0 >= len(idx):
            return float("nan")
        return float(prices["Close"].iloc[j1] / prices["Open"].iloc[j0] - 1)
    j1 = i + h
    return float("nan") if j1 >= len(idx) else float(prices["Close"].iloc[j1] / prices["Close"].iloc[i] - 1)


def binom_p_one_sided(k, n, p=0.5) -> float:
    return float(sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))) if n else float("nan")


def rating_ic_table(decisions, price_for, horizons=(1, 5, 20), modes=("next_open", "close_close"), n_perm=10000):
    """decisions: [{date, symbol, rating, clean(bool)}]; price_for(symbol) -> daily frame. Returns {set: {mode: {h: stats}}}."""
    rows = []
    for d in decisions:
        px = price_for(d["symbol"])
        rec = {"date": d["date"], "symbol": d["symbol"], "score": rating_score(d["rating"]), "clean": bool(d.get("clean", True))}
        for m in modes:
            for h in horizons:
                rec[f"{m}_{h}"] = forward_returns(px, d["symbol"], d["date"], h, m)
        rows.append(rec)
    df = pd.DataFrame(rows)
    out = {"n_decisions": len(df)}
    sets = {"all": df, "clean": df[df["clean"]], "possibly_contaminated": df[~df["clean"]]}
    for name, sub in sets.items():
        out[name] = {}
        for m in modes:
            out[name][m] = {}
            for h in horizons:
                col = sub[f"{m}_{h}"]
                ok = sub[col.notna()]
                n = len(ok)
                st = {"n": int(n), "n_dates": int(ok["date"].nunique()), "n_unrealised": int(len(sub) - n)}
                if n >= 4:
                    ic = spearman(ok["score"], ok[f"{m}_{h}"])
                    nh = ok[ok["score"] != 0]
                    hits = int(((np.sign(nh["score"]) == np.sign(nh[f"{m}_{h}"]))).sum())
                    st |= {"spearman_ic": ic, "pearson_ic": pearson(ok["score"], ok[f"{m}_{h}"]),
                           "perm_p_one_sided": permutation_p(ok["score"], ok[f"{m}_{h}"], n_perm),
                           "non_hold": int(len(nh)), "hit_rate_non_hold": hits / len(nh) if len(nh) else None,
                           "hit_binom_p": binom_p_one_sided(hits, len(nh)),
                           "mean_fwd_by_rating": {k: float(v) for k, v in ok.groupby("score")[f"{m}_{h}"].mean().items()},
                           "n_needed_at_observed_ic": required_n(ic)}
                st["n_needed_at_ic"] = {str(x): required_n(x) for x in (0.02, 0.05, 0.10)}
                out[name][m][str(h)] = st
    out["rows"] = df.assign(date=df["date"].astype(str)).to_dict("records")
    return out


# ------------------------------------------------------------------ factor evaluation (qlib predictions)
def daily_ic(scores: pd.DataFrame, fwd: pd.DataFrame) -> pd.Series:
    """Cross-sectional Spearman rank IC per date. scores, fwd: date x instrument."""
    common = scores.index.intersection(fwd.index)
    out = {}
    for d in common:
        s, f = scores.loc[d], fwd.loc[d].reindex(scores.columns)
        ok = s.notna() & f.notna()
        if ok.sum() >= 5:
            out[d] = spearman(s[ok], f[ok])
    return pd.Series(out, dtype=float)


def ic_summary(ic: pd.Series) -> dict:
    ic = ic.dropna()
    if len(ic) < 2:
        return {"days": int(len(ic))}
    m, sd = float(ic.mean()), float(ic.std(ddof=1))
    return {"days": int(len(ic)), "mean_ic": m, "std_ic": sd, "icir": m / sd if sd > 0 else None,
            "t_stat": m / sd * math.sqrt(len(ic)) if sd > 0 else None, "share_positive": float((ic > 0).mean())}


def quantile_returns(scores: pd.DataFrame, fwd: pd.DataFrame, q=5):
    """Mean forward return of each score quantile (1 = lowest) per date, then averaged over dates."""
    rows = []
    for d in scores.index.intersection(fwd.index):
        s, f = scores.loc[d], fwd.loc[d].reindex(scores.columns)
        ok = s.notna() & f.notna()
        if ok.sum() < q * 2:
            continue
        b = pd.qcut(s[ok].rank(method="first"), q, labels=False) + 1
        rows.append(f[ok].groupby(b).mean())
    if not rows:
        return {}
    m = pd.DataFrame(rows).mean()
    return {"mean_fwd_by_quantile": {int(k): float(v) for k, v in m.items()}, "top_minus_bottom": float(m.iloc[-1] - m.iloc[0]),
            "monotonic_share": float(np.mean(np.diff(m.values) > 0))}


def top_quantile_turnover(scores: pd.DataFrame, q=5, bottom=False) -> float:
    """Mean share of the top (or bottom) quantile's names replaced from one date to the next."""
    prev, vals = None, []
    for d in scores.index:
        s = scores.loc[d].dropna()
        if len(s) < q * 2:
            continue
        b = pd.qcut(s.rank(method="first"), q, labels=False)
        cur = set(s.index[b == (0 if bottom else q - 1)])
        if prev is not None and cur:
            vals.append(1 - len(cur & prev) / len(cur))
        prev = cur
    return float(np.mean(vals)) if vals else float("nan")


def ic_decay(scores: pd.DataFrame, close: pd.DataFrame, lags=range(1, 11)) -> dict:
    """IC of the score at t against the 1-day close-to-close return from t+k-1 to t+k, k = 1..max."""
    ret1 = close.pct_change(fill_method=None)
    out = {}
    for k in lags:
        fwd = ret1.shift(-k)
        out[int(k)] = ic_summary(daily_ic(scores, fwd)).get("mean_ic")
    return out


def factor_report(scores: pd.DataFrame, close: pd.DataFrame, q=5, lags=range(1, 11)) -> dict:
    """The (c) readout. scores: date x instrument (known at that date's close); close: same shape, daily closes."""
    fwd1 = close.pct_change(fill_method=None).shift(-1)
    ic = daily_ic(scores, fwd1)
    return {"dates": [str(scores.index.min().date()), str(scores.index.max().date())], "n_instruments": int(scores.shape[1]),
            "ic": ic_summary(ic), "quantiles": quantile_returns(scores, fwd1, q),
            "top_quantile_turnover": top_quantile_turnover(scores, q), "bottom_quantile_turnover": top_quantile_turnover(scores, q, True),
            "ic_decay": ic_decay(scores, close, lags)}
