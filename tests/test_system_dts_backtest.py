"""System B backtest helpers: metrics, gate modes, no-lookahead window, coin-flip benchmark (synthetic bars)."""
import datetime as dt
import json

import numpy as np
import pytest

import system_dts_backtest as B


def fake_arr(n_days=70, seed=0):
    rng = np.random.default_rng(seed)
    d0, days, rows = dt.date(2025, 1, 2), {}, []
    t = []
    px, i, day = 100.0, 0, d0
    while len(days) < n_days:
        if day.weekday() < 5:
            days[day.toordinal()] = [i, i + 78]
            for j in range(78):
                o = px
                px = px * (1 + rng.normal(0, 0.001))
                rows.append((o, max(o, px) * 1.0005, min(o, px) * 0.9995, px))
                t.append(int(dt.datetime.combine(day, dt.time(14, 30)).timestamp()) + 300 * j)
                i += 1
        day += dt.timedelta(days=1)
    a = np.array(rows)
    return {"t": np.array(t), "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "v": np.full(len(a), 1e5), "days": days}


def test_metrics_table():
    trades = [{"date": "2025-01-02", "pnl": 100.0, "r": 1.0, "entry_time": "2025-01-02 10:15", "exit_time": "2025-01-02 11:15", "reason": "target"},
              {"date": "2025-01-03", "pnl": -50.0, "r": -0.5, "entry_time": "2025-01-03 10:15", "exit_time": "2025-01-03 10:45", "reason": "stop"}]
    m = B.metrics(trades, [("2025-01-02", 25100.0), ("2025-01-03", 25050.0), ("2025-01-06", 25050.0)])
    assert m["trades"] == 2 and m["win_rate_pct"] == 50.0 and m["avg_R"] == 0.25 and m["total_pnl"] == 50.0
    assert m["worst_day_usd"] == -50.0 and m["max_drawdown_pct"] == pytest.approx(-100 * 50 / 25100, abs=0.01)
    assert m["trades_per_day"] == pytest.approx(2 / 3, abs=0.001) and m["avg_hold_min"] == 45.0


def test_gate_modes():
    ok = {"stage": "odds", "why": None, "odds": {"lean": 0.02}}
    assert B.passes(ok, "loose") and not B.passes(ok, "strict") and B.passes(ok, "none")
    bad = {"stage": "odds", "why": "verdict toward the stop", "odds": {"lean": -0.1}}
    assert not B.passes(bad, "loose") and B.passes(bad, "none")
    assert not B.passes({"stage": "plan"}, "none")


def test_chart_text_uses_only_prior_days_and_bars_before_1015(monkeypatch):
    a = fake_arr()
    monkeypatch.setattr(B, "ARR", {"X": a})
    day = sorted(a["days"])[-1]
    doc = json.loads(B.chart_text("X", day))["chart"]["result"][0]
    i0 = a["days"][day][0]
    assert doc["timestamp"][-1] == int(a["t"][i0 + 8])                 # the 10:10 bar is the last one; 10:15 is not in
    assert len(doc["timestamp"]) <= 60 * 78 + 9 and doc["timestamp"][0] >= a["t"][a["days"][day - 61 if (day - 61) in a["days"] else sorted(a["days"])[0]][0]]


def test_coin_flip_has_zero_edge_on_a_random_walk(monkeypatch):
    a = fake_arr(150)
    monkeypatch.setattr(B, "ARR", {"X": a})
    days = sorted(a["days"])
    trades = [{"symbol": "X", "qty": 100, "entry_ref": 100.0, "pnl": 0.0, "entry_time": "2025-01-02 10:15", "exit_time": "2025-01-02 11:15"}] * 20
    r = B.coin_flip(trades, days, n=500)
    assert r["traders"] == 500 and r["random_mean"] < 0 and r["random_std"] > 0       # costs make a coin flip a loser
    assert 0 <= r["share_of_random_traders_beaten_pct"] <= 100
