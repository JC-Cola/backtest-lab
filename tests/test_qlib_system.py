"""qlib paper system: target portfolio, whole shares, caps, sell-before-buy, paper guard, idempotent nights. Fakes only (no qlib needed)."""
import datetime as dt
from types import SimpleNamespace

import pytest

import system_qlib as Q

NIGHT = dt.datetime(2026, 11, 13, 22, 0)      # 17:00 New York (EST), a Friday


def names(n, prefix="S"):
    return ["%s%02d" % (prefix, i) for i in range(n)]


# ---------------------------------------------------------------- top-k dropout (qlib's rule)

def test_first_night_buys_the_top_k():
    scores = {s: 100 - i for i, s in enumerate(names(80))}
    t = Q.topk_dropout(scores, [], 50, 5)
    assert t == names(50)


def test_dropout_replaces_the_worst_five_with_the_best_non_holdings():
    scores = {s: 100 - i for i, s in enumerate(names(80))}
    held = names(50)
    scores["S45"], scores["S46"], scores["S47"], scores["S48"], scores["S49"] = -1, -2, -3, -4, -5   # the held tail got worse
    t = Q.topk_dropout(scores, held, 50, 5)
    assert len(t) == 50 and set(names(45)) <= set(t)
    assert set(t) - set(held) == set(names(55)[50:])           # the five best non-holdings S50..S54
    assert not {"S45", "S46", "S47", "S48", "S49"} & set(t)


def test_dropout_keeps_a_good_holding_even_if_not_top_k_and_never_sells_more_than_n_drop():
    scores = {s: 100 - i for i, s in enumerate(names(80))}
    held = names(50)[5:] + ["S60"]                              # S60 is rank 61 but held
    t = Q.topk_dropout(scores, held, 50, 5)
    assert len(t) == 50 and len(set(held) - set(t)) <= 5
    assert "S60" not in t                                       # worst of the held, replaced by the best non-holding


def test_missing_score_ranks_last_and_gets_sold():
    scores = {s: 100 - i for i, s in enumerate(names(60))}
    held = names(49) + ["GONE"]
    t = Q.topk_dropout(scores, held, 50, 5)
    assert "GONE" not in t and len(t) == 50


# ---------------------------------------------------------------- shares, caps, diff

def test_whole_shares_equal_weight_and_cap():
    px = {"A": 10.0, "B": 333.0, "C": 900.0, "D": 1500.0}
    q = Q.target_shares(["A", "B", "C", "D"], px, 25000.0, 50, 0.04)
    assert q["A"] == 50                       # $500 / $10
    assert q["B"] == 1                        # floor(500/333) = 1
    assert q["C"] == 1                        # floors to 0 but $900 <= 4% of 25k = $1000: one share
    assert "D" not in q                       # $1500 > the $1000 cap: not affordable
    assert all(q[s] * px[s] <= 0.04 * 25000 for s in q)


def test_few_names_still_use_one_over_topk_not_more_than_the_cap():
    q = Q.target_shares(["A", "B"], {"A": 10.0, "B": 10.0}, 25000.0, 50, 0.04)
    assert q == {"A": 50, "B": 50}            # 2% each, not 50% each; the rest stays cash


def test_total_never_exceeds_equity():
    px = {s: 990.0 for s in names(40)}        # every name bumps to one $990 share = $39,600 > $25,000
    q = Q.target_shares(list(px), px, 25000.0, 50, 0.04)
    assert sum(q[s] * px[s] for s in q) <= 25000.0


def test_diff_sells_before_buys_skips_small_and_exits_fully():
    px = {"OLD": 40.0, "UP": 50.0, "NEW": 20.0, "TINY": 30.0, "KEEP": 99.0, "DUST": 5.0}
    held = {"OLD": 10, "UP": 5, "KEEP": 5, "DUST": 3}
    target = {"UP": 8, "NEW": 25, "TINY": 2, "KEEP": 6}
    o = Q.diff_orders(target, held, px, 100.0)
    sides = [x["side"] for x in o]
    assert sides == sorted(sides, key=lambda s: s != "sell")                     # every sell precedes every buy
    by = {x["symbol"]: x for x in o}
    assert by["OLD"]["qty"] == 10 and by["DUST"]["qty"] == 3                      # leaving names sold in full, even $15
    assert by["NEW"]["side"] == "buy" and by["NEW"]["qty"] == 25
    assert "TINY" not in by                                                       # $60 new buy < $100
    assert "KEEP" not in by                                                       # +1 share = $99 < $100
    assert by["UP"]["qty"] == 3


