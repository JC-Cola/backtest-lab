"""vectorbt noise-band implementation (src/vbt_noise.py) on small synthetic data."""
import numpy as np
import pandas as pd
import pytest

import intraday as I
import vbt_noise as V

LB = 2


def build(paths, n_prev=3, prev=None):
    """Sessions: n_prev quiet sessions (price 100 all day unless `prev` = (price after minute 0) is given) + one session per
    entry of `paths` (full-length minute price arrays; bar open = previous close, so O[d,0] = 100)."""
    rows = []
    for _ in range(n_prev):
        rows.append(np.full(390, 100.0 if prev is None else prev))
        if prev is not None:
            rows[-1][0] = 100.0
    rows += [np.asarray(p, float) for p in paths]
    C = np.array(rows)
    O = np.c_[np.full((len(C), 1), 100.0), C[:, :-1]]
    H, L = np.maximum(O, C), np.minimum(O, C)
    V_ = np.ones_like(C)
    return I.Days.from_arrays(pd.bdate_range("2021-03-01", periods=len(C)), O, H, L, C, V_, np.full(len(C), 389))


def path(**steps):
    """100 until minute 59, then piecewise: step=(from_minute, price)."""
    p = np.full(390, 100.0)
    for m, x in steps.values():
        p[m:] = x
    return p


UP = path(a=(60, 101.0), b=(120, 102.0))            # breaks up at the 10:59 bar, keeps rising
DOWN = path(a=(60, 99.0), b=(120, 98.0))
P = dict(lookback=LB, check=60, band=1.0, vwap=True)


def run(D, grid=None, **kw):
    g = grid or V.compact_grid(D)
    pf, R, tg = V.run(D, g, [{**P, **kw}] if "params" not in kw else kw["params"])
    return g, pf, R[:, 0], tg[:, 0]


def test_long_signal_fills_at_next_bar_open_and_exits_at_close():
    D = build([UP])
    g, pf, R, tg = run(D)
    d = D.n - 1
    pos = {int(k): t for k, t, dd in zip(g.k, tg, g.day) if dd == d}
    assert pos[75] == 0 and pos[30] == 0                 # 10:00 decision (bar 29) saw 100 <= bands: flat
    assert pos[90] == 1 and pos[375] == 1                # decision on bar 89's close (10:59) -> long from the bar-90 open
    assert pos[390] == 0                                  # flat at the close
    entry, exit_ = UP[89], UP[389]                         # bar 90 opens at bar 89's close
    assert D.P[d, 90] == pytest.approx(entry)
    expect = V.WEIGHT * ((exit_ / entry - 1) - 2 * V.COST)
    assert R[d] == pytest.approx(expect, abs=2e-6)
    assert (R[:d] == 0).all()


def test_short_signal():
    D = build([DOWN])
    g, pf, R, tg = run(D)
    d = D.n - 1
    pos = {int(k): t for k, t, dd in zip(g.k, tg, g.day) if dd == d}
    assert pos[90] == -1 and pos[390] == 0
    assert R[d] == pytest.approx(V.WEIGHT * (-(DOWN[389] / DOWN[89] - 1) - 2 * V.COST), abs=2e-6)


def test_no_signal_means_no_orders_and_no_cost():
    D = build([np.full(390, 100.0)])
    g, pf, R, tg = run(D)
    assert (tg == 0).all() and (R == 0).all()
    assert pf.orders.count().sum() == 0


def test_warmup_sessions_are_flat():
    D = build([UP, UP, UP])
    g, pf, R, tg = run(D)
    assert (tg[g.day < LB + 1] == 0).all()               # engine: sessions d <= lookback never trade (sigma needs lookback days)
    assert (tg[(g.day == LB + 1) & (g.k == 90)] == 1).all()


def test_costs_scale_with_fee():
    D = build([UP])
    g = V.compact_grid(D)
    _, R0, _ = V.run(D, g, [P], cost=0.0)
    _, R1, _ = V.run(D, g, [P], cost=1e-3)
    assert R0[-1, 0] - R1[-1, 0] == pytest.approx(V.WEIGHT * 2 * 1e-3, rel=0.02)   # one round trip, both sides


