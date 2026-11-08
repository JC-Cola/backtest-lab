"""Study 10 (CRYPTO_PLAN_10.md): the weekend-entry rule on the trend filter and the stationary bootstrap helper."""
import numpy as np
import pandas as pd

import crypto10 as S


def frame(closes, start="2024-01-01"):
    """Daily bars (2024-01-01 is a Monday) with open = previous close."""
    c = np.asarray(closes, float)
    idx = pd.date_range(start, periods=len(c), freq="D")
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": c, "Low": c, "Close": c, "Volume": 1.0}, index=idx)


def test_entry_signalled_friday_or_saturday_is_blocked_then_taken_monday():
    # SMA(2): flat until the close rises above it. Mon..Sun = days 0..6, next Monday = day 7.
    # Close jumps on Friday (day 4): signal Fri close -> fill Saturday -> blocked. Still on Saturday/Sunday (fill Sun/Mon):
    # Sunday-close signal fills Monday, so the entry happens at the day-7 decision's position, i.e. s turns 1 on day 6.
    c = [10, 10, 10, 10, 20, 21, 22, 23, 24]
    df = frame(c)
    free = S.positions(df, 2, False)
    skip = S.positions(df, 2, True)
    assert free[4] == 1 and free[5] == 1 and free[6] == 1       # without the rule: decided Fri, filled Sat
    assert skip[4] == 0 and skip[5] == 0                        # Fri and Sat signals (Sat, Sun fills) are blocked
    assert skip[6] == 1 and skip[7] == 1                        # Sunday-close signal fills Monday


def test_blocked_entry_is_dropped_if_signal_turns_off():
    c = [10, 10, 10, 10, 20, 5, 5, 5, 5]                        # up on Friday, collapses Saturday
    skip = S.positions(frame(c), 2, True)
    assert skip.tolist() == [0] * 9


def test_held_position_is_kept_over_the_weekend_and_exits_on_a_weekend_day():
    # long from Wednesday (day 2: fill Thursday, a weekday), stays on through Fri/Sat; falls below SMA on Saturday (day 5)
    c = [10, 10, 20, 21, 22, 23, 5, 5, 5]
    free, skip = S.positions(frame(c), 2, False), S.positions(frame(c), 2, True)
    assert skip[2] == 1 and skip[3] == 1 and skip[4] == 1 and skip[5] == 1   # kept over Fri/Sat
    assert skip[6] == 0 and free[6] == 0                                      # exit is never delayed (signal Sun close)


def test_no_skip_flag_gives_plain_trend_filter():
    df = frame(np.cumsum(np.random.default_rng(1).normal(0, 1, 300)) + 100)
    import strategies_trend as tr
    assert (S.positions(df, 20, False) == tr.trend_filter(df, 20).values).all()


def test_bootstrap_detects_positive_mean_and_not_noise():
    rng = np.random.default_rng(0)
    pos = rng.normal(0.01, 0.01, 800)
    noise = rng.normal(0.0, 0.01, 800)
    assert S.stationary_bootstrap_p(pos, 2000, 20, 1) < 0.01
    assert S.stationary_bootstrap_p(noise, 2000, 20, 1) > 0.05
    assert S.stationary_bootstrap_p(-pos, 2000, 20, 1) > 0.9


def test_bootstrap_is_seeded_and_handles_all_zero_difference():
    d = np.random.default_rng(3).normal(0, 1, 200)
    assert S.stationary_bootstrap_p(d, 500, 20, 5) == S.stationary_bootstrap_p(d, 500, 20, 5)
    assert S.stationary_bootstrap_p(np.zeros(100), 500, 20, 0) == 1.0


def test_bootstrap_blocks_are_serially_dependent():
    # a series made of long runs: block resampling keeps the runs, so a wider spread than iid resampling would give
    d = np.repeat(np.random.default_rng(2).normal(0, 1, 40), 20)
    p_block = S.stationary_bootstrap_p(d + 0.15, 4000, 20, 1)
    p_iid = S.stationary_bootstrap_p(d + 0.15, 4000, 1, 1)
    assert p_block > p_iid


def test_binom_and_decision():
    assert abs(S.binom_p(8, 10) - 56 / 1024) < 1e-12
    comb = {"n150_c20": {"fisher_p": 0.01, "median_sharpe_diff": 0.1}, "n150_c50": {"fisher_p": 0.2, "median_sharpe_diff": 0.05}}
    assert S.decide(comb)["adopt"]
    comb["n150_c50"]["median_sharpe_diff"] = -0.01
    assert not S.decide(comb)["adopt"]
