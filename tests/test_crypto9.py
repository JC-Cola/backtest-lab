"""Study 9 (CRYPTO_PLAN_9.md): no lookahead, fee accounting, the Donchian trailing stop, rotation picks, filters."""
import numpy as np
import pandas as pd
import pytest

import crypto9_base as B
import crypto9_rot as R
from engine import returns_np


def synth_df(seed, n=900, start="2019-01-01", freq="D", drift=0.0005, vol=0.03):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, 0.002, n))
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.01, "Low": np.minimum(o, c) * 0.99, "Close": c,
                         "Volume": 1.0}, index=idx)


def perturb(df, K, seed=7):
    """Replace every bar after K by a wild unrelated future."""
    d = df.copy()
    rng = np.random.default_rng(seed)
    m = len(df) - K - 1
    c = df.Close.values[K + 1:] * np.exp(np.cumsum(rng.normal(0, 0.15, m)))
    d.iloc[K + 1:, d.columns.get_loc("Close")] = c
    d.iloc[K + 1:, d.columns.get_loc("Open")] = c * np.exp(rng.normal(0, 0.05, m))
    return d


K = 600


# ------------------------------------------------------------------------------------------ Donchian trailing stop

def test_donchian_leg_entry_exit_and_flat_warmup():
    c = [100, 100, 100, 110, 120, 130, 125, 100, 100, 100]
    on = B.donchian_leg(c, 3)
    # flat for t < N; enters on the first close above the prior 3-day high; the stop ratchets 105 -> 110 -> 120 -> 125;
    # 125 >= 120 holds at t=6; the close of 100 is below 125 and exits at t=7
    assert on.tolist() == [False, False, False, True, True, True, True, False, False, False]


def test_donchian_stop_only_moves_up_and_no_reentry_on_exit_day():
    rng = np.random.default_rng(3)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, 700)))
    N = 20
    on = B.donchian_leg(c, N)
    stop, state = 0.0, False
    for t in range(N, len(c)):
        w = c[t - N + 1: t + 1]
        mid = (w.max() + w.min()) / 2
        if state:
            if c[t] < stop:
                state = False
                assert not on[t]                      # exits on the close below the stop...
            else:
                stop = max(stop, mid)                 # ...which never decreased
        elif c[t] > c[t - N: t].max():
            state, stop = True, mid
        assert on[t] == state


def test_newhigh_variants():
    c = np.array([5, 4, 3, 6, 7, 5, 4, 8, 6.0])
    # N=3: close equal to the max of the last 3 closes (today included)
    assert B.newhigh_1d(c, 3).tolist() == [0, 0, 0, 1, 1, 0, 0, 1, 0]
    lv = B.newhigh_level(c, 3)
    # enters at t=3 (6 is a 3-day high); L0 = max of the 3 closes before = 5; holds while close >= 5; 7 stays;
    # t=5 (5) is still >= 5; t=6 (4) < 5 exits; t=7 (8) re-enters, L0 = max(5,4,... closes 4..6) = 7; t=8 (6) < 7 exits
    assert lv.tolist() == [0, 0, 0, 1, 1, 1, 0, 1, 0]


# ------------------------------------------------------------------------------------------ no lookahead

@pytest.mark.parametrize("name,kw", [
    ("pos_donchian", {"target": 0.5, "legs": "all"}), ("pos_donchian", {"target": 0.25, "legs": "slow"}),
    ("pos_trend", {"n": 20}), ("pos_trend", {"n": 100}),
    ("pos_newhigh", {"N": 10, "hold": "1d"}), ("pos_newhigh", {"N": 20, "hold": "level"}),
    ("pos_trend", {"n": 50, "weekend": True}), ("pos_donchian", {"target": 0.5, "legs": "all", "weekend": True})])
def test_positions_up_to_t_do_not_depend_on_the_future(name, kw):
    df = synth_df(1)
    fn = getattr(B, name)
    a = fn(B.Coin("X", df), **kw)
    b = fn(B.Coin("X", perturb(df, K)), **kw)
    assert np.array_equal(a[: K + 1], b[: K + 1])
    assert not np.array_equal(a[K + 1:], b[K + 1:])    # and the perturbation really changes the later positions


def test_funding_filter_no_lookahead_and_inactive_before_a_year():
    idx = pd.date_range("2020-01-01", periods=800 * 3, freq="8h")
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(1e-4, 5e-5, len(idx)), index=idx)
    dates = pd.date_range("2020-01-01", periods=800, freq="D")
    f = B.funding_flag(dates, r)
    assert not f[:370].any()                             # 7-day mean + 365 days of it must exist first
    assert B.funding_active_from(dates, r) == str(dates[6 + 364].date())
    r2 = r.copy()
    r2[r2.index >= "2021-10-01"] += 5e-3                 # a very different future
    f2 = B.funding_flag(dates, r2)
    cut = int((pd.Timestamp("2021-10-01") - dates[0]).days)
    assert np.array_equal(f[:cut], f2[:cut])
    assert f2[cut + 8:cut + 60].any()                    # and a funding spike is flagged once it is in the 7-day mean
    assert not B.funding_flag(dates, r.iloc[:-30])[-20:].any()   # no data after the last download: inactive


