"""Universe-wide news scan: universe loading + fallback, batching, market-wide news mapping, abnormal move / beta,
ranking and cap, LLM review parsing, the llm shadow rule, idempotent re-runs, morning gappers (all on fake data)."""
import copy
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import news_gappers as ng
import news_review as nr
import news_scan as sc
import news_shadow as ns
import universe as uni
from report import NY


# ---------- universe ----------
def sym(i):
    return "Q" + chr(65 + i // 26) + chr(65 + i % 26)


SP_CSV = "Symbol,Security\n" + "\n".join(f"{sym(i)},x" for i in range(300)) + "\nBRK-B,x\n"
NDX_JSON = json.dumps({"data": {"data": {"rows": [{"symbol": "NVDA"}, {"symbol": "AAPL"}, {"symbol": sym(1)}]}}})


class Resp:
    def __init__(self, body): self.body = body.encode() if isinstance(body, str) else body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self.body
    def __iter__(self): return iter(())


def fake_open(req, timeout=None):
    u = req.full_url
    if "constituents.csv" in u:
        return Resp(SP_CSV)
    if "nasdaq100" in u:
        return Resp(NDX_JSON)
    raise OSError("unexpected " + u)


def test_rebuild_filters_to_tradable_and_cleans_symbols():
    ok = {sym(i) for i in range(1, 300)} | {"BRK.B", "NVDA"}
    syms, note = uni.rebuild(fake_open, lambda: ok)
    assert "BRK.B" in syms and "NVDA" in syms and "AAPL" not in syms and sym(0) not in syms and "Alpaca" in note
    assert syms == sorted(syms)


def test_rebuild_without_alpaca_keeps_constituents_and_too_small_fails():
    def boom():
        raise RuntimeError("no keys")
    syms, note = uni.rebuild(fake_open, boom)
    assert "AAPL" in syms and "skipped" in note
    with pytest.raises(RuntimeError):
        uni.rebuild(lambda req, timeout=None: (_ for _ in ()).throw(OSError("down")), boom)


def test_load_cache_refresh_and_fallback_chain(tmp_path):
    cache, fb = tmp_path / "u.csv", tmp_path / "fb.txt"
    fb.write_text("# fallback\nAAA\nBBB BRK-B\n")
    down = lambda req, timeout=None: (_ for _ in ()).throw(OSError("down"))
    today = date(2026, 11, 9)
    assert uni.load(today, urlopen=down, cache=cache, fallback=fb) == (["AAA", "BBB", "BRK.B"], "static fallback")
    syms, src = uni.load(today, urlopen=fake_open, tradable_fn=lambda: {"AAPL", "NVDA", *{sym(i) for i in range(300)}},
                         cache=cache, fallback=fb)
    assert src.startswith("rebuilt") and len(syms) > 300 and cache.exists()
    assert uni.load(today + timedelta(days=3), urlopen=down, cache=cache, fallback=fb)[1] == "cache 2026-11-09"
    assert uni.load(today + timedelta(days=30), urlopen=down, cache=cache, fallback=fb)[1] == "stale cache 2026-11-09"
    assert uni.load(today + timedelta(days=30), refresh=False, urlopen=down, cache=cache, fallback=fb)[1].startswith("cache")


def test_dry_run_load_does_not_write_cache(tmp_path):
    cache = tmp_path / "u.csv"
    uni.load(date(2026, 11, 9), write=False, urlopen=fake_open, tradable_fn=lambda: set(), cache=cache, fallback=tmp_path / "x")
    assert not cache.exists()


def test_committed_fallback_is_a_real_universe():
    syms = uni.read_fallback()
    assert 400 <= len(syms) <= 650 and "AAPL" in syms and "NVDA" in syms and "SPY" not in syms


# ---------- batching ----------
def test_chunks_and_batched_bars_requests():
    assert [len(c) for c in sc.chunks(range(250), 100)] == [100, 100, 50]
    seen = []

    def op(req, timeout=None):
        seen.append(req.full_url)
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(req.full_url).query)
        syms = q["symbols"][0].split(",")
        if "page_token" not in q and syms[0] == "A000":   # first batch pages once
            body = {"bars": {s: [{"t": "2026-11-05T05:00:00Z", "o": 1, "c": 2, "v": 10}] for s in syms}, "next_page_token": "t"}
        elif "page_token" in q:
            body = {"bars": {syms[0]: [{"t": "2026-11-06T05:00:00Z", "o": 2, "c": 3, "v": 11}]}}
        else:
            body = {"bars": {s: [{"t": "2026-11-05T05:00:00Z", "o": 1, "c": 2, "v": 10}] for s in syms}}
        return Resp(json.dumps(body))
    syms = [f"A{i:03d}" for i in range(230)]
    out = sc.fetch_bars(syms, date(2026, 10, 1), date(2026, 11, 6), key="K", secret="S", urlopen=op)
    assert len(out) == 230 and len(seen) == 4               # 3 batches + 1 extra page
    assert out["A000"] == [("2026-11-05", 1.0, 2.0, 10.0), ("2026-11-06", 2.0, 3.0, 11.0)]
    assert all("feed=sip" in u for u in seen)
    with pytest.raises(RuntimeError):
        sc.fetch_bars(["A"], date(2026, 10, 1), date(2026, 11, 6), key="", secret="")


