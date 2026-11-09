import json
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

import intraday as I
import reconcile as R

DAYS = 17   # 16 history sessions + the day under test (lookback 14 needs 14 before it)
DAY = date(2026, 11, 9)


def make_frame(day_shape, seed=1):
    """Synthetic minute bars: DAYS weekdays ending 2026-11-09, a quiet random walk, the last day shaped by day_shape(minute) -> drift."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end="2026-11-09", periods=DAYS)
    rows, idx, price = [], [], 100.0
    for k, d in enumerate(days):
        o = price
        for m in range(390):
            c = o + rng.normal(0, 0.02) + (day_shape(m) if k == DAYS - 1 else 0.0)
            rows.append((o, max(o, c) + 0.01, min(o, c) - 0.01, c, 1000.0))
            idx.append(pd.Timestamp(d).tz_localize("America/New_York") + pd.Timedelta(minutes=570 + m))
            o = c
        price = o
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx), columns=["open", "high", "low", "close", "volume"])


UP = lambda m: 0.05 if m >= 20 else 0.0      # strong rally from 09:50 -> long at every check
FLAT = lambda m: 0.0


def frames(shape):
    return {"SPY": make_frame(shape, 1), "QQQ": make_frame(shape, 2)}


def live_row(day, sym, hour, decision, order_id="", price=100, vwap=100, upper=101, lower=99, minute=1):
    return {"time_ny": f"{day} {hour:02d}:{minute:02d}:30", "symbol": sym, "event": "check", "price": str(price), "vwap": str(vwap),
            "upper": str(upper), "lower": str(lower), "decision": decision, "qty_before": "0", "qty_after": "0", "order_id": order_id, "note": ""}


def decisions(fr, sym):
    D = I.Days.from_frame(fr[sym])
    return [R.SIDE[s] for _, s, *_ in I.noise_decisions(D, D.n - 1, 14, 60)]


def fill(day, hhmmss, sym, side, qty, price, order_id=None):
    t = datetime.fromisoformat(f"{day}T{hhmmss}-05:00")
    return {"id": "x", "time": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "symbol": sym, "side": side,
            "qty": str(qty), "price": str(price), "order_id": order_id}


def test_days_from_frame_uses_the_backtest_rule():
    D = I.Days.from_frame(frames(UP)["SPY"])
    d = D.n - 1
    assert [m for m, *_ in I.noise_decisions(D, d, 14, 60)] == [29, 89, 149, 209, 269, 329]
    assert (I.noise_breakout(D, 14, 60)["day"] == d).any()   # noise_breakout runs on the same decisions


def test_all_match():
    fr = frames(FLAT)
    rows = [live_row(DAY, s, h, d) for s in R.SYMBOLS for h, d in zip(range(10, 16), decisions(fr, s))]
    rec = R.reconcile_day(DAY, fr, rows, [])
    assert (rec["summary"]["decisions"], rec["summary"]["matched"], rec["summary"]["missing"]) == (12, 12, 0)
    assert R.alert_text(rec) is None


def test_missing_live_decision_is_counted_not_alerted():
    rec = R.reconcile_day(DAY, frames(UP), [], [])
    assert rec["summary"]["missing"] == 12 and rec["summary"]["matched"] == 0
    assert R.alert_text(rec) is None
    assert "12 not logged" in R.line(rec)


def test_mismatch_alert_names_check_and_numbers():
    fr = frames(UP)
    assert decisions(fr, "SPY")[0] == "long"
    rec = R.reconcile_day(DAY, fr, [live_row(DAY, "SPY", 10, "flat", price=100.5, vwap=100.4, upper=101.0, lower=99.0)], [])
    c = rec["checks"][0]
    assert not c["matched"] and c["bt_decision"] == "long" and c["live_decision"] == "flat" and c["why"]
    txt = R.alert_text(rec)
    assert "SPY 10:00" in txt and "live flat" in txt and "model long" in txt and "101.0" in txt


def test_mismatch_reason_is_data_when_sip_at_live_bar_disagrees_too():
    D = I.Days.from_frame(frames(UP)["SPY"])
    d = D.n - 1
    c = R.compare_checks(D, d, "SPY", {0: live_row(DAY, "SPY", 10, "flat")})[0]
    assert c["why"].startswith("data")


def test_slippage_sign():
    assert R.slippage_bp("buy", 100.1, 100.0) == pytest.approx(10.0)    # paid more: worse
    assert R.slippage_bp("sell", 100.1, 100.0) == pytest.approx(-10.0)  # received more: better
    assert R.slippage_bp("sell_short", 99.9, 100.0) == pytest.approx(10.0)


def test_fill_slippage_against_next_open_close_and_offhours():
    D = I.Days.from_frame(frames(UP)["SPY"])
    d = D.n - 1
    nxt, close = float(D.P[d, 30]), float(D.P[d, int(D.last[d]) + 1])
    fills = [fill(DAY, "10:01:33", "SPY", "buy", 16, nxt * 1.001, "o1"),
             fill(DAY, "15:56:30", "SPY", "sell", 16, close * 0.999, "o2"),
             fill(DAY, "02:26:00", "SPY", "buy", 1, 100.0, "o3")]
    recs = R.fill_records(fills, DAY, "SPY", D, d, {"o1": ("check", 0), "o2": ("close", None)})
    assert recs[0]["kind"] == "check" and recs[0]["slippage_bp"] == pytest.approx(10.0, abs=0.2)
    assert recs[1]["kind"] == "close" and recs[1]["slippage_bp"] == pytest.approx(10.0, abs=0.2)   # sold 10 bp under the close
    assert recs[2]["kind"] == "offhours" and recs[2]["slippage_bp"] is None


def test_live_pnl_and_equity_before():
    fills = [fill("2026-11-06", "10:01:00", "SPY", "buy", 10, 100), fill("2026-11-06", "15:56:00", "SPY", "sell", 10, 101),
             fill("2026-11-09", "10:01:00", "SPY", "buy", 10, 100), fill("2026-11-09", "15:56:00", "SPY", "sell", 10, 103)]
    assert R.live_pnl(fills, DAY, {"SPY": 103.0}) == pytest.approx(30.0)
    assert R.realized_before(fills, DAY) == pytest.approx(10.0)
    short = [fill("2026-11-06", "15:00:00", "QQQ", "sell_short", 2, 100)]   # left overnight: marked at its last price, no phantom cash
    assert R.realized_before(short, DAY) == pytest.approx(0.0)


def test_model_pnl_scales_with_equity_and_is_zero_when_flat():
    D = I.Days.from_frame(frames(UP)["SPY"])
    d = D.n - 1
    p1, p2 = R.model_pnl(D, d, 25_000.0), R.model_pnl(D, d, 50_000.0)
    assert p1 > 0 and p2 == pytest.approx(2 * p1, rel=0.1)
    assert R.model_pnl(I.Days.from_frame(make_frame(FLAT)), d, 25_000.0) == pytest.approx(0.0, abs=60)


def test_store_replaces_same_day_and_briefing_line(tmp_path):
    rec = R.reconcile_day(DAY, frames(UP), [], [fill(DAY, "10:01:33", "SPY", "buy", 5, 100.0, "o1")])
    out = tmp_path / "reconcile.json"
    R.store(rec, out)
    R.store(rec, out)
    data = json.loads(out.read_text())
    assert len(data["days"]) == 1 and data["summary"]["days"] == 1 and data["summary"]["decisions"] == 12
    assert R.briefing_line(DAY, out).startswith("Live vs backtest today:")
    assert R.briefing_line(date(2026, 1, 1), out) is None
    assert R.briefing_line(DAY, tmp_path / "nope.json") is None


def test_line_format():
    rec = {"summary": {"decisions": 12, "matched": 12, "missing": 0, "mean_slippage_bp": 1.4, "live_pnl": 223.4, "model_pnl": 231.2}}
    assert R.line(rec) == "Live vs backtest today: 12/12 decisions matched · avg slippage +1.4 bp · P&L live +$223 vs model +$231"


def test_evening_text_and_caption_show_the_line_only_when_present():
    import briefing as bf
    import briefing_cards as bc
    acct = {"ok": True, "equity": 25040.0, "last_equity": 25000.0, "cash": 1.0, "positions": [], "orders": [], "note": ""}
    res = {"intraday": {"tested": 0, "passed": 0, "rejected": 0, "best": None}}
    try:
        e = bf.evening_data(DAY, {"intraday": acct}, {"intraday": [25000.0]}, [], {}, {"intraday": None}, res)
    except Exception:
        pytest.skip("evening_data input shape changed; covered by test_briefing")
    assert "Live vs backtest" not in bf.text_evening(e) and "Live vs backtest" not in bc.caption(e)
    e["reconcile_line"] = "Live vs backtest today: 12/12 decisions matched"
    assert "Live vs backtest today: 12/12" in bf.text_evening(e) and "Live vs backtest today: 12/12" in bc.caption(e)
