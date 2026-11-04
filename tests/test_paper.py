from datetime import datetime, timezone

import numpy as np
import pandas as pd

import paper


def frame(dates):
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    v = np.arange(1, len(idx) + 1, dtype=float)
    return pd.DataFrame({"Open": v, "High": v, "Low": v, "Close": v}, index=idx)


def test_equity_bar_dropped_until_after_the_close():
    df = frame(["2026-10-14", "2026-10-15", "2026-10-16"])
    during = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)   # 14:00 New York, market open
    after = datetime(2026, 10, 16, 21, 0, tzinfo=timezone.utc)    # 17:00 New York
    assert str(paper.complete_bars(df, "SPY", during).index[-1].date()) == "2026-10-15"
    assert str(paper.complete_bars(df, "SPY", after).index[-1].date()) == "2026-10-16"


def test_crypto_bar_for_current_utc_day_always_dropped():
    df = frame(["2026-10-16", "2026-10-17", "2026-10-18"])
    now = datetime(2026, 10, 18, 23, 59, tzinfo=timezone.utc)
    assert str(paper.complete_bars(df, "BTC-USD", now).index[-1].date()) == "2026-10-17"


def test_report_scores_next_open_to_open_with_costs(tmp_path, monkeypatch):
    df = frame(["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"])  # opens 1,2,3,4
    monkeypatch.setattr(paper, "LOG", tmp_path / "log.csv")
    monkeypatch.setattr(paper, "REPORT", tmp_path / "rep.json")
    monkeypatch.setattr(paper.data, "load", lambda t, max_age_s=0: df)
    paper.LOG.write_text("logged_at_utc,decision_date,key,name,market,position,close\n"
                         "x,2026-10-12,K,k,SPY,1,1\nx,2026-10-13,K,k,SPY,1,2\nx,2026-10-14,K,k,SPY,0,3\n")
    out = paper.report()["K"]
    c = paper.CONFIG["SPY"]["cost_bps"] / 1e4
    # decision 10-12 -> open 10-13 (2) to open 10-14 (3); decision 10-13 -> 3 to 4; 10-14 not yet known
    exp = (1 + (3 / 2 - 1) - c) * (1 + (4 / 3 - 1)) - 1
    assert out["scored"] == 2 and abs(out["paper_return"] - exp) < 1e-12


def test_alpaca_sleeves_net_and_whole_shares():
    import alpaca_paper as a
    d = {"A": {"market": "SPY", "position": "1"}, "B": {"market": "SPY", "position": "0"},
         "C": {"market": "BTC-USD", "position": "1"}}
    t = a.targets(d, 180000)  # the daily strategies share DAILY_SHARE (half) of equity: 90000 / 3 sleeves
    assert a.DAILY_SHARE == 0.5 and t == {"SPY": 30000.0, "BTC/USD": 30000.0}
    orders = {o["symbol"]: o for o in a.plan(t, {"SPY": 10, "QQQ": 5}, {"SPY": 700, "BTC/USD": 60000, "QQQ": 600})}
    assert orders["SPY"]["qty"] == 32 and orders["SPY"]["tif"] == "opg"      # 42 whole shares wanted, 10 held
    assert orders["QQQ"]["side"] == "sell" and orders["QQQ"]["qty"] == 5     # no longer wanted
    assert orders["BTC/USD"]["qty"] == 0.5 and orders["BTC/USD"]["tif"] == "gtc"