def test_bars_fall_back_to_iex_per_batch():
    def op(req, timeout=None):
        if "feed=sip" in req.full_url:
            raise OSError("sip refused")
        return Resp(json.dumps({"bars": {"A": [{"t": "2026-11-05T05:00:00Z", "o": 1, "c": 2, "v": 5}]}}))
    assert sc.fetch_bars(["A"], date(2026, 10, 1), date(2026, 11, 6), key="K", secret="S", urlopen=op)["A"][0][3] == 5.0


# ---------- market-wide news ----------
def art(h, syms, t):
    return {"headline": h, "symbols": syms, "created_at": t}


def test_news_window_paging_and_mapping():
    pages = [{"news": [art("a", ["NVDA"], "2026-11-06T15:00:00Z")], "next_page_token": "p2"},
             {"news": [art("b", ["NVDA", "AMD", "X", "Y"], "2026-11-06T16:00:00Z")], "next_page_token": "p3"},
             {"news": [art("c", ["AMD"], "2026-11-06T17:00:00Z"), art("too late", ["NVDA"], "2026-11-06T22:00:00Z"),
                       art("a", ["NVDA"], "2026-11-06T14:00:00Z")]}]
    calls = []

    def op(req, timeout=None):
        calls.append(req.full_url)
        return Resp(json.dumps(pages[len(calls) - 1]))
    s, e = ns.news_window("2026-11-05", "2026-11-06")
    arts = sc.fetch_news_window(s, e, key="K", secret="S", urlopen=op)
    assert len(calls) == 3 and len(arts) == 5 and "symbols=" not in calls[0] and "page_token=p3" in calls[2]
    m = sc.news_by_symbol(arts, {"NVDA", "AMD", "TSLA"}, s, e)
    assert m["NVDA"] == ["a", "b"] and m["AMD"] == ["c", "b"] and "TSLA" not in m and "X" not in m   # specific first, deduped, window


def test_news_page_error_raises():
    with pytest.raises(OSError):
        sc.fetch_news_window(datetime(2026, 11, 5, tzinfo=timezone.utc), key="K", secret="S",
                             urlopen=lambda req, timeout=None: (_ for _ in ()).throw(OSError("503")))


# ---------- features: relvol, gap, beta, abnormal move ----------
def series(n, vol=1_000_000.0, spy_beta=1.5, last=None, spy_last=None):
    """Aligned stock/SPY bars: SPY alternates +-1%, the stock moves spy_beta x SPY (so beta is exactly spy_beta)."""
    d, spy, stk, px_s, px = date(2026, 6, 1), [], [], 100.0, 50.0
    for k in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        r = 0.01 if k % 2 else -0.01
        if k == n - 1:
            r = spy_last if spy_last is not None else r
        rs = last if (k == n - 1 and last is not None) else spy_beta * r
        spy.append((str(d), px_s, px_s * (1 + r), 1e7)); stk.append((str(d), px, px * (1 + rs), vol * (3 if k == n - 1 else 1)))
        px_s, px, d = px_s * (1 + r), px * (1 + rs), d + timedelta(days=1)
    return stk, spy


