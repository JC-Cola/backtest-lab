"""News feed (briefing headlines) and the shadow news-rule test, on fake data (no network, no orders)."""
from datetime import datetime, timezone

import pytest

import briefing as bf
import briefing_cards as bc
import news_feed as nf
import news_shadow as ns


def art(h, syms, t="2026-11-09T13:00:00Z"):
    return {"headline": h, "symbols": syms, "created_at": t}


# ---------- news_feed ----------
def test_pick_prefers_held_dedupes_and_limits_per_tag():
    arts = [art("Market wide story", [], "2026-11-09T14:00:00Z"),
            art("NVDA beats", ["NVDA"], "2026-11-09T10:00:00Z"),
            art("NVDA beats!", ["NVDA"], "2026-11-09T10:01:00Z"),       # duplicate after normalising
            art("NVDA again", ["NVDA"], "2026-11-09T11:00:00Z"),
            art("NVDA third", ["NVDA"], "2026-11-09T12:00:00Z"),        # 3rd NVDA: over the per-tag cap
            art("SPY drops", ["SPY", "XYZ"], "2026-11-09T09:00:00Z"),
            art("Unrelated co", ["XYZ"], "2026-11-09T14:30:00Z")]
    out = nf.pick(arts, {"SPY", "NVDA"})
    tags = [o["tag"] for o in out]
    assert tags[:3] == ["NVDA", "NVDA", "SPY"] and tags.count("NVDA") == 2
    assert len({o["headline"] for o in out}) == len(out) and "NVDA beats!" not in [o["headline"] for o in out]


def test_headlines_uses_positions_and_never_raises():
    seen = []

    def fake(symbols, start):
        seen.append(symbols)
        return [art("x", ["AAPL"])] if symbols else [art("macro", [])]

    out = nf.headlines([{"symbol": "AAPL"}, {"symbol": "BTC/USD"}], datetime(2026, 11, 9, 14, tzinfo=timezone.utc), fake)
    assert set(seen[0]) >= {"SPY", "QQQ", "NVDA", "AAPL", "BTCUSD"} and seen[1] is None
    assert [o["tag"] for o in out] == ["AAPL", "MKT"]

    def boom(symbols, start):
        raise OSError("down")
    assert nf.headlines([], datetime.now(timezone.utc), boom) == []


def test_fetch_builds_request_with_headers():
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"news": [{"headline": "h"}]}'
    got = {}

    def op(req, timeout=None):
        got["req"] = req
        return R()
    out = nf.fetch(["SPY"], datetime(2026, 11, 8, tzinfo=timezone.utc), key="K", secret="S", urlopen=op)
    assert out == [{"headline": "h"}]
    assert "symbols=SPY" in got["req"].full_url and "sort=desc" in got["req"].full_url
    assert got["req"].get_header("Apca-api-key-id") == "K"
    with pytest.raises(RuntimeError):
        nf.fetch(None, datetime.now(timezone.utc), key="", secret="")


def test_morning_card_and_text_with_headlines(monkeypatch):
    d = {"kind": "morning", "day": datetime(2026, 11, 9).date(), "flags": [], "waiting_candidates": [],
         "accounts": {n: {"equity": 25000.0, "note": "", "last": (10.0, 0.001), "positions": [], "since": 0.0, "waiting": [],
                          "dd": 0.01, "kill": 0.12, "day_limit": 0.03, "risk": "r"} for n in ("intraday",)},
         "headlines": [{"tag": "NVDA", "headline": "A very long headline " * 8, "ts": ""}, {"tag": "MKT", "headline": "Short", "ts": ""}]}
    png = bc.render(d)
    assert png[:4] == b"\x89PNG"
    assert "NVDA" in bf.text_morning(d)
    d["headlines"] = []
    assert bc.render(d)[:4] == b"\x89PNG" and "Headlines" not in bf.text_morning(d)


