import numpy as np
import pytest

from engine import count_trades, extract_trades, returns_np

O = np.array([100.0, 102, 101, 105, 104])
C = np.array([101.0, 100, 104, 103, 106])
S = np.array([1.0, 1, 0, -1, 0])
COST = 10  # bps
c = COST / 1e4


def test_returns_split_and_costs():
    r = returns_np(O, C, S, COST)
    exp = [
        0.0,
        100 / 102 - 1 - c,  # enter at open1, intraday only
        (101 / 100) * (104 / 101) - 1,  # overnight + intraday, no trade
        105 / 104 - 1 - c,  # overnight held, exit at open3
        -(106 / 104 - 1) - c,  # short entered at open4 (s3=-1)
    ]
    np.testing.assert_allclose(r, exp, atol=1e-12)


def test_buy_hold_matches_open_close_math():
    r = returns_np(O, C, np.ones(5), 0)
    # s0=1 trades at open1: day1 intraday only, from day2 full close-to-close
    exp = [0.0, C[1] / O[1] - 1] + [C[d] / C[d - 1] - 1 for d in (2, 3, 4)]
    np.testing.assert_allclose(r, exp, atol=1e-12)
    np.testing.assert_allclose(np.prod(1 + r), C[4] / O[1], atol=1e-12)


def test_trades():
    t = extract_trades(O, C, S, COST)
    assert count_trades(S) == 2 and len(t["ret"]) == 2
    assert (t["entry"][0], t["exit"][0], t["dir"][0]) == (1, 3, 1)
    long_ret = (100 / 102) * (1.01) * (104 / 101) * (105 / 104) - 1 - 2 * c
    assert t["ret"][0] == pytest.approx(long_ret)
    assert t["dir"][1] == -1 and t["hold"][0] == 2


def test_position_flat_gives_zero():
    assert not returns_np(O, C, np.zeros(5), 5).any()