def test_beta_relvol_gap_and_abnormal_move():
    stk, spy = series(90, last=-0.08, spy_last=-0.02)
    day = stk[-1][0]
    assert sc.beta_vs(stk, spy, day) == pytest.approx(1.5, abs=1e-6)
    sh = ns.detect_shock(stk, day)
    f = sc.features(stk, day, sh["ret"], spy)
    assert f["relvol"] == pytest.approx(3.0) and f["spy_ret"] == pytest.approx(-0.02)
    assert f["beta"] == pytest.approx(1.5, abs=1e-3)
    assert f["abn"] == pytest.approx(-0.08 - 1.5 * -0.02, abs=1e-4)        # market explains 3 of the 8 points
    assert f["gap"] == pytest.approx(stk[-1][1] / stk[-2][2] - 1)


def test_features_degrade_gracefully():
    stk, _ = series(90, last=0.06)
    f = sc.features([b[:3] for b in stk], stk[-1][0], 0.06, None)      # no volume, no market bars
    assert f["relvol"] is None and f["spy_ret"] is None and f["abn"] == 0.06
    stk2, spy2 = series(30, last=0.06, spy_last=0.01)                    # too little history for beta -> beta 1.0 fallback
    f2 = sc.features(stk2, stk2[-1][0], 0.06, spy2)
    assert f2["beta"] is None and f2["abn"] == pytest.approx(0.05)


# ---------- ranking and cap ----------
def universe_bars(moves):
    """moves: {sym: (ret, volume multiple)}; plus SPY (flat-ish). All shocks on the last bar."""
    bars = {}
    for sym, (r, vm) in moves.items():
        stk, spy = series(90, last=r)
        stk = [b[:3] + (b[3] / 3 * vm if k == len(stk) - 1 else b[3],) for k, b in enumerate(stk)]
        bars[sym] = stk
        bars["SPY"] = spy
    return bars


def test_rank_by_abnormal_move_times_relvol_and_cap():
    moves = {"AAA": (0.10, 1.0), "BBB": (0.06, 5.0), "CCC": (-0.09, 2.0), "DDD": (0.08, 0.5)}
    bars = universe_bars(moves)
    day = bars["AAA"][-1][0]
    st = ns.load_state(path=ns.PATH.parent / "none.json")
    new = ns.run(st, bars, day, lambda s, p, d: [f"{s} news"], cap=3)
    assert [r["symbol"] for r in new] == ["BBB", "CCC", "AAA"]               # 0.06*5 > 0.09*2 > 0.10*1; DDD capped out
    assert new[0]["score"] > new[1]["score"] > new[2]["score"]
    assert all(r["relvol"] and r["abn"] is not None and "gap" in r for r in new)
    assert len(st["shocks"]) == 3 and len(st["trades"]) == 12


def test_spy_is_never_a_shock_and_penny_stocks_skipped():
    bars = universe_bars({"AAA": (0.10, 1.0)})
    bars["SPY"] = series(90, last=0.10)[1]
    day = bars["AAA"][-1][0]
    st = ns.load_state(path=ns.PATH.parent / "none.json")
    assert [r["symbol"] for r in ns.run(st, bars, day, lambda *a: ["h"])] == ["AAA"]
    cheap = {"PNY": [(d, 1.0, 1.0 * (1.05 if i == 89 else 1.0 + (0.01 if i % 2 else -0.01)), 1e6) for i, (d, *_r) in enumerate(bars["AAA"])]}
    assert ns.candidates({**cheap, "SPY": bars["SPY"]}, day, lambda *a: ["h"], ["PNY"]) == []


def test_cap_counts_shocks_already_recorded_for_the_day_so_reruns_add_nothing():
    bars = universe_bars({"AAA": (0.10, 1.0), "BBB": (0.06, 5.0), "CCC": (-0.09, 2.0)})
    day = bars["AAA"][-1][0]
    st = ns.load_state(path=ns.PATH.parent / "none.json")
    assert len(ns.run(st, bars, day, lambda *a: ["h"], cap=2)) == 2
    snap = copy.deepcopy(st)
    assert ns.run(st, bars, day, lambda *a: ["h"], cap=2) == [] and st == snap


def test_news_none_means_settle_only():
    bars = universe_bars({"AAA": (0.10, 1.0)})
    st = ns.load_state(path=ns.PATH.parent / "none.json")
    assert ns.run(st, bars, bars["AAA"][-1][0], None) == [] and st["shocks"] == []