def test_gather_morning_survives_news_error(monkeypatch):
    import news_feed
    monkeypatch.setattr(news_feed, "headlines", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(bf, "_gather", lambda day: ({n: {"equity": 1.0, "last_equity": 1.0, "positions": []} for n in ("intraday",)},
                                                    {}))
    monkeypatch.setattr(bf.proposer_health, "briefing_warning", lambda: None)
    d = bf.gather_morning(datetime(2026, 11, 9, 14, 15, tzinfo=timezone.utc))
    assert d["headlines"] == []


# ---------- shadow: detection ----------
def make_bars(days, last_ret=0.0, opens=None):
    """days daily bars, ~1% alternating moves (vol ~1%), then a final bar returning last_ret."""
    from datetime import date, timedelta
    out, px, d = [], 100.0, date(2026, 6, 1)
    for k in range(days):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        r = 0.01 if k % 2 else -0.01
        if k == days - 1:
            r = last_ret
        c = px * (1 + r)
        out.append((str(d), (opens or {}).get(str(d), px), c))
        px, d = c, d + timedelta(days=1)
    return out


def test_detect_shock_threshold():
    b = make_bars(80, 0.05)
    s = ns.detect_shock(b, b[-1][0])
    assert s and s["ret"] > 0.049 and s["mult"] > 4
    assert ns.detect_shock(make_bars(80, 0.015), make_bars(80)[-1][0]) is None      # 1.5% < 2 x ~1%
    assert ns.detect_shock(make_bars(30, 0.2), make_bars(30)[-1][0]) is None        # not enough history
    assert ns.detect_shock(b, "2000-01-01") is None


def test_detect_uses_vol_before_the_day():
    b = make_bars(80, -0.04)
    assert ns.detect_shock(b, b[-1][0])["ret"] < 0


def test_news_window_is_1600_et_to_1600_et():
    s, e = ns.news_window("2026-11-05", "2026-11-06")   # EST in November: 16:00 = 21:00Z
    assert (s.hour, e.hour, s.day, e.day) == (21, 21, 5, 6)


def test_shock_headlines_filters_window_and_prefers_specific():
    arts = [art("too early", ["NVDA"], "2026-11-05T20:59:00Z"), art("too late", ["NVDA"], "2026-11-06T21:01:00Z"),
            art("broad", ["NVDA", "A", "B", "C"], "2026-11-06T15:00:00Z"), art("specific", ["NVDA"], "2026-11-06T14:00:00Z")]
    assert ns.shock_headlines("NVDA", "2026-11-05", "2026-11-06", lambda *a, **k: arts) == ["specific", "broad"]


# ---------- shadow: trades ----------
def test_sides_and_short_pnl():
    assert ns.side_for("momentum", 0.05) == "long" and ns.side_for("momentum", -0.05) == "short"
    assert ns.side_for("reversal", 0.05) == "short" and ns.side_for("reversal", -0.05) == "long"
    assert ns.pnl_pct("long", 100, 110) == pytest.approx(10 - 0.1)
    assert ns.pnl_pct("short", 100, 90) == pytest.approx(10 - 0.1)       # price falls: short wins
    assert ns.pnl_pct("short", 100, 110) == pytest.approx(-10 - 0.1)


def bars_for(sym, signal_idx_opens):
    """Bars: shock at the 80th bar, then 6 later bars with distinct opens (101..106)."""
    b = make_bars(80, 0.05)
    from datetime import date, timedelta
    d = date.fromisoformat(b[-1][0])
    for k in range(6):
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        b.append((str(d), 101.0 + k, 101.5 + k))
    return {sym: b}


def test_entry_exit_at_the_right_opens_and_no_double_open():
    bars = bars_for("NVDA", None)
    day = bars["NVDA"][79][0]
    st = ns.load_state(path=ns.PATH.parent / "nonexistent.json")
    # only the first 80 bars known: shock recorded, trades pending
    known = {"NVDA": bars["NVDA"][:80]}
    new = ns.run(st, known, day, lambda *a: ["Big news"])
    assert [s["symbol"] for s in new] == ["NVDA"] and len(st["trades"]) == 4
    assert {t["status"] for t in st["trades"]} == {"pending"}
    # next night: entry bar (index 80, open 101) exists -> open; 1d exit needs bar 81
    ns.run(st, {"NVDA": bars["NVDA"][:81]}, bars["NVDA"][80][0], lambda *a: [])
    assert {t["status"] for t in st["trades"]} == {"open"} and all(t["entry_px"] == 101.0 for t in st["trades"])
    ns.run(st, {"NVDA": bars["NVDA"][:82]}, bars["NVDA"][81][0], lambda *a: [])
    t = {(t["rule"], t["hold"]): t for t in st["trades"]}
    assert t[("momentum", 1)]["status"] == "closed" and t[("momentum", 1)]["exit_px"] == 102.0
    assert t[("momentum", 1)]["exit_date"] == bars["NVDA"][81][0]
    assert t[("momentum", 1)]["pnl_pct"] == pytest.approx(ns.pnl_pct("long", 101, 102), abs=1e-3)
    assert t[("reversal", 1)]["side"] == "short" and t[("reversal", 1)]["pnl_pct"] == pytest.approx(ns.pnl_pct("short", 101, 102), abs=1e-3)
    assert t[("momentum", 5)]["status"] == "open"
    ns.run(st, bars, bars["NVDA"][85][0], lambda *a: [])
    assert t[("momentum", 5)]["exit_px"] == 106.0 and t[("momentum", 5)]["exit_date"] == bars["NVDA"][85][0]
    assert len(st["trades"]) == 4 and len(st["shocks"]) == 1


def test_nightly_rerun_is_idempotent_and_notifies_once():
    bars = bars_for("AAPL", None)
    st = ns.load_state(path=ns.PATH.parent / "nonexistent.json")
    day = bars["AAPL"][79][0]
    assert len(ns.run(st, bars, day, lambda *a: ["h"])) == 1
    import copy
    snap = copy.deepcopy(st)
    assert ns.run(st, bars, day, lambda *a: ["h"]) == []        # no second shock message
    assert st == snap                                            # nothing double-opened or changed


def test_shock_needs_news():
    bars = bars_for("AMD", None)
    st = ns.load_state(path=ns.PATH.parent / "nonexistent.json")
    assert ns.run(st, bars, bars["AMD"][79][0], lambda *a: []) == [] and st["trades"] == []


def test_news_lookup_error_skips_without_crashing():
    bars = bars_for("AMD", None)
    st = ns.load_state(path=ns.PATH.parent / "nonexistent.json")

    def boom(*a):
        raise OSError("x")
    assert ns.run(st, bars, bars["AMD"][79][0], boom) == [] and st["trades"] == []


def test_messages_and_summary_line(tmp_path):
    sh = {"symbol": "NVDA", "date": "2026-11-05", "ret": -0.062, "mult": 2.8, "headlines": ["Chip curbs"]}
    m = ns.shock_message(sh)
    assert "NVDA −6.2% (2.8× normal)" in m and "momentum (short) & reversal (buy)" in m and "not traded" in m
    st = {"started": "2026-11-05", "shocks": [], "trades": []}
    assert ns.summary_line(st) is None
    for i, p in enumerate([1.0, 0.6]):
        st["trades"].append({"id": str(i), "rule": "momentum", "hold": 1, "status": "closed", "pnl_pct": p, "exit_date": "2026-11-09"})
    st["trades"].append({"id": "x", "rule": "reversal", "hold": 1, "status": "open", "pnl_pct": None, "exit_date": None})
    line = ns.summary_line(st)
    assert "momentum 1d +0.8% avg over 2 trades" in line and "reversal" not in line and "not traded" in line
    assert ns.summary_line(st, "2026-11-10") is None and ns.summary_line(st, "2026-11-09")
    p = tmp_path / "s.json"
    ns.save_state(st, p)
    assert ns.evening_line("2026-11-09", p) == line and ns.evening_line("2026-11-09", tmp_path / "none.json") is None


def test_evening_caption_and_text_include_shadow_line():
    d = {"kind": "evening", "day": datetime(2026, 11, 9).date(), "shadow_line": "Shadow news rules (not traded): x",
         "research": {"intraday": {"tested": 1}}, "waiting_candidates": [],
         "accounts": {n: {"equity": 1.0, "today": (1.0, 0.01), "verdict": "early", "days": 1, "trades": []} for n in ("intraday",)}}
    assert "Shadow news rules" in bc.caption(d)


def test_nightly_workflow_runs_shadow_before_commit():
    from pathlib import Path
    y = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "nightly.yml").read_text()
    assert "python news_shadow.py" in y and y.index("news_shadow.py") < y.index("Commit results")
