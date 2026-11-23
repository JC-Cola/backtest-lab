import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from alpaca_paper import ledger_from_fills

TPL = ROOT / "report" / "lab_template.html"


def test_template_has_live_code():
    t = TPL.read_text()
    for needle in ("alpaca_live(", 'claude.use("mcp")', "watchTool", "execute_sql", "LIVE-PURE-BEGIN", 'id="live-badge"',
                   "Live data not set up yet", "Live unavailable"):
        assert needle in t
    for bad in ("APCA", "ALPACA_SECRET", "ALPACA_API_KEY", "insert into", "delete from"):
        assert bad not in t


def _js_ledger(fills, start):
    t = TPL.read_text()
    pure = t.split("// LIVE-PURE-BEGIN")[1].split("\n", 1)[1].split("// LIVE-PURE-END")[0]
    prog = pure + "\nconst IN = JSON.parse(require('fs').readFileSync(0, 'utf8'));\nconsole.log(JSON.stringify(ledgerFromFills(IN.fills, IN.start)));"
    r = subprocess.run(["node", "-e", prog], input=json.dumps({"fills": fills, "start": start}), capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_ledger_matches_python():
    rnd = random.Random(7)
    for trial in range(30):
        fills = []
        for i in range(rnd.randint(1, 60)):
            sym = rnd.choice(["SPY", "QQQ", "BTC/USD"])
            fills.append({"id": f"{i:04d}", "time": f"2026-11-0{rnd.randint(2, 6)}T{rnd.randint(14, 20)}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}.{rnd.randint(0, 999999):06d}Z",
                          "symbol": sym, "side": rnd.choice(["buy", "sell"]),
                          "qty": str(round(rnd.uniform(0.01, 9), 4)) if sym == "BTC/USD" else str(rnd.randint(1, 20)),
                          "price": str(round(rnd.uniform(50, 800), 2))})
        py = ledger_from_fills(fills, 25000.0)
        assert _js_ledger(fills, 25000.0) == py


def test_template_three_accounts_live_and_no_long_term():
    t = TPL.read_text()
    for acct in ("id", "dts", "ta"):
        assert f"select private.alpaca_live('{acct}') as d" in t
    assert "alpaca_live('lt')" not in t and 'lt: "select' not in t          # long-term is gone and never queried
    assert "RETIRED" not in t and "Long-term" not in t and 'data-f="longterm"' not in t
    assert "Live data not set up yet" in t
    assert "LIVE_EVERY" not in t and "liveEvery" in t and "15000" in t and "5000" in t   # 5 s for one account, 15 s in Compare


# ---- canonical daily series (P&L calendar) -------------------------------------------------------------------------
def _js_run(code, payload):
    t = TPL.read_text()
    pure = t.split("// LIVE-PURE-BEGIN")[1].split("\n", 1)[1].split("// LIVE-PURE-END")[0]
    prog = pure + "\nconst IN = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n" + code
    r = subprocess.run(["node", "-e", prog], input=json.dumps(payload), capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def _canon(h, **o):
    return _js_run("console.log(JSON.stringify(canonSeries(barsFromHistory(IN.h), IN.o)));", {"h": h, "o": o})


def _ts(date, hour_utc):  # epoch seconds of date at hour_utc:00Z
    from datetime import datetime, timezone
    y, m, d = map(int, date.split("-"))
    return int(datetime(y, m, d, hour_utc, tzinfo=timezone.utc).timestamp())


def _ms(date, hour_utc):
    return _ts(date, hour_utc) * 1000


def _hist(rows):  # rows [(date_utc_stamp, hour_utc, equity)]
    return {"timestamp": [_ts(d, h) for d, h, _ in rows], "equity": [e for _, _, e in rows]}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_canon_series_gap_funding_holiday_dst_today():
    # EDT stamps (20:00 NY = 00:00Z next day) then, after the Nov 1 DST end, EST stamps (20:00 NY = 01:00Z next day)
    rows = [("2026-10-28", 0, None), ("2026-10-29", 0, 0.0),                      # before funding: null / 0
            ("2026-10-30", 0, 25000.0),                                          # Thu Oct 29 NY, funding day (== deposit -> 0)
            ("2026-10-31", 0, 25100.0),                                          # Fri Oct 30
            ("2026-11-03", 1, 25050.0),                                          # Mon Nov 2 (EST, after DST end); Sat/Sun gap
            ("2026-11-04", 1, 25050.0),                                          # Tue Nov 3 flat
            ("2026-11-06", 1, 25300.0),                                          # Thu Nov 5 (Wed Nov 4 missing = gap day): delta spans the gap
            ("2026-11-07", 1, 25250.0)]                                          # Fri Nov 6
    S = _canon(_hist(rows), start=25000, equityNow=None, nowMs=_ms("2026-11-07", 18))
    d = {c["date"]: c for c in S["days"]}
    assert [c["date"] for c in S["days"]] == ["2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03", "2026-11-05", "2026-11-06"]
    assert d["2026-10-29"]["pl"] == 0 and d["2026-10-30"]["pl"] == 100 and d["2026-11-02"]["pl"] == -50
    assert d["2026-11-03"]["pl"] == 0 and d["2026-11-05"]["pl"] == 250 and d["2026-11-06"]["pl"] == -50
    assert d["2026-10-30"]["pct"] == 0.4 and d["2026-11-02"]["prev"] == 25100
    assert S["start"] == 25000 and S["eqNow"] == 25250 and S["total"] == 250
    assert round(sum(c["pl"] for c in S["days"]), 2) == S["total"]
    # monthly reconciliation: October = 25000 -> 25100, November = 25100 -> 25250
    o = _js_run("console.log(JSON.stringify([monthRecon(canonSeries(barsFromHistory(IN.h), IN.o), '2026-10'), monthRecon(canonSeries(barsFromHistory(IN.h), IN.o), '2026-11')]));",
                {"h": _hist(rows), "o": {"start": 25000}})
    assert o[0]["total"] == 100 and o[0]["from"] == 25000 and o[0]["to"] == 25100 and o[0]["ok"]
    assert o[1]["total"] == 150 and o[1]["from"] == 25100 and o[1]["to"] == 25250 and o[1]["ok"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_canon_series_holiday_and_dst_start():
    # spring DST: Fri Mar 6 (EST stamp 01:00Z Mar 7) then Mon Mar 9 (EDT stamp 00:00Z Mar 10); a Thanksgiving-style holiday leaves no bar
    S = _canon(_hist([("2026-03-07", 1, 25000.0), ("2026-03-10", 0, 25400.0), ("2026-11-26", 1, 25400.0), ("2026-11-28", 1, 25450.0)]), start=25000)
    assert [c["date"] for c in S["days"]] == ["2026-03-06", "2026-03-09", "2026-11-25", "2026-11-27"]
    assert [c["pl"] for c in S["days"]] == [0, 400, 0, 50]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_canon_series_today_cell():
    rows = [("2026-11-17", 1, 25000.0), ("2026-11-18", 1, 25100.0)]  # last bar Tue Nov 17
    # Wed Nov 18 15:00Z = 10:00 NY, no bar yet: today = live equity - last bar
    S = _canon(_hist(rows), start=25000, equityNow=25160.5, nowMs=_ms("2026-11-18", 15))
    assert [c["date"] for c in S["days"]] == ["2026-11-16", "2026-11-17", "2026-11-18"]
    assert S["days"][-1]["pl"] == 160.5 - 100 and S["days"][-1]["today"] and S["total"] == 160.5 and S["eqNow"] == 25160.5
    # the same date already has a bar (the bar is replaced by the live equity, never counted twice)
    S = _canon(_hist([("2026-11-17", 1, 25000.0), ("2026-11-18", 1, 25100.0), ("2026-11-19", 1, 25120.0)]), start=25000, equityNow=25130.0, nowMs=_ms("2026-11-18", 20))
    assert len(S["days"]) == 3 and S["days"][-1]["date"] == "2026-11-18" and S["days"][-1]["equity"] == 25130.0 and not S["days"][-1]["today"]
    assert round(sum(c["pl"] for c in S["days"]), 2) == S["total"] == 130.0
    # holiday / flat: equity unchanged -> no extra cell; late evening NY date (01:00Z next day) is still the same NY date
    S = _canon(_hist(rows), start=25000, equityNow=25100.0, nowMs=_ms("2026-11-19", 15))
    assert len(S["days"]) == 2
    S = _canon(_hist(rows), start=25000, equityNow=25111.0, nowMs=_ms("2026-11-18", 3))   # 22:00 NY on Nov 17
    assert S["days"][-1]["date"] == "2026-11-17" and S["days"][-1]["equity"] == 25111.0 and len(S["days"]) == 2


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_canon_series_funding_amount_and_empty():
    S = _canon(_hist([("2026-11-17", 1, 100000.0), ("2026-11-18", 1, 100500.0)]), start=25000)   # funded with something other than the deposit
    assert S["start"] == 100000 and [c["pl"] for c in S["days"]] == [0, 500] and S["total"] == 500
    S = _canon({"timestamp": [], "equity": []}, start=25000, equityNow=25000.0, nowMs=_ms("2026-11-18", 15))
    assert S["days"] == [] and S["eqNow"] is None


def test_template_daily_wiring():
    t = TPL.read_text()
    for acct in ("id", "dts", "ta"):
        assert f"select private.alpaca_daily('{acct}') as d" in t
    assert "300000" in t and "snapshot as of" in t and "doesn't reconcile" in t and "pcal-recon" in t
    assert "A.calendar" not in t and "base.calendar" not in t     # the calendar no longer reads the embedded series except via canonOf()