def test_rotation_weights_up_to_t_do_not_depend_on_the_future():
    dfs = {"A": synth_df(1, drift=0.002), "B": synth_df(2), "C": synth_df(3, drift=-0.001)}
    P1 = R.Panel(dfs) if hasattr(R, "Panel") else None
    P2 = R.Panel({k: perturb(v, K) for k, v in dfs.items()})
    for L, k in ((1, 1), (2, 2), (4, 3)):
        a, b = R.rotation_weights(P1, L, k), R.rotation_weights(P2, L, k)
        assert np.array_equal(a[: K + 1], b[: K + 1])


def test_hourly_macd_control_no_lookahead_and_red_candle_exit():
    h = synth_df(5, n=24 * 120, start="2022-01-01", freq="h", vol=0.01)
    d = h.Close.resample("D").last()
    s = R.ctl_macd_positions(h, d)
    assert s.sum() > 0
    o, c = h.Open.values, h.Close.values
    t = 1
    while t < len(s):                                    # every run of 1s: green bars inside, a red bar ends it
        if s[t] == 1 and s[t - 1] == 0:
            e = t
            while e + 1 < len(s) and s[e + 1] == 1:
                e += 1
                assert c[e] >= o[e]
            if e + 1 < len(s):
                assert c[e + 1] < o[e + 1]
            t = e + 1
        t += 1
    # perturb the daily closes from day D on: hourly positions through the end of day D are unchanged
    D = 70
    d2 = d.copy()
    d2.iloc[D:] = d.iloc[D:].values * np.exp(np.random.default_rng(1).normal(0, 0.2, len(d) - D))
    s2 = R.ctl_macd_positions(h, d2)
    end = (D + 1) * 24
    assert np.array_equal(s[:end], s2[:end])
    # perturb hourly bars after hour T: positions through T unchanged
    T = 24 * 60
    s3 = R.ctl_macd_positions(perturb(h, T), d)
    assert np.array_equal(s[: T + 1], s3[: T + 1])


# ------------------------------------------------------------------------------------------ costs

def test_round_trip_costs_two_sides_and_scale_with_size():
    o = c = np.full(10, 100.0)
    s = np.array([0, 0, 1, 1, 1, 0, 0, 0, 0, 0.0])
    for bps in (20.0, 50.0):
        assert np.isclose(returns_np(o, c, s, bps).sum(), -2 * bps / 1e4)
        assert np.isclose(returns_np(o, c, 0.5 * s, bps).sum(), -bps / 1e4)       # half size, half the fee


def test_fills_at_the_next_open_not_the_signal_close():
    o = np.array([100, 100, 110, 110, 110.0])
    c = np.array([100, 100, 110, 110, 110.0])
    s = np.array([0, 1, 1, 1, 1.0])            # decided on close 1 (100), filled at open 2 (110): the 10% gap is missed
    assert np.isclose(returns_np(o, c, s, 0.0).sum(), 0.0)


def test_hour_control_pays_two_sides_every_day_and_holds_two_bars():
    idx = pd.date_range("2022-01-01", periods=24 * 10, freq="h")
    s = R.ctl_hour_positions(idx)
    o = c = np.full(len(idx), 100.0)
    assert np.isclose(returns_np(o, c, s, 20.0).sum(), -10 * 2 * 0.002)
    held = idx[np.flatnonzero(s) + 1]                       # filled at the next open: bars starting 21:00 and 22:00
    assert set(held.hour) == {21, 22}
    # gain 1% in the 21:00 bar and 2% in the 22:00 bar only; flat otherwise, zero fee: the control captures exactly those
    o2 = np.full(len(idx), 100.0)
    c2 = o2.copy()
    for i in np.flatnonzero(idx.hour == 21):
        c2[i] = 101.0
    for i in np.flatnonzero(idx.hour == 22):
        o2[i], c2[i] = 101.0, 101.0 * 1.02
    o2[idx.hour == 23] = 101.0 * 1.02
    c2[idx.hour == 23] = 101.0 * 1.02
    o2[idx.hour == 21] = 100.0
    r = returns_np(o2, c2, s, 0.0)
    assert np.isclose(np.prod(1 + r), (1.01 * 1.02) ** 10, rtol=1e-6)


def test_executed_positions_charge_fees_on_the_size_that_changed():
    df = synth_df(11, n=600)
    coin = B.Coin("X", df)
    s = B.pos_donchian(coin, 0.5, "all")
    r0, r20 = B.run_ret(coin, s, slice(0, coin.n), 0.0), B.run_ret(coin, s, slice(0, coin.n), 20.0)
    turnover = np.abs(np.diff(s, prepend=0.0)).sum()
    assert np.isclose((r0 - r20).sum(), turnover * 0.002, rtol=1e-6)
    assert B.perf(r20, s, B.extract_trades(coin.o, coin.c, s, 20.0))["turnover_per_year"] > 0


