"""Finnhub client, earnings guard, briefing earnings line, news-shock catalyst fields, study-18 helpers. No network."""
import io
import json
import urllib.error

import pytest

import briefing as bf
import earnings18 as E
import finnhub_client as F
import ta_broker as TB
import ta_ledger as L


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(*answers, calls=None):
    """urlopen fake: each answer is a payload (dict/list) or an int HTTP status; the last one repeats."""
    seq = list(answers)

    def f(req, timeout=None):
        if calls is not None:
            calls.append(req.full_url)
        a = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(a, int):
            raise urllib.error.HTTPError(req.full_url, a, "x", {}, io.BytesIO(b'{"error":"You don\'t have access to this resource."}'))
        return Resp(json.dumps(a).encode())
    return f


class Lim:
    def wait(self):
        pass


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-token")


def kw(tmp_path, *answers, calls=None, now=lambda: 1000.0, sleeps=None):
    return dict(urlopen=opener(*answers, calls=calls), cache_dir=tmp_path, now=now, limiter=Lim(),
               sleep=(sleeps.append if sleeps is not None else lambda s: None))


# ---------- client ----------
def test_missing_key_returns_empty_and_never_calls(monkeypatch, tmp_path):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    calls = []
    k = kw(tmp_path, [], calls=calls)
    assert F.earnings_calendar("2026-11-16", "2026-11-17", **k) == [] and F.earnings_surprises("A", **k) == []
    assert F.recommendation_trends("A", **k) == [] and F.insider_transactions("A", **k) == []
    assert F.company_news("A", "2026-11-01", "2026-11-02", **k) == [] and F.news_sentiment("A", **k) is None and calls == []


def test_token_sent_and_parsed(key, tmp_path):
    calls = []
    rows = F.earnings_calendar("2026-11-16", "2026-11-17", **kw(tmp_path, {"earningsCalendar": [{"symbol": "NVDA"}]}, calls=calls))
    assert rows == [{"symbol": "NVDA"}] and "token=test-token" in calls[0] and "calendar/earnings" in calls[0]


def test_cache_hit_expiry(key, tmp_path):
    calls = []
    t = [1000.0]
    k = kw(tmp_path, [{"period": "2026-09-30"}], calls=calls, now=lambda: t[0])
    F.earnings_surprises("AAPL", **k)
    F.earnings_surprises("AAPL", **k)
    assert len(calls) == 1                      # second answer from disk
    t[0] += F.TTL["stock/earnings"] + 1
    F.earnings_surprises("AAPL", **k)
    assert len(calls) == 2                      # expired
    F.earnings_surprises("MSFT", **k)
    assert len(calls) == 3                      # other symbol = other cache entry


def test_403_returns_empty_and_is_cached(key, tmp_path):
    calls = []
    k = kw(tmp_path, 403, calls=calls)
    assert F.news_sentiment("AAPL", **k) is None and F.news_sentiment("AAPL", **k) is None
    assert len(calls) == 1                      # the denial is remembered, no second call


def test_error_body_without_access_is_empty(key, tmp_path):
    assert F.news_sentiment("AAPL", **kw(tmp_path, {"error": "You don't have access to this resource."})) is None


def test_429_backoff_then_success(key, tmp_path):
    sleeps = []
    assert F.recommendation_trends("A", **kw(tmp_path, 429, 429, [{"period": "2026-11-01"}], sleeps=sleeps)) == [{"period": "2026-11-01"}]
    assert sleeps == [2.0, 4.0]


def test_429_forever_and_other_errors_never_raise(key, tmp_path):
    assert F.recommendation_trends("A", **kw(tmp_path, 429)) == []
    assert F.recommendation_trends("B", **kw(tmp_path, 500)) == []


