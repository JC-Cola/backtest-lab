"""The nightly report covers every system (Noise band, qlib, TradingAgents, day-trader-skill, Pelosi) and never fails on missing data."""
import json

import pytest
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import report as rp  # noqa: E402

DAY = date(2026, 11, 20)
FILL = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)   # 10:00 NY


@pytest.fixture(autouse=True)
def no_real_adjustments(tmp_path, monkeypatch):
    monkeypatch.setattr(rp.adjustments, "PATH", tmp_path / "none.json")   # the real corrections must not shift these fixtures


def acct(eq, last, pos=(), orders=()):
    return {"ok": True, "equity": eq, "cash": 1000.0, "last_equity": last, "positions": list(pos), "orders": list(orders), "note": ""}


def fill(sym, side, price=10.0, qty=2.0):
    return {"id": "", "symbol": sym, "side": side, "qty": qty, "filled_qty": qty, "price": price, "status": "filled", "submitted": FILL, "filled": FILL}


POS = [{"symbol": "AAA", "qty": 3.0, "avg_price": 10.0, "price": 11.0, "pl": 3.0}]
EXTRAS = {
    "cards": {"accounts": {"qlib": {"ok": True, "days": 9, "end": "2026-11-19", "metrics": {"cagr": 0.1, "sharpe": 1.5, "sortino": 2.0, "max_dd": -0.05, "win_days": 0.6, "vol": 0.1, "beta": 0.9}},
                           "ta": {"ok": False, "note": "not enough history yet (2 days)", "metrics": None}}},
    "plans": {"plans": {"NVDA": {"status": "active", "opened": True, "log": ["2026-11-20: stop hit, sold 4"], "plan": {"side": "long", "stop": 100.0, "tp": None},
                                 "scale": [{"state": "pending", "size_x": 0.5, "condition": "above", "price": 118.94}]}}},
    "news": {"shocks": [{"date": "2026-11-20", "symbol": "GPN", "ret": -0.12}, {"date": "2026-11-19", "symbol": "OLD", "ret": 0.1}],
             "trades": [{"status": "closed", "rule": "momentum", "pnl_pct": 3.0}, {"status": "closed", "rule": "momentum", "pnl_pct": -1.0},
                        {"status": "closed", "rule": "reversal", "pnl_pct": -2.0}, {"status": "open", "rule": "reversal", "pnl_pct": None}]},
    "states": {"qlib": {"picks": [{"symbol": "AAA"}], "rebalances": [{"data_date": "2026-11-19", "orders": [1, 2]}]},
               "ta": {"decisions": [{"date": "2026-11-20", "symbol": "NVDA", "rating": "Buy", "rationale": "Strong momentum. Second sentence."}]},
               "dts": {"trades": [{"date": "2026-11-20", "symbol": "ED", "side": "long", "qty": 5, "entry_time": "2026-11-20 10:15", "entry_price": 120.0,
                                   "exit_time": "2026-11-20 10:50", "exit_price": 121.0, "pnl": 5.0, "reason": "target"}], "days": {"2026-11-20": {"decisions": [1, 2, 3]}}},
               "pelosi": {"filings": [{"filed": "2026-11-20", "member": "Pelosi", "ticker": "NVDA", "type": "purchase", "traded": "2026-11-10", "amount_range": "$1M"}],
                          "orders": [{"symbol": "AAA", "member": "Pelosi", "doc_id": "123"}]}},
}


def full():
    return {"intraday": acct(26000.0, 25000.0), "qlib": acct(25500.0, 25400.0, POS, [fill("AAA", "buy"), fill("ZZZ", "sell")]),
            "ta": acct(24000.0, 24100.0, POS, [fill("NVDA", "buy")])}