def test_diff_threshold_is_a_floor_not_a_ceiling():
    o = Q.diff_orders({"KEEP": 6}, {"KEEP": 5}, {"KEEP": 100.0}, 100.0)
    assert o and o[0]["qty"] == 1                                                  # exactly $100 trades
    assert Q.diff_orders({"KEEP": 6}, {"KEEP": 5}, {"KEEP": 99.0}, 100.0) == []


def test_telegram_line_format():
    o = [{"side": "buy", "symbol": "AAPL"}, {"side": "buy", "symbol": "MSFT"}, {"side": "sell", "symbol": "XOM"}]
    assert Q.telegram_line(o, 48) == "qlib · rebalance: +AAPL +MSFT −XOM · 48 holdings"
    assert Q.telegram_line([], 50) == "qlib · no changes · 50 holdings"
    long = [{"side": "buy", "symbol": "S%d" % i} for i in range(30)]
    assert "(+16 more)" in Q.telegram_line(long, 50)


# ---------------------------------------------------------------- the account (fake) and a full night

class FakeClient:
    def __init__(self, equity=25000.0, positions=None, base="https://paper-api.alpaca.markets", number="PA3ABC"):
        self._base_url = SimpleNamespace(value=base)           # alpaca-py keeps a BaseURL enum
        self.equity, self.number = equity, number
        self.pos = {k: dict(qty=v, px=10.0) for k, v in (positions or {}).items()}
        self.submitted, self.cancelled, self.open_orders = [], [], []

    def get_account(self):
        return SimpleNamespace(equity=str(self.equity), cash=str(self.equity), account_number=self.number)

    def get_all_positions(self):
        return [SimpleNamespace(symbol=k, qty=str(v["qty"]), current_price=str(v["px"])) for k, v in self.pos.items()]

    def get_calendar(self, req):
        return [SimpleNamespace(date=dt.date(2026, 11, 13))] if req.start == dt.date(2026, 11, 13) else []

    def get_orders(self, req):
        return self.open_orders

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)

    def submit_order(self, req):
        if any(o.client_order_id == req.client_order_id for o in self.submitted):
            raise RuntimeError("client_order_id must be unique")
        o = SimpleNamespace(id="o%d" % len(self.submitted), client_order_id=req.client_order_id, symbol=req.symbol,
                            side=req.side.value, qty=req.qty, tif=req.time_in_force.value, status=SimpleNamespace(value="accepted"))
        self.submitted.append(o)
        return o


def scorer_for(n=70, price=20.0):
    scores = {s: 100 - i for i, s in enumerate(names(n))}
    return lambda day: {"scores": dict(scores), "prices": {s: price for s in scores}, "meta": {"model": {"fitted_on": "2026-11-01"}, "data": {"symbols": n}}}


@pytest.fixture
def snap(monkeypatch):
    import alpaca_paper
    monkeypatch.setattr(alpaca_paper, "snapshot", lambda *a, **k: {"equity": 25000.0, "cash": 24000.0, "last_equity": 24900.0, "positions": [],
                                                                    "orders": [], "calendar": [], "history": {}, "balance_history": {"rows": []}})


def test_paper_guard_refuses_live_url_and_non_paper_account():
    with pytest.raises(Q.Refused):
        Q.assert_paper(FakeClient(base="https://api.alpaca.markets"))
    with pytest.raises(Q.Refused):
        Q.assert_paper(FakeClient(number="123456789"))
    with pytest.raises(Q.Refused):
        Q.run_once(Q.new_state(), FakeClient(base="https://api.alpaca.markets"), NIGHT, scorer_for())
    assert Q.assert_paper(FakeClient()).account_number == "PA3ABC"


def test_guard_accepts_the_real_alpaca_paper_client(monkeypatch):
    from alpaca.trading.client import TradingClient
    c = TradingClient("PKTESTKEY000000000000", "testsecret", paper=True)
    raw = getattr(c, "_base_url")
    assert Q.PAPER_HOST in str(getattr(raw, "value", raw))


def test_missing_keys_refuse(monkeypatch):
    monkeypatch.delenv("ALPACA_DTS_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_DTS_SECRET_KEY", raising=False)
    with pytest.raises(Q.Refused):
        Q.make_client()


def test_first_night_places_50_buys_as_day_market_orders(snap, tmp_path):
    c, sent = FakeClient(), []
    st = Q.new_state()
    out = Q.run_once(st, c, NIGHT, scorer_for(), notify=lambda t: sent.append(t) or True, path=tmp_path / "s.json")
    assert out["status"] == "ok" and len(c.submitted) == 50
    assert {o.side for o in c.submitted} == {"buy"} and {o.tif for o in c.submitted} == {"day"}
    assert all(o.qty == 25 for o in c.submitted)                                 # $500 at $20
    assert sent == ["qlib · rebalance: " + " ".join("+" + s for s in names(14)) + " … (+36 more) · 50 holdings"]
    assert len(st["picks"]) == 50 and st["picks"][0] == {"symbol": "S00", "score": 100.0, "rank": 1}
    assert st["system"] == st["label"] == "qlib" and st["source"] == "alpaca-paper" and st["rebalances"][0]["data_date"] == "2026-11-13"
    assert (tmp_path / "s.json").exists()