# ---------- LLM review ----------
GOOD = {"catalyst": "earnings", "company_specific": True, "is_new": True, "sentiment": 2, "bull_case": "b", "bear_case": "r",
        "direction": "continue", "confidence": 0.7, "rationale": "why"}


def test_parse_review_valid_clamped_and_invalid():
    r = nr.parse_review({**GOOD, "sentiment": 5, "confidence": 1.4})
    assert r["status"] == "ok" and r["sentiment"] == 2 and r["confidence"] == 1.0 and r["direction"] == "continue"
    for bad in (None, {}, {**GOOD, "catalyst": "weather"}, {**GOOD, "direction": "up"}, {**GOOD, "confidence": "high"}):
        assert nr.parse_review(bad) is None


def sample_shock(sym="AAA", ret=0.08, **kw):
    return {"id": f"{sym}|2026-11-06", "symbol": sym, "date": "2026-11-06", "ret": ret, "vol": 0.01, "mult": 4.0, "relvol": 3.0,
            "gap": 0.02, "spy_ret": -0.01, "beta": 1.1, "abn": 0.09, "headlines": ["Acme raises guidance", "Analyst upgrades Acme"], **kw}


def test_prompt_has_numbers_headlines_and_keyword_hints():
    p = nr.build_prompt(sample_shock())
    assert "AAA" in p and "+8.0%" in p and "Acme raises guidance" in p and "RISK MANAGER" in p and "analyst" in p.lower()
    assert "guidance" in nr.keyword_tags(["Acme raises guidance"]) and nr.keyword_tags(["nothing here"]) == []


def test_review_one_failure_modes_never_raise():
    assert nr.review_one(sample_shock(), lambda *a, **k: None) == {"status": "no review"}
    assert nr.review_one(sample_shock(), lambda *a, **k: {"catalyst": "x"}) == {"status": "no review"}

    def boom(*a, **k):
        raise OSError("cli")
    assert nr.review_one(sample_shock(), boom) == {"status": "no review"}


def test_review_all_caps_calls_and_marks_the_rest():
    shocks = [sample_shock(f"S{i}") for i in range(20)]
    calls = []

    def ask(prompt, schema, timeout=None):
        calls.append(prompt)
        return GOOD
    out = nr.review_all(shocks, ask=ask, max_calls=15)
    assert len(calls) == 15 and sum(r["status"] == "ok" for r in out.values()) == 15 and len(out) == 20
    assert out["S19|2026-11-06"] == {"status": "no review"}


def test_review_all_bails_when_cli_is_dead_and_respects_time_budget():
    shocks = [sample_shock(f"S{i}") for i in range(12)]
    calls = []

    def dead(prompt, schema, timeout=None):
        calls.append(1)
        return None
    out = nr.review_all(shocks, ask=dead, workers=1)
    assert len(calls) == nr.BAIL_AFTER and all(r == {"status": "no review"} for r in out.values())
    t = iter(range(0, 10_000, 400))        # fake clock jumps 400 s per reading: budget (600 s) is gone after a couple of calls
    calls.clear()
    out = nr.review_all(shocks, ask=lambda *a, **k: calls.append(1) or GOOD, workers=1, clock=lambda: next(t))
    assert 0 < len(calls) < 12


# ---------- llm shadow rule ----------
def test_llm_rule_side_and_unclear_means_no_trade():
    for direction, ret, side in (("continue", 0.08, "long"), ("continue", -0.08, "short"), ("reverse", 0.08, "short"), ("reverse", -0.08, "long")):
        sh = sample_shock(ret=ret, review={"status": "ok", "direction": direction, "confidence": 0.6, "catalyst": "other"})
        llm = [t for t in ns.new_trades(sh) if t["rule"] == "llm"]
        assert {t["hold"] for t in llm} == {1, 5} and {t["side"] for t in llm} == {side}
    for review in ({"status": "ok", "direction": "unclear", "confidence": 0.5, "catalyst": "other"}, {"status": "no review"}, None):
        sh = sample_shock(**({"review": review} if review is not None else {}))
        assert len(ns.new_trades(sh)) == 4