# ------------------------------------------------------------------------------------------ execution rules

def test_band_and_weekend_rules():
    wd = np.zeros(4, int)
    assert B.execute(np.array([0.5, 0.55, 0.7, 0.0]), wd, 0.10).tolist() == [0.5, 0.5, 0.7, 0.0]
    assert B.execute(np.array([0, 1, 1, 1.0]), np.array([4, 5, 6, 0]), 0.0, weekend=True).tolist() == [0, 0, 0, 1]
    assert B.execute(np.array([1, 1, 0.0]), np.array([4, 5, 6]), 0.0, weekend=True).tolist() == [1, 1, 0]  # exits never wait
    assert B.execute(np.array([0, 1, 1, 1.0]), np.array([4, 5, 6, 0]), 0.0, weekend=False).tolist() == [0, 1, 1, 1]


# ------------------------------------------------------------------------------------------ rotation picks

def _panel(jump_b=False, n=330):
    idx = pd.date_range(end="2021-12-26", periods=n, freq="D")        # the last day is a Sunday
    a = 100 * 1.01 ** np.arange(n)
    b = 100 * 1.005 ** np.arange(n)
    if jump_b:
        b[-7:] = b[-8] * 1.02 ** np.arange(1, 8)
    cc = 100 * 0.995 ** np.arange(n)
    mk = lambda c: pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1.0}, index=idx)
    return R.Panel({"A": mk(a), "B": mk(b), "C": mk(cc)})


def test_rotation_picks_top_k_and_sends_failed_trend_slots_to_cash():
    P = _panel()
    t = P.n - 1
    assert P.dates[t].dayofweek == 6
    w1 = R.rotation_weights(P, 1, 1)[t]
    w2 = R.rotation_weights(P, 1, 2)[t]
    w3 = R.rotation_weights(P, 1, 3)[t]
    assert w1.tolist() == [1.0, 0.0, 0.0]
    assert w2.tolist() == [0.5, 0.5, 0.0]
    assert np.allclose(w3, [1 / 3, 1 / 3, 0.0])          # the third slot is C, below its SMA(50): cash, not redistributed
    assert R.rotation_weights(P, 1, 3)[t - 3].tolist() == R.rotation_weights(P, 1, 3)[t - 6].tolist()   # held all week


def test_rotation_look_back_changes_the_pick():
    P = _panel(jump_b=True)
    t = P.n - 1
    assert np.argmax(R.rotation_weights(P, 1, 1)[t]) == 1       # B had the best last week
    assert np.argmax(R.rotation_weights(P, 4, 1)[t]) == 0       # A still has the best 4-week return


def test_rotation_ignores_a_coin_without_a_full_look_back():
    n = 330
    idx = pd.date_range(end="2021-12-26", periods=n, freq="D")
    mk = lambda c, ix: pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1.0}, index=ix)
    a = 100 * 1.001 ** np.arange(n)
    new = 1.0 * 1.05 ** np.arange(10)                           # listed 10 days ago, rocketing, but too young for 2 weeks / SMA50
    P = R.Panel({"A": mk(a, idx), "N": mk(new, idx[-10:])})
    assert R.rotation_weights(P, 2, 1)[P.n - 1].tolist() == [1.0, 0.0]


def test_rotation_fees_charged_on_every_swap():
    n = 40
    idx = pd.date_range("2022-01-03", periods=n, freq="D")
    flat = pd.DataFrame({"Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 1.0}, index=idx)
    P = R.Panel({"A": flat, "B": flat})
    W = np.zeros((n, 2))
    W[3:10, 0] = 1.0        # in A, then out of A and into B, then out of B
    W[10:20, 1] = 1.0
    r = R.rot_returns(P, W, 20.0, slice(0, n))
    assert np.isclose(r.sum(), -4 * 0.002)               # A in, A out, B in, B out


def test_rotation_coin_flip_rewards_a_real_edge(monkeypatch):
    monkeypatch.setattr(R, "N_SIMS", 400)
    n = 500
    idx = pd.date_range(end="2023-06-04", periods=n, freq="D")                # ends on a Sunday
    rng = np.random.default_rng(2)
    mk = lambda c: pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1.0}, index=idx)
    star = 100 * 1.02 ** np.arange(n)
    others = [100 * np.exp(np.cumsum(rng.normal(0, 0.02, n))) for _ in range(2)]
    P = R.Panel({"S": mk(star), "X": mk(others[0]), "Y": mk(others[1])})
    W = R.rotation_weights(P, 1, 1)
    cf = R.rotation_coin_flip(P, W, 1, 1, 123)
    assert cf["p_value"] < 0.05
    cf2 = R.rotation_coin_flip(P, W, 1, 1, 123)
    assert cf == cf2                                               # seeded, repeatable