def test_rate_limiter_blocks_the_51st_call():
    t, slept = [0.0], []
    lim = F._Limiter(3, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    for _ in range(3):
        lim.wait()
    assert slept == []
    lim.wait()
    assert len(slept) == 1 and slept[0] == pytest.approx(60.05)
    t[0] = 1000.0
    lim.wait()
    assert len(slept) == 1                      # window slid: no wait


def test_rec_change_and_latest_surprise():
    tr = [{"period": "2026-10-01", "strongBuy": 10, "buy": 10, "hold": 0, "sell": 0, "strongSell": 0},
          {"period": "2026-11-01", "strongBuy": 5, "buy": 10, "hold": 5, "sell": 0, "strongSell": 0}]
    assert F.rec_change(tr)["delta"] == pytest.approx(-0.5) and F.rec_change(tr[:1]) is None and F.rec_change([]) is None
    rows = [{"period": "2026-06-30", "actual": 1, "estimate": 1, "surprisePercent": 2.0},
            {"period": "2026-09-30", "actual": 2, "estimate": 1, "surprisePercent": 100.0}]
    assert F.latest_surprise(rows)["surprise_pct"] == 100.0 and F.latest_surprise(rows, "2026-08-01")["period"] == "2026-06-30"
    assert F.latest_surprise([], None) is None


# ---------- earnings guard ----------
def night(*pairs, day="2026-11-16"):
    return [L.make_decision(day, s, r) for s, r in pairs]


CAL = [{"symbol": "NVDA", "date": "2026-11-18", "hour": "amc"}, {"symbol": "AAPL", "date": "2026-11-30", "hour": "bmo"},
       {"symbol": "TSLA", "date": "2026-11-16", "hour": "bmo"}, {"symbol": "AMD", "date": "2026-11-16", "hour": "amc"}]


def test_earnings_blocks_window_and_disabled():
    cal = lambda a, b: CAL
    b = TB.earnings_blocks(["NVDA", "AAPL", "TSLA", "AMD", "SPY"], "2026-11-16", 2, cal)
    assert b == {"NVDA": "2026-11-18", "AMD": "2026-11-16"}      # AAPL too far, TSLA already reported this morning
    assert TB.earnings_blocks(["NVDA"], "2026-11-16", 0, cal) == {}
    assert TB.earnings_blocks(["NVDA"], "2026-11-16", 1, cal) == {}   # only the 17th counts then
    assert TB.earnings_blocks(["NVDA"], "2026-11-16", 2, lambda a, b: (_ for _ in ()).throw(RuntimeError("x"))) == {}


def test_next_sessions_skip_weekend_and_holiday():
    assert TB.next_sessions("2026-11-25", 2) == ["2026-11-27", "2026-11-30"]   # Thanksgiving 11-26
    assert TB.next_sessions("2026-11-20", 2) == ["2026-11-23", "2026-11-24"]


def test_plan_orders_skips_new_position_only():
    prices = {"NVDA": 200.0, "SPY": 500.0}
    ew = {"NVDA": "2026-11-18"}
    o = TB.plan_orders(night(("NVDA", "Buy"), ("SPY", "Buy")), [], {}, prices, 25000, earnings=ew)
    assert {"symbol": "NVDA", "side": None, "qty": 0, "note": "skipped: earnings on 2026-11-18"} in o
    assert {"symbol": "SPY", "side": "buy", "qty": 10.0, "note": "open"} in o
    short = TB.plan_orders(night(("NVDA", "Sell")), [], {}, prices, 25000, earnings=ew)
    assert short[0]["note"] == "skipped: earnings on 2026-11-18"
    # disabled / not in calendar: normal order
    assert TB.plan_orders(night(("NVDA", "Buy")), [], {}, prices, 25000, earnings={})[0]["side"] == "buy"
    # held position: resize and exit still go through
    hist = [L.make_decision("2026-11-13", "NVDA", "Overweight")]
    assert TB.plan_orders(night(("NVDA", "Buy")), hist, {"NVDA": 10.0}, prices, 25000, earnings=ew)[0]["note"] == "resize"
    ex = [L.make_decision("2026-11-16", "NVDA", "Exit", "x", source="rotation")]
    assert TB.plan_orders(ex, [], {"NVDA": 5.0}, prices, 25000, earnings=ew) == [{"symbol": "NVDA", "side": "sell", "qty": 5.0, "note": "exit"}]


def test_skip_reaches_the_telegram_message():
    r = [{"symbol": "NVDA", "rating": "Buy", "rationale": "x", "horizon": None, "not_executed": ["skipped: earnings on 2026-11-18"]}]
    assert "NVDA: skipped: earnings on 2026-11-18" in L.nightly_message("2026-11-16", r)


# ---------- briefing line ----------
def test_briefing_earnings_line(monkeypatch):
    cal = lambda a, b: [{"symbol": "NVDA", "date": "2026-11-16", "hour": "amc"}, {"symbol": "AAPL", "date": "2026-11-17", "hour": "bmo"},
                        {"symbol": "ZZZ", "date": "2026-11-17", "hour": "bmo"}, {"symbol": "SPY", "date": "2026-11-20"}]
    items = bf.earnings_items(["NVDA", "AAPL", "SPY"], "2026-11-16", cal, tomorrow="2026-11-17")
    assert [(i["symbol"], i["when"]) for i in items] == [("NVDA", "today"), ("AAPL", "tomorrow")]
    assert bf.earnings_text(items) == "Earnings: today NVDA (after close) · tomorrow AAPL (before open)"
    assert bf.earnings_text([]) == ""
    assert bf.earnings_items(["NVDA"], "2026-11-16", lambda a, b: (_ for _ in ()).throw(RuntimeError), tomorrow="2026-11-17") == []


def test_morning_text_and_card_carry_the_line():
    import briefing_cards as bc
    from test_briefing import acct
    d = bf.morning_data(__import__("datetime").date(2026, 11, 16), {"intraday": acct(25000, 25000, [])}, {"intraday": [25000.0]}, [], [])
    assert "Earnings" not in bf.text_morning(d)
    d["earnings"] = [{"symbol": "NVDA", "when": "today", "hour": "amc"}]
    assert "Earnings: today NVDA (after close)" in bf.text_morning(d)
    assert bc.render(d)[:8] == b"\x89PNG\r\n\x1a\n"


# ---------- news shock catalyst ----------
def test_catalyst_fields_optional_and_never_block(monkeypatch):
    import news_shadow as NS
    assert NS.finnhub_context("AAPL", "2026-11-16", recs=lambda s: [], surprises=lambda s: []) == {}
    ctx = NS.finnhub_context("AAPL", "2026-11-16",
                             recs=lambda s: [{"period": "2026-10-01", "strongBuy": 1, "buy": 1}, {"period": "2026-11-01", "strongBuy": 0, "buy": 2}],
                             surprises=lambda s: [{"period": "2026-09-30", "actual": 2, "estimate": 1, "surprisePercent": 10.0}])
    assert ctx["surprise_pct"] == 10.0 and ctx["rec_delta"] == pytest.approx(-0.5)
    boom = lambda s: (_ for _ in ()).throw(RuntimeError("x"))
    assert NS.finnhub_context("AAPL", "2026-11-16", recs=boom, surprises=boom) == {}


# ---------- study 18 ----------
def mk_bars(n=120, spike=None):
    import datetime as dt
    d0 = dt.date(2026, 1, 1)
    out = []
    for i in range(n):
        v = 1000.0 * (3 if spike == i else 1)
        out.append(((d0 + dt.timedelta(days=i)).isoformat(), 100.0 + i * 0.1, 100.0 + i * 0.1, v))
    return out


def test_reaction_day_no_lookahead_and_window():
    bars = mk_bars(spike=60)
    assert E.reaction_day(bars, "2026-03-31") == 60 or E.reaction_day(bars, "2026-03-31") is None
    assert E.reaction_day(mk_bars(), "2026-03-31") is None            # no volume spike: dropped
    assert E.reaction_day(mk_bars(spike=15), "2026-03-31") is None    # spike outside the window


def test_window_return_and_drop_counts():
    bars = mk_bars()
    assert E.window_return(bars, 10, 5) == pytest.approx(bars[16][1] / bars[11][1] - 1)
    assert E.window_return(bars, 118, 5) is None
    ev, dropped = E.build_events({"A": [{"period": "2026-03-31", "estimate": 1, "surprisePercent": 5.0},
                                         {"period": "2026-03-31", "estimate": 0.01, "surprisePercent": 5.0},
                                         {"period": "2026-03-31", "estimate": 1, "surprisePercent": None}]}, {"A": mk_bars(spike=60)})
    assert dropped["small_estimate"] == 1 and dropped["no_surprise"] == 1 and len(ev) + dropped["no_reaction_day"] == 1


def test_analyse_marks_descriptive_and_too_few():
    assert E.analyse([], {}, [])["status"] == "too few events"
    bars = {f"S{k}": mk_bars() for k in range(60)}
    ev = [{"symbol": f"S{k}", "period": "2026-03-31", "surprise": float(k), "react": mk_bars()[20 + k % 5][0], "i": 20 + k % 5} for k in range(60)]
    r = E.analyse(ev, bars, mk_bars(), n_sims=200)
    assert "descriptive" in r["verdict"] and set(r["holds"]) == {"5", "20", "60"} and r["cutoffs"]["p90"] > r["cutoffs"]["p10"]


def test_review_prompt_includes_context_only_when_present():
    import news_review as NR
    sh = {"symbol": "A", "date": "2026-11-16", "ret": -0.05, "mult": 3.0, "headlines": ["h"]}
    assert "EPS surprise" not in NR.build_prompt(sh) and "Analyst consensus" not in NR.build_prompt(sh)
    p = NR.build_prompt({**sh, "rec_delta": -0.3, "rec_score": 1.0, "surprise_pct": -4.2, "surprise_period": "2026-09-30"})
    assert "EPS surprise (quarter ending 2026-09-30): -4.2%" in p and "change vs the previous month -0.30" in p