def test_next_night_sells_before_buys_and_only_changes_five(snap, tmp_path):
    pos = {s: 25 for s in names(50)}
    c = FakeClient(positions=pos)
    for p in c.pos.values():
        p["px"] = 20.0
    sc = scorer_for()
    base = sc(None)
    for s in names(50)[-5:]:
        base["scores"][s] = -10.0
    out = Q.run_once(Q.new_state(), c, NIGHT, lambda d: base, path=tmp_path / "s.json")
    sides = [o.side for o in c.submitted]
    assert sides == ["sell"] * 5 + ["buy"] * 5
    assert {o.symbol for o in c.submitted if o.side == "sell"} == set(names(50)[-5:])
    assert {o.symbol for o in c.submitted if o.side == "buy"} == set(names(55)[50:])
    assert out["holdings"] == 50


def test_rerun_the_same_night_is_a_noop(snap, tmp_path):
    c, st = FakeClient(), Q.new_state()
    Q.run_once(st, c, NIGHT, scorer_for(), path=tmp_path / "s.json")
    n = len(c.submitted)
    again = Q.run_once(st, c, NIGHT + dt.timedelta(minutes=30), scorer_for(), path=tmp_path / "s.json")
    assert again["status"] == "already done" and len(c.submitted) == n
    st2 = Q.load(tmp_path / "s.json")                                            # even from a fresh process: state on disk
    again = Q.run_once(st2, c, NIGHT, scorer_for(), path=tmp_path / "s.json")
    assert again["status"] == "already done" and len(c.submitted) == n


def test_duplicate_client_order_id_is_rejected_by_the_broker_and_recorded(snap, tmp_path):
    c = FakeClient()
    Q.submit(c, "2026-11-13", [{"symbol": "A", "side": "buy", "qty": 1, "usd": 1}])
    sent = Q.submit(c, "2026-11-13", [{"symbol": "A", "side": "buy", "qty": 1, "usd": 1}])
    assert sent[0]["status"] == "error" and len(c.submitted) == 1


def test_dry_run_places_nothing_and_saves_nothing(snap, tmp_path):
    c = FakeClient()
    out = Q.run_once(Q.new_state(), c, NIGHT, scorer_for(), path=tmp_path / "s.json", dry_run=True)
    assert out["status"] == "dry-run" and len(out["orders"]) == 50 and not c.submitted and not (tmp_path / "s.json").exists()


def test_holiday_and_early_runs_do_nothing(snap, tmp_path):
    c = FakeClient()
    assert Q.run_once(Q.new_state(), c, dt.datetime(2026, 11, 14, 22, 0), scorer_for(), path=tmp_path / "s.json")["status"] == "closed"
    early = Q.run_once(Q.new_state(), c, dt.datetime(2026, 11, 13, 19, 0), scorer_for(), path=tmp_path / "s.json")   # 14:00 NY
    assert early["status"].startswith("too early") and not c.submitted


def test_holdings_that_are_too_expensive_or_unscored_are_sold(snap, tmp_path):
    c = FakeClient(positions={"BIG": 1, "GONE": 4})
    c.pos["BIG"]["px"] = 3000.0
    c.pos["GONE"]["px"] = 10.0
    Q.run_once(Q.new_state(), c, NIGHT, scorer_for(), path=tmp_path / "s.json")
    sells = {o.symbol: o.qty for o in c.submitted if o.side == "sell"}
    assert sells == {"BIG": 1, "GONE": 4}
    assert [o.side for o in c.submitted][:2] == ["sell", "sell"]


def test_order_errors_are_counted_in_the_message(snap, tmp_path):
    c = FakeClient()
    c.submit_order = lambda req: (_ for _ in ()).throw(RuntimeError("insufficient buying power"))
    sent = []
    Q.run_once(Q.new_state(), c, NIGHT, scorer_for(), notify=sent.append, path=tmp_path / "s.json")
    assert sent[0].endswith("50 order errors")


def test_save_handles_pandas_timestamps(tmp_path):
    import json
    import pandas as pd
    import system_qlib
    p = tmp_path / "s.json"
    system_qlib.save({"generated": pd.Timestamp("2026-11-20 22:03"), "picks": [{"date": pd.Timestamp("2026-11-20")}]}, p)
    assert json.loads(p.read_text())["generated"].startswith("2026-11-20")