def test_all_five_accounts_and_table():
    md = rp.build(DAY, full(), [], "", None, EXTRAS)
    for i, t in enumerate(["Noise band", "qlib", "TradingAgents", "News shadow test"], 1):
        assert f"## {i}. {t}" in md
    pos = [md.index(f"## {i}.") for i in range(1, 5)]
    assert pos == sorted(pos) and md.index("## All accounts") < pos[0]
    tbl = md.split("## All accounts")[1].split("## 1.")[0]
    assert "| Noise band | $26,000.00 | +$1,000.00 (+4.00%) | +$1,000.00 (+4.00%) |" in tbl and "**Total**" in tbl
    assert "| AAA | 3 | $10.00 | $11.00 | +$3.00 |" in md                       # positions
    assert "rebalance: in" in md and "rebalance: out" in md and "last rebalance on 2026-11-19" in md
    assert "Report card (9 days to 2026-11-19): CAGR +10.0%, Sharpe 1.50" in md and "not enough history yet (2 days)" in md
    assert "Buy: Strong momentum." in md and "Second sentence" not in md            # one-line rationale
    assert "NVDA: stop hit, sold 4" in md and "| NVDA | long | $100.00 | none | 0.5x above $118.94 |" in md
    assert "day-trader-skill (shadow)" not in md and "Pelosi (shadow)" not in md   # retired: not shown
    assert "Shocks today: 1: GPN -12.0%" in md and "| momentum | 2 | +1.00% |" in md and "| reversal | 1 | -2.00% |" in md


def test_missing_data_never_fails():
    md = rp.build(DAY, {"intraday": acct(25100.0, 25000.0)}, [], "", None)
    assert md.count("no data") >= 6 and "## 4. News shadow test" in md and "<!-- pl: intraday=100.0 qlib= ta= -->" in md


def test_broken_section_is_contained():
    bad = full()
    bad["qlib"]["orders"] = [{"symbol": "X", "side": "buy", "filled_qty": 1, "filled": "not a datetime"}]
    md = rp.build(DAY, bad, [], "", None, EXTRAS)
    assert "could not build the detail" in md and "## 3. TradingAgents" in md


def test_header_and_index_roundtrip(tmp_path):
    md = rp.build(DAY, full(), [], "", None, EXTRAS)
    assert md.startswith("<!-- pl: intraday=1000.0 qlib=100.0 ta=-100.0 -->")
    (tmp_path / "2026-11-20.md").write_text(md)
    (tmp_path / "2026-11-03.md").write_text("<!-- pl: long-term=-2.0 intraday=4.25 -->\n")
    rp.index(tmp_path)
    line = (tmp_path / "README.md").read_text().splitlines()
    assert "qlib +$100.00" in line[-2] and "ta -$100.00" in line[-2]
    assert "intraday +$4.25" in line[-1] and "long-term" not in line[-1]


def test_main_writes_latest_without_keys(tmp_path, monkeypatch):
    mem = tmp_path / "mem"
    mem.mkdir()
    (mem / "news_shadow.json").write_text(json.dumps({"shocks": [], "trades": []}))
    monkeypatch.setattr(rp, "MEM", mem)
    monkeypatch.setattr(rp, "OUT", tmp_path / "reports")
    monkeypatch.setattr(rp, "SYSTEMS", [(k, t, ke, se, mem / Path(p).name, c) for k, t, ke, se, p, c in rp.SYSTEMS])
    for k in ("ALPACA_INTRADAY_API_KEY", "ALPACA_DTS_API_KEY", "ALPACA_TA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    md = rp.main("2026-11-20")
    assert (tmp_path / "reports" / "latest.md").read_text() == md == (tmp_path / "reports" / "2026-11-20.md").read_text()
    assert "| qlib | no data | no data | no data |" in md and "Shocks today: 0" in md


def test_qlib_adjustment_applied_to_section_and_total(tmp_path, monkeypatch):
    a = tmp_path / "adj.json"
    a.write_text('[{"account": "dts", "date": "2026-11-20", "symbol": "SNDK", "usd": 628.67, "why": "t"}]')
    monkeypatch.setattr(rp.adjustments, "PATH", a)
    accts = {"intraday": acct(25000.0, 25000.0), "qlib": acct(24400.0, 24500.0), "ta": acct(25000.0, 25000.0)}
    md = rp.build(DAY, accts, [], "", None, EXTRAS)
    assert "$25,028.67" in md and "Includes split correction +$628.67 (SNDK, Alpaca paper bug)" in md
    assert rp.adjusted("qlib", accts["qlib"], date(2026, 11, 20))[0]["equity"] == pytest.approx(25028.67)
    assert rp.day_pl(rp.adjusted("qlib", accts["qlib"], date(2026, 11, 20))[0]) == pytest.approx(528.67)
    assert rp.day_pl(rp.adjusted("qlib", accts["qlib"], date(2026, 11, 24))[0]) == pytest.approx(-100.0)
