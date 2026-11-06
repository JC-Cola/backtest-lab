from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from alpaca_paper import ledger_from_fills


def F(i, t, sym, side, qty, px):
    return {"id": str(i), "time": f"2026-11-04T{t}Z", "symbol": sym, "side": side, "qty": qty, "price": px}


def test_no_fills():
    assert ledger_from_fills([], 25000) == {"rows": [], "realized": 0.0}


def test_long_open_close():
    r = ledger_from_fills([F(1, "15:00:00", "SPY", "buy", 10, 100), F(2, "16:30:05", "SPY", "sell", 10, 101.5)], 25000)
    assert r["realized"] == 15.0 and len(r["rows"]) == 1
    row = r["rows"][0]
    assert row["time"] == "2026-11-04 11:30:05" and row["balance_before"] == 25000 and row["balance_after"] == 25015
    assert row["action"] == "Close long position for symbol SPY at price 101.5 for 10 units. Position AVG Price was 100"


def test_short_partial_then_full_close_newest_first():
    r = ledger_from_fills([F(1, "15:00:00", "QQQ", "sell", 10, 100), F(2, "15:10:00", "QQQ", "buy", 4, 99),
                           F(3, "15:20:00", "QQQ", "buy", 6, 102)], 1000)
    assert [x["realized_pnl"] for x in r["rows"]] == [-12.0, 4.0]  # newest first
    assert r["rows"][1]["action"].startswith("Close short position for symbol QQQ at price 99 for 4 units")
    assert r["rows"][0]["balance_before"] == 1004 and r["rows"][0]["balance_after"] == 992 and r["realized"] == -8.0


def test_flip_long_to_short_in_one_fill():
    r = ledger_from_fills([F(1, "15:00:00", "SPY", "buy", 5, 100), F(2, "15:05:00", "SPY", "sell", 8, 110),
                           F(3, "15:10:00", "SPY", "buy", 3, 105)], 0)
    a, b = r["rows"][1], r["rows"][0]
    assert a["realized_pnl"] == 50.0 and "for 5 units" in a["action"]  # closes the long only
    assert b["realized_pnl"] == 15.0 and "Close short" in b["action"] and "AVG Price was 110" in b["action"]


def test_fractional_crypto():
    r = ledger_from_fills([F(1, "15:00:00", "BTC/USD", "buy", 0.5, 60000), F(2, "15:01:00", "BTC/USD", "sell", 0.25, 61000)], 25000)
    assert r["rows"][0]["realized_pnl"] == 250.0 and "BTC/USD at price 61000 for 0.25 units" in r["rows"][0]["action"]
