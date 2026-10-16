import numpy as np
import pandas as pd
import pytest

from data import CTX_TICKERS
from stats import coin_flip
from strategies import STRATEGIES, positions

K = 1000


def synth(seed, n=1500, start="2015-01-01", freq="B"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    o = c * np.exp(rng.normal(0, 0.004, n))
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.01, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.01, n))
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=idx)


def synth_ctx(index, seed):
    """Positive random-walk context series (VIX-like levels, yields, ETF prices), one per ticker."""
    rng = np.random.default_rng(seed)
    n = len(index)
    cols = {t: 20 * np.exp(np.cumsum(rng.normal(0, 0.03, n))) for t in CTX_TICKERS}
    ctx = pd.DataFrame(cols, index=index)
    ctx.iloc[:50, list(ctx.columns).index("HYG")] = np.nan  # a series that starts late
    return ctx


def perturb(df, ctx, seed=99):
    """Replace everything after bar K (prices AND context) with a wild, unrelated future."""
    df2, ctx2 = df.copy(), ctx.copy()
    rng = np.random.default_rng(seed)
    m = len(df) - K - 1
    c = df.Close.values[K + 1:] * np.exp(np.cumsum(rng.normal(0, 0.05, m)))
    o = c * np.exp(rng.normal(0, 0.02, m))
    df2.iloc[K + 1:, df.columns.get_loc("Close")] = c
    df2.iloc[K + 1:, df.columns.get_loc("Open")] = o
    df2.iloc[K + 1:, df.columns.get_loc("High")] = np.maximum(o, c) * 1.03
    df2.iloc[K + 1:, df.columns.get_loc("Low")] = np.minimum(o, c) * 0.97
    ctx2.iloc[K + 1:] = ctx.iloc[K + 1:].values * np.exp(np.cumsum(rng.normal(0, 0.2, (m, ctx.shape[1])), axis=0))
    return df2, ctx2


@pytest.mark.parametrize("freq", ["B", "D"], ids=["stocks", "crypto"])
@pytest.mark.parametrize("st", STRATEGIES, ids=[s["id"] for s in STRATEGIES])
def test_no_lookahead(st, freq):
    df = synth(1, freq=freq)
    ctx = synth_ctx(df.index, 3)
    df2, ctx2 = perturb(df, ctx)
    for params in st["grid"]:
        full = positions(st, df, ctx, params)
        a = full.iloc[: K + 1].fillna(0).values
        b = positions(st, df2, ctx2, params).iloc[: K + 1].fillna(0).values
        np.testing.assert_array_equal(a, b, err_msg=f"{st['id']} {params} peeks at the future")
        # truncating the data at K must not change the decision at K either (no reliance on later rows)
        c = positions(st, df.iloc[: K + 1], ctx.iloc[: K + 1], params).fillna(0).values
        np.testing.assert_array_equal(a, c, err_msg=f"{st['id']} {params} depends on later rows existing")
        assert np.abs(full).max() <= 1.0 + 1e-12
        assert len(full) == len(df) and full.index.equals(df.index)


def test_grids_capped_and_ids_unique():
    assert all(len(s["grid"]) <= 12 for s in STRATEGIES if s["batch"] >= 1)
    ids = [s["id"] for s in STRATEGIES]
    assert len(ids) == len(set(ids))
    assert all(s.get("source") and "batch" in s for s in STRATEGIES)


def test_coinflip_pvalue_for_random_strategy_not_small():
    df = synth(7, 2000)
    o = df.Open.values
    rng = np.random.default_rng(5)
    ps = []
    for i in range(60):
        k = 25
        h = rng.integers(2, 15, k)
        free = len(o) - 1 - h.sum()
        cuts = np.sort(rng.integers(0, free + 1, k))
        ent = cuts + np.cumsum(h) - h
        d = rng.choice([1.0, -1.0], k)
        tr = {"entry": ent, "exit": ent + h, "dir": d}
        ps.append(coin_flip(o, tr, 3, n_sims=500, seed=i)["p_value"])
    ps = np.array(ps)
    assert 0.3 < ps.mean() < 0.7
    assert (ps < 0.05).mean() < 0.15


# ---------------------------------------------------------------- portfolio strategies
import portfolio as pfe  # noqa: E402
from strategies import PORTFOLIO_STRATEGIES  # noqa: E402


def synth_panel(seed, n_assets=12, n=1500, perturb_after=None):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2010-01-01", periods=n)
    cols = [f"A{i}" for i in range(n_assets)]
    O, H, L, C = ({} for _ in range(4))
    for i, c in enumerate(cols):
        lr = rng.normal(0.0003, 0.012, n)
        if perturb_after is not None:
            lr[perturb_after + 1:] = np.random.default_rng(1000 + i).normal(0, 0.06, n - perturb_after - 1)
        cl = 100 * np.exp(np.cumsum(lr))
        op = cl * np.exp(rng.normal(0, 0.004, n))
        if perturb_after is not None:
            op[perturb_after + 1:] = cl[perturb_after + 1:] * 1.01
        C[c], O[c] = cl, op
        H[c], L[c] = np.maximum(op, cl) * 1.004, np.minimum(op, cl) * 0.996
        if i == 3:  # one asset starts late
            for d in (O, H, L, C):
                d[c] = np.where(np.arange(n) < 300, np.nan, d[c])
    mk = lambda d: pd.DataFrame(d, index=idx)
    Cf = mk(C)
    return pfe.Panel(mk(O), mk(H), mk(L), Cf, Cf.notna())


def _cut(P, k):
    return pfe.Panel(*(x.iloc[: k + 1] for x in (P.O, P.H, P.L, P.C, P.started)))


@pytest.mark.parametrize("st", PORTFOLIO_STRATEGIES, ids=[s["id"] for s in PORTFOLIO_STRATEGIES])
def test_portfolio_no_lookahead(st):
    P = synth_panel(4)
    P2 = synth_panel(4, perturb_after=K)
    for params in st["grid"][:4]:
        a = pfe.finalize(st["fn"](P, **params), P).iloc[: K + 1].values
        b = pfe.finalize(st["fn"](P2, **params), P2).iloc[: K + 1].values
        np.testing.assert_allclose(a, b, err_msg=f"{st['id']} {params} peeks at the future")
        Pc = _cut(P, K)
        c = pfe.finalize(st["fn"](Pc, **params), Pc).values
        np.testing.assert_allclose(a, c, err_msg=f"{st['id']} {params} depends on later rows existing")
        assert np.all(a[:300, 3] == 0), "weight on an asset before it started"
        assert np.abs(a).sum(axis=1).max() <= 2.0 + 1e-9


def test_portfolio_coinflip_random_weights_not_small():
    P = synth_panel(9, n_assets=10, n=1200)
    rng = np.random.default_rng(3)
    ps = []
    for i in range(30):
        W = pd.DataFrame(0.0, index=P.index, columns=P.tickers)
        for p0 in range(0, len(P.index), 21):
            w = np.zeros(10)
            w[rng.choice([0, 1, 2, 4, 5, 6, 7, 8, 9], 3, replace=False)] = 1 / 3
            W.iloc[p0:p0 + 21] = w
        W = pfe.finalize(W, P)
        ps.append(pfe.coin_flip_portfolio(P, W, slice(None), 3, n_sims=300, seed=i)["p_value"])
    ps = np.array(ps)
    assert 0.25 < ps.mean() < 0.75 and (ps < 0.05).mean() < 0.2