def test_run_attaches_reviews_and_llm_trades_settle():
    bars = universe_bars({"AAA": (0.08, 3.0), "BBB": (-0.07, 3.0)})
    day = bars["AAA"][-1][0]
    st = ns.load_state(path=ns.PATH.parent / "none.json")
    rv = {"AAA|" + day: {"status": "ok", "direction": "reverse", "confidence": 0.8, "catalyst": "earnings"}}
    new = ns.run(st, bars, day, lambda *a: ["h"], lambda recs: rv)
    by = {r["symbol"]: r for r in new}
    assert by["AAA"]["review"]["direction"] == "reverse" and by["BBB"]["review"] == {"status": "no review"}
    llm = [t for t in st["trades"] if t["rule"] == "llm"]
    assert len(llm) == 2 and all(t["side"] == "short" and t["symbol"] == "AAA" for t in llm) and len(st["trades"]) == 10
    snap = copy.deepcopy(st)
    assert ns.run(st, bars, day, lambda *a: ["h"], lambda recs: 1 / 0) == [] and st == snap    # re-run: no reviews, no changes


def test_review_function_crash_cannot_lose_the_shock():
    bars = universe_bars({"AAA": (0.08, 3.0)})
    day = bars["AAA"][-1][0]
    st = ns.load_state(path=ns.PATH.parent / "none.json")
    new = ns.run(st, bars, day, lambda *a: ["h"], lambda recs: None)
    assert new[0]["review"] == {"status": "no review"} and len(st["trades"]) == 4


def test_breakdowns_by_rule_catalyst_and_confidence():
    def sh(sym, cat, conf, direction="continue"):
        return {"id": f"{sym}|2026-11-06", "symbol": sym, "date": "2026-11-06", "ret": 0.05,
                "review": {"status": "ok", "catalyst": cat, "confidence": conf, "direction": direction}}

    def tr(sym, rule, pnl, hold=5):
        return {"id": f"{sym}|{rule}|{hold}", "symbol": sym, "signal_date": "2026-11-06", "rule": rule, "hold": hold,
                "status": "closed", "pnl_pct": pnl, "exit_date": "2026-11-13"}
    st = {"started": "2026-11-06", "shocks": [sh("A", "earnings", 0.8), sh("B", "analyst_rating", 0.4), {"id": "C|2026-11-06", "symbol": "C", "date": "2026-11-06", "ret": 0.05}],
          "trades": [tr("A", "momentum", 2.0), tr("A", "llm", 2.0), tr("B", "momentum", -1.0), tr("B", "reversal", 1.0), tr("C", "momentum", 4.0)]}
    b = ns.breakdowns(st)
    assert b["rule"]["all shocks"]["momentum 5d"] == (3, pytest.approx(5 / 3))
    assert b["catalyst"]["earnings"]["llm 5d"] == (1, 2.0) and "no review" not in b["catalyst"]
    assert b["confidence"]["conf >=0.7"] == {"llm 5d": (1, 2.0), "momentum 5d": (1, 2.0)}
    assert b["confidence"]["conf <0.5"]["reversal 5d"] == (1, 1.0) and b["confidence"]["no review"]["momentum 5d"] == (1, 4.0)
    assert "By catalyst" in ns.breakdown_text(st) and "llm 5d" in ns.summary_line(st)
    assert ns.breakdown_text({"shocks": [], "trades": []}) == "No closed shadow trades yet."


def test_nightly_message_is_short_and_escaped():
    new = [sample_shock(f"S{i}", review={"status": "ok", "catalyst": "m_and_a", "direction": "reverse", "confidence": 0.65}) for i in range(12)]
    new[0]["headlines"] = ["A & B <x>"]
    m = ns.nightly_message(new, "2026-11-06", 517)
    lines = m.split("\n")
    assert len(lines) == 1 + 8 + 1 and lines[-1] == "+4 more" and "not traded" in lines[0] and "12 kept of ~517" in lines[0]
    assert "m and a · LLM reverse 0.65" in lines[1] and "abn +9.0%" in lines[1] and "3.0× vol" in lines[1]
    assert ns.shock_line(sample_shock(review={"status": "no review"})).endswith("no review")


# ---------- morning gappers ----------
def snap(prev, px, vol, day="2026-11-09"):
    return {"prevDailyBar": {"c": prev}, "latestTrade": {"p": px}, "dailyBar": {"t": f"{day}T05:00:00Z", "v": vol}}


