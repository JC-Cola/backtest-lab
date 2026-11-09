"""Telegram sender and the morning/evening briefings, on fixture data (no network)."""
import json
from datetime import date, datetime, timezone

import numpy as np
import pytest

import briefing as bf
import report as rp
import telegram as tg

NY = rp.NY
DAY = date(2026, 11, 4)
PREV = date(2026, 11, 3)


def utc(h, m=0, d=4):
    return datetime(2026, 11, d, h, m, tzinfo=timezone.utc)


# ---------- telegram ----------
def test_send_noop_without_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(tg.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not call the network"))
    assert tg.send("hi") is False


class Resp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_send_request_body(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    seen = []
    monkeypatch.setattr(tg.urllib.request, "urlopen", lambda req, timeout=None: (seen.append((req, timeout)), Resp())[1])
    assert tg.send("<b>hi</b>") is True
    req, timeout = seen[0]
    assert req.full_url == "https://api.telegram.org/botTOK/sendMessage" and timeout == 10
    body = json.loads(req.data)
    assert body == {"chat_id": "42", "text": "<b>hi</b>", "parse_mode": "HTML", "disable_web_page_preview": True}


def test_send_splits_long_text_and_never_raises(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    sent = []
    monkeypatch.setattr(tg.urllib.request, "urlopen", lambda req, timeout=None: (sent.append(json.loads(req.data)["text"]), Resp())[1])
    text = "\n".join(["x" * 90] * 100)             # 9,100 characters
    assert tg.send(text) is True
    assert len(sent) == 3 and all(len(s) <= 4000 for s in sent) and "\n".join(sent) == text

    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(tg.urllib.request, "urlopen", boom)
    assert tg.send("x") is False


def test_split_hard_cuts_a_single_huge_line():
    parts = tg.split("y" * 9000)
    assert [len(p) for p in parts] == [4000, 4000, 1000]


# ---------- gate ----------
def test_morning_window():
    assert bf.morning_window(utc(14, 15))            # 09:15 EST
    assert bf.morning_window(datetime(2026, 10, 1, 13, 15, tzinfo=timezone.utc))   # 09:15 EDT
    assert not bf.morning_window(utc(13, 15))        # 08:15 EST: the EDT cron firing in winter
    assert not bf.morning_window(utc(15, 0))         # 10:00 EST
    assert not bf.morning_window(utc(14, 45))        # 09:45 EST, too late


# ---------- helpers ----------
def test_last_session_and_drawdown():
    assert bf.last_session([25000.0], 25100.0) == pytest.approx((100.0, 100 / 25000))
    # newest history point equals last_equity: it is dropped, the one before is the base
    assert bf.last_session([25000.0, 25100.0], 25100.0)[0] == pytest.approx(100.0)
    assert bf.last_session([], None) is None
    assert bf.drawdown([25000.0, 26000.0], 24700.0) == pytest.approx(24700 / 26000 - 1)
    assert bf.drawdown([], 26000.0) == 0.0


def test_risk_line_distances():
    line = bf.risk_line("intraday", 24000.0, [25000.0], 24250.0)
    assert "DD -4.0%" in line and "8.0 pts to kill (-12%)" in line
    assert "last day -3.0%" in line and "0.0 pts to -3%" in line   # at the daily limit
    assert "FTMO ref" in line and "2.0 / 6.0 pts left" in line


NOISE = [{"time_ny": f"2026-11-03 {h}:00:20", "symbol": s, "event": "check", "decision": "flat"}
         for s in ("SPY", "QQQ") for h in (10, 11, 12, 14, 15)]          # 13:00 missing for both


def test_missed_checks():
    assert bf.missed_checks(PREV, NOISE) == ["QQQ 13:00", "SPY 13:00"]
    assert bf.missed_checks(PREV, []) == ["no intraday log rows at all"]


# ---------- morning ----------
def acct(eq, last, pos=(), orders=(), note=""):
    return {"ok": True, "equity": eq, "cash": 0, "last_equity": last, "positions": list(pos), "orders": list(orders), "note": note}


CAND = {"candidates": [{"account": "intraday", "description": "buy <dips> early", "status": "awaiting approval"},
                       {"account": "long-term", "description": "zzz-approved", "status": "approved"}]}


def test_morning_lines_and_flags():
    it = acct(24400, 24800, [{"symbol": "SPY", "qty": 3, "pl": -5.0}])
    txt = bf.build_morning(DAY, {"intraday": it}, {"intraday": [25000.0, 24800.0]}, NOISE, CAND, PREV, prev_report_exists=False)
    assert "<b>Morning 2026-11-04</b>" in txt
    assert "long-term" not in txt.lower() and "Waiting for the open" not in txt
    assert "last session -$200.00" in txt
    assert "since start -2.40%" in txt and "SPY 3 (-$5.00)" in txt
    assert "Intraday still holds 3 SPY" in txt
    assert "Missed intraday checks on 2026-11-03: QQQ 13:00; SPY 13:00" in txt
    assert "No nightly report found for 2026-11-03" in txt
    assert "1 awaiting approval" in txt and "buy &lt;dips&gt; early" in txt and "zzz-approved" not in txt
    assert len(txt) < 1800


def test_retired_longterm_candidates_are_not_offered():
    c = {"candidates": [{"account": "long-term", "description": "old", "status": "awaiting approval"},
                        {"account": "intraday", "description": "new", "status": "awaiting approval"}]}
    assert [x["description"] for x in bf.waiting_candidates(c)] == ["new"]


def test_morning_clean_day_has_no_flags():
    ok_noise = [{"time_ny": f"2026-11-03 {h}:00:20", "symbol": s, "event": "check", "decision": "flat"}
                for s in ("SPY", "QQQ") for h in (10, 11, 12, 13, 14, 15)]
    txt = bf.build_morning(DAY, {"intraday": acct(25000, 25000)}, {}, ok_noise, {"candidates": []}, PREV)
    assert "No flags." in txt and "⚠️" not in txt and "none awaiting approval" in txt


def test_snapshot_accounts_graceful_and_shown(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    b, c = tmp_path / "b.json", tmp_path / "c.json"
    b.write_text('{"equity": 25200.0, "last_equity": 25000.0, "positions": [{"symbol": "NVDA", "qty": 3, "unrealized_pl": 12.5}], '
                 '"calendar": [{"date": "2026-11-03", "equity": 25000.0}]}')
    c.write_text("not json")
    assert bf.snapshot_account(missing) is None and bf.snapshot_account(c) is None
    ex = bf.extra_accounts([("system-b", b), ("system-c", c), ("x", missing)])
    assert list(ex) == ["system-b"] and ex["system-b"]["positions"][0]["pl"] == 12.5
    assert bf.extra_accounts([("system-b", missing)]) == {}
    accts = {"intraday": acct(25040, 25000), "system-b": ex["system-b"]}
    d = bf.morning_data(DAY, accts, {"intraday": [25000.0], "system-b": ex["system-b"]["calendar_equity"]}, [], {}, PREV)
    assert d["accounts"]["system-b"]["kill"] is None and d["accounts"]["system-b"]["risk"] == ""
    txt = bf.text_morning(d)
    assert "System B</b> $25,200.00" in txt and "NVDA 3 (+$12.50)" in txt
    assert txt.count("DD ") == 1                       # the risk lines are the intraday account's only
    e = bf.evening_data(DAY, accts, {"intraday": [25000.0]}, [], {}, {"intraday": None}, NO_RES)
    assert e["accounts"]["system-b"]["trades"] == [] and "System B</b> $25,200.00" in bf.text_evening(e)


# ---------- evening ----------
def order(sym, side, q, px, t, oid=""):
    ts = datetime(2026, 11, 4, *t, tzinfo=NY)
    return {"id": oid, "symbol": sym, "side": side, "qty": q, "filled_qty": q, "price": px, "status": "filled", "submitted": ts, "filled": ts}


def test_round_trips_long_short_and_flip():
    fills = [order("SPY", "buy", 10, 100, (10, 0)), order("SPY", "sell", 10, 101, (11, 0)),
             order("QQQ", "sell", 5, 200, (10, 0)), order("QQQ", "buy", 5, 198, (12, 0)),
             order("IWM", "buy", 2, 10, (10, 0))]                                  # IWM still open: not a trip
    assert bf.round_trips(fills) == [("SPY", 10.0), ("QQQ", 10.0)]
    flip = [order("SPY", "buy", 4, 100, (10, 0)), order("SPY", "sell", 6, 101, (11, 0)), order("SPY", "buy", 2, 100, (12, 0))]
    assert bf.round_trips(flip) == [("SPY", 4.0), ("SPY", 2.0)]
    assert bf.best_worst([("A", 5.0), ("B", -2.0)]) == "Best A +$5.00 | Worst B -$2.00"
    assert bf.best_worst([("A", 5.0)]) == "Best A +$5.00" and bf.best_worst([]) is None


def window(n_bt=400, mean=0.0004, sd=0.006, seed=1):
    return np.random.default_rng(seed).normal(mean, sd, n_bt)


def test_backtest_verdicts():
    bt = window()
    assert "too early to tell (3 days)" in bf.backtest_line("intraday", 3, 0.5, bt)
    assert "too early to tell (1 day)" in bf.backtest_line("intraday", 1, 0.0, bt)
    assert "in line" in bf.backtest_line("intraday", 10, 0.004, bt)
    assert "below normal" in bf.backtest_line("intraday", 10, -0.2, bt)
    assert "above normal" in bf.backtest_line("intraday", 10, 0.2, bt)
    assert "backtest unavailable" in bf.backtest_line("intraday", 10, 0.01, None)
    w = bf.window_returns([0.01, 0.01, -0.01], 2)
    assert w == pytest.approx([1.01 * 1.01 - 1, 1.01 * 0.99 - 1]) and len(bf.window_returns([0.1], 2)) == 0


def test_live_stats():
    assert bf.live_stats([25000.0], 25000.0) == (0, 0.0)               # nothing has happened yet
    n, ret = bf.live_stats([25000.0, 25250.0], 25500.0)
    assert (n, round(ret, 4)) == (2, 0.02)
    assert bf.live_stats([25000.0, 25250.0], 25250.0)[0] == 1          # today's point already in the history


def test_research_today(tmp_path):
    j = tmp_path / "j.jsonl"
    rows = [{"ts": "2026-11-04T15:00:00+00:00", "status": "scored", "reward": 0.2},
            {"ts": "2026-11-04T16:00:00+00:00", "status": "scored", "reward": 0.5, "holdout": {"sharpe": 1}},
            {"ts": "2026-11-04T17:00:00+00:00", "status": "rejected", "reward": 0.0},
            {"ts": "2026-11-05T03:00:00+00:00", "status": "scored", "reward": 9},      # 22:00 NY on the 4th: counts
            {"ts": "2026-11-05T14:00:00+00:00", "status": "scored", "reward": 7}]      # next NY day
    j.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    s = bf.research_today(j, DAY)
    assert s == {"tested": 4, "scored": 3, "best": 9, "holdout": 1}
    assert bf.research_today(tmp_path / "none", DAY)["tested"] == 0
    assert "no ideas tested" in bf.research_line("X", bf.research_today(tmp_path / "none", DAY))


NO_RES = {"intraday": {"tested": 0, "scored": 0, "best": None, "holdout": 0}}


def test_evening_text():
    it_orders = [order("SPY", "buy", 16, 767.0, (12, 5), "a"), order("SPY", "sell", 16, 766.0, (15, 56), "b")]
    noise = [{"time_ny": "2026-11-04 12:00:20", "symbol": "SPY", "event": "check", "decision": "long", "price": "767", "upper": "766", "lower": "760", "vwap": "765", "order_id": "a"}]
    it = acct(24984, 25000, orders=it_orders)
    res = {"intraday": {"tested": 4, "scored": 2, "best": 0.31, "holdout": 0}}
    txt = bf.build_evening(DAY, {"intraday": it}, {"intraday": []}, noise, CAND, {"intraday": None}, res)
    assert "today -$16.00 (-0.06%)" in txt and "Long-term" not in txt and "Daily agent" not in txt
    assert "12:05 BUY 16 SPY @ $767.00 (long)" in txt and "15:56 SELL 16 SPY" in txt and "Best SPY -$16.00" in txt
    assert "Intraday: too early to tell (1 day)" in txt
    assert "Intraday agent: 4 tested, 2 scored, best score 0.310, 0 held-out shots" in txt and "Candidates awaiting approval: 1" in txt
    assert len(txt) < 1800


def test_evening_verdict_in_text_and_trade_overflow():
    many = [order("SPY", "buy", 1, 100.0, (10, i)) for i in range(8)]
    hist = [25000.0] + [25000.0 + 10 * i for i in range(1, 8)]            # 7 live days after the start
    txt = bf.build_evening(DAY, {"intraday": acct(25070, 25060, orders=many)}, {"intraday": hist}, [], {},
                           {"intraday": window(mean=0.0, sd=0.0002)}, {"intraday": bf.research_today("/nonexistent", DAY)})
    assert "+2 more" in txt
    assert "Intraday: 7 days +0.28% vs backtest" in txt and "above normal" in txt   # tight backtest: +0.28% is far above it


def test_workflows_wiring():
    wf = bf.ROOT / ".github" / "workflows"
    m = (wf / "morning.yml").read_text()
    assert "schedule:" not in m and "inputs.force != 'false'" in m and "workflow_dispatch:" in m and "group: backtest-lab-briefing" in m
    for k in ("ALPACA_INTRADAY_API_KEY", "ALPACA_INTRADAY_SECRET_KEY", "ALPACA_DTS_API_KEY", "ALPACA_TA_API_KEY",
              "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        assert f"{k}: ${{{{ secrets.{k} }}}}" in m
    assert "secrets.ALPACA_API_KEY" not in m          # the retired long-term account is not a dependency
    assert "cd src && python briefing.py morning" in m
    n = (wf / "nightly.yml").read_text()
    assert n.rstrip().endswith("if: always()\n        run: cd src && python briefing.py evening")
    assert "TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}" in n and "TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}" in n