def test_flip_pays_both_legs():
    # long from 10:59, short from 11:59 (price falls under the lower band and VWAP), flat at the close
    p = path(a=(60, 101.0), b=(120, 98.0))
    D = build([p])
    g, pf, R, tg = run(D)
    d = D.n - 1
    pos = {int(k): t for k, t, dd in zip(g.k, tg, g.day) if dd == d}
    assert pos[90] == 1 and pos[150] == -1 and pos[390] == 0
    r1 = p[149] / p[89] - 1 - 2 * V.COST
    r2 = -(p[389] / p[149] - 1) - 2 * V.COST
    assert R[d] == pytest.approx(V.WEIGHT * (r1 + r2), abs=5e-6)
    assert pf.orders.count().sum() == 3                   # buy, sell 2x (reverse), buy back


def test_vwap_filter():
    # early spike lifts VWAP; at 10:59 price is above the upper band (100) but below VWAP
    p = np.full(390, 100.0)
    p[30:60] = 110.0
    p[60:] = 101.0
    D = build([p])
    on = run(D, vwap=True)[3]
    off = run(D, vwap=False)[3]
    d = D.n - 1
    day = D.n - 1
    g = V.compact_grid(D)
    sel = (g.day == day) & (g.k == 90)
    assert on[sel][0] == 0 and off[sel][0] == 1


def test_band_multiplier_widens_bands():
    # previous sessions move +0.5%: sigma 0.005, bands 100.5*(1 +/- band*0.005); price 101.5 clears band 1.0 but not 2.0
    p = np.full(390, 100.0)
    p[60:] = 101.5
    D = build([p], prev=100.5)
    g = V.compact_grid(D)
    sel = (g.day == D.n - 1) & (g.k == 90)
    assert run(D, band=1.0)[3][sel][0] == 1
    assert run(D, band=2.0)[3][sel][0] == 0


def test_check_interval_decision_minutes():
    D = build([UP])
    g = V.compact_grid(D)
    for check, first_fill in ((30, 90), (60, 90), (90, 120), (120, 150)):
        tg = run(D, check=check)[3]
        sel = g.day == D.n - 1
        first = g.k[sel][np.flatnonzero(tg[sel] != 0)[0]]
        # decision bars 29 + check*i; UP is above the band from bar 60: first decision at or after bar 60 fills one bar later
        want = min(29 + check * i + 1 for i in range(20) if 29 + check * i >= 60)
        assert first == want, (check, first, want)


def synth(n=40, seed=3):
    rng = np.random.default_rng(seed)
    C = 100 * np.exp(np.cumsum(rng.normal(0, 0.0012, (n, 390)), axis=1) + np.cumsum(rng.normal(0, 0.01, n))[:, None])
    O = np.c_[C[:, :1] * np.exp(rng.normal(0, 0.003, (n, 1))), C[:, :-1]]
    Vv = rng.integers(1000, 5000, (n, 390)).astype(float)
    H, L = np.maximum(O, C) * 1.0003, np.minimum(O, C) * 0.9997
    last = np.full(n, 389)
    last[10] = 209                                       # half day
    for d in (10,):
        C[d, 210:], O[d, 210:], H[d, 210:], L[d, 210:], Vv[d, 210:] = C[d, 209], C[d, 209], C[d, 209], C[d, 209], 0
    return I.Days.from_arrays(pd.bdate_range("2020-01-02", periods=n), O, H, L, C, Vv, last)


@pytest.mark.parametrize("lb,check", [(3, 60), (5, 30), (4, 90), (6, 120), (3, 15)])
def test_matches_existing_engine_daily_returns(lb, check):
    D = synth()
    t = I.noise_breakout(D, lb, check)
    comp = np.ones(D.n)
    for d, x in zip(t["day"].astype(int), t["ret"]):
        comp[d] *= 1 + V.WEIGHT * x                      # engine trades, each re-sized at current equity
    comp -= 1
    for g in (V.compact_grid(D), V.full_grid(D)):
        pf, R, tg = V.run(D, g, [dict(lookback=lb, check=check, band=1.0, vwap=True)])
        assert np.abs(R[:, 0] - comp).max() < 1e-5
        assert V.n_trades(tg, g)[0] == len(t["ret"])


def test_flat_at_every_close_and_batch_equals_single():
    D = synth()
    g = V.compact_grid(D)
    params = [dict(lookback=l, check=c, band=b, vwap=v) for l in (3, 5) for c in (30, 60) for b in (0.8, 1.2) for v in (True, False)]
    pf, R, tg = V.run(D, g, params)
    assert (tg[g.is_close] == 0).all()
    j = 5
    _, R1, _ = V.run(D, g, [params[j]])
    assert np.allclose(R[:, j], R1[:, 0], atol=1e-12)