def test_gap_rows_filters_and_orders():
    snaps = {"UP": snap(100, 107, 50_000), "DN": snap(100, 94, 20_000), "SMALL": snap(100, 102, 90_000), "NOVOL": snap(100, 110, 10),
             "OLD": snap(100, 110, 90_000, day="2026-11-06"), "PENNY": snap(2, 3, 90_000), "BAD": {"prevDailyBar": {"c": 1}}}
    assert [r["symbol"] for r in ng.gap_rows(snaps, "2026-11-09")] == ["UP", "DN"]
    assert ng.gap_rows(snaps, "2026-11-09")[1]["gap"] == pytest.approx(-0.06)


def test_gappers_need_news_in_16h_and_top_n():
    now = datetime(2026, 11, 9, 9, 15, tzinfo=NY)
    snaps = {f"G{i}": snap(100, 104 + i, 5000) for i in range(7)}
    snaps["NONEWS"] = snap(100, 130, 5000)
    wins = []

    def news(start, end):
        wins.append((start, end))
        return [art(f"head G{i}", [f"G{i}"], "2026-11-08T23:00:00Z") for i in range(7)] + [art("old", ["NONEWS"], "2026-11-08T10:00:00Z")]
    out = ng.gappers(list(snaps), now, lambda syms: snaps, news)
    assert [g["symbol"] for g in out] == ["G6", "G5", "G4", "G3", "G2"] and out[0]["headline"] == "head G6"
    assert wins[0][1] - wins[0][0] == timedelta(hours=16)


def test_gappers_never_raise_and_skip_news_call_without_gaps():
    now = datetime(2026, 11, 9, 9, 15, tzinfo=NY)

    def boom(*a):
        raise OSError("x")
    assert ng.gappers(["A"], now, boom, boom) == []
    assert ng.gappers(["A"], now, lambda s: {"A": snap(100, 101, 5000)}, boom) == []      # no gap -> news never fetched


def test_snapshots_batched_and_iex_fallback():
    calls = []

    def op(req, timeout=None):
        calls.append(req.full_url)
        if "feed=sip" in req.full_url:
            raise OSError("subscription does not permit")
        return Resp(json.dumps({"X": snap(1, 1, 1)}))
    out = ng.fetch_snapshots([f"S{i}" for i in range(250)], key="K", secret="S", urlopen=op)
    assert "X" in out and sum("feed=iex" in u for u in calls) == 3


def test_morning_text_and_card_show_gappers_and_survive_errors(monkeypatch):
    import briefing as bf
    import briefing_cards as bc
    d = {"kind": "morning", "day": date(2026, 11, 9), "flags": [], "waiting_candidates": [], "headlines": [],
         "accounts": {n: {"equity": 25000.0, "note": "", "last": (10.0, 0.001), "positions": [], "since": 0.0, "waiting": [],
                          "dd": 0.01, "kill": 0.2, "day_limit": 0.03, "risk": "r"} for n in ("long-term", "intraday")},
         "gappers": [{"symbol": "LITE", "gap": 0.119, "price": 602.0, "pm_vol": 54_000, "headline": "Bernstein adjusts target " * 5}]}
    t = bf.text_morning(d)
    assert "Gappers with news" in t and "LITE +11.9%" in t and "54K" in t
    assert bc.render(d)[:4] == b"\x89PNG"
    d["gappers"] = []
    assert "Gappers" not in bf.text_morning(d) and bc.render(d)[:4] == b"\x89PNG"
    import news_gappers
    monkeypatch.setattr(news_gappers, "gappers_now", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(bf, "_gather", lambda day: ({n: {"equity": 1.0, "last_equity": 1.0, "positions": []} for n in ("long-term", "intraday")}, {}))
    monkeypatch.setattr(bf.proposer_health, "briefing_warning", lambda: None)
    assert bf.gather_morning(datetime(2026, 11, 9, 14, 15, tzinfo=timezone.utc))["gappers"] == []


def test_workflows_have_claude_cli_keys_and_timeout():
    from pathlib import Path
    wf = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    n = (wf / "nightly.yml").read_text()
    assert "@anthropic-ai/claude-code" in n and "CLAUDE_CODE_OAUTH_TOKEN" in n and n.index("claude-code") < n.index("python news_shadow.py")
    assert "timeout-minutes: 25" in n
    assert "ALPACA_API_KEY" in (wf / "morning.yml").read_text()
