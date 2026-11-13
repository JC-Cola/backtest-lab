"""System C trading plans: extraction validation, sizing, stops/targets, the 5-minute monitor. Fakes only: no network, no orders."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import ta_broker as TB
import ta_ledger as L
import ta_monitor as M
import ta_plan as P

NBIS = ("Initiate a 0.5x position in NBIS at current levels near $128.64 to capture the oversold bounce. Limit downside with a "
        "stop-loss at $110.00, acknowledging the high Average True Range, and look to scale to a full 1.0x position only if the "
        "stock sustains support above $118.94. Monitor quarterly CapEx efficiency as the primary indicator for long-term thesis viability.")
NVDA = ("Initiate a long position in NVDA using a tiered entry strategy to capture the current valuation dislocation while respecting "
        "extreme volatility. Execute an initial 0.5x tranche immediately to capitalize on the oversold RSI, deploying the remaining 1.0x "
        "allocation only upon price stabilization above $175 or consolidation near $165. Maintain a strict stop-loss at $146.49 to manage "
        "downside risk associated with the current leadership uncertainty.")
GPN = ("Initiate an Overweight position, capping exposure at a 0.5x standard allocation to manage the stock's high volatility. Scale into "
       "the position near current levels ($87.09) to capitalize on the mispriced forward valuation, while maintaining a firm risk-off "
       "trigger below the 200-day SMA ($77.63).")
QQQ = ("Maintain current exposure while awaiting a clearer technical breakout. Do not initiate new positions until QQQ confirms a sustained "
       "move above the 50-day SMA ($718.46) to signal renewed bullish momentum, or prepares to reduce risk should the 200-day SMA ($680.93) fail to hold.")


def raw(**k):
    base = {"direction": "long", "initial_size_x": 1.0, "stop": {"price": None, "type": "none", "sma_days": None}, "take_profit": [],
            "scale_in": [], "exit_conditions": [], "time_horizon": None, "non_price_notes": []}
    base.update(k)
    return base


RAW = {
    "NBIS": raw(initial_size_x=0.5, stop={"price": 110.0, "type": "price", "sma_days": None},
                scale_in=[{"condition": "above", "price": 118.94, "size_x": 0.5, "valid_days": None}],
                non_price_notes=["Monitor quarterly CapEx efficiency"]),
    "NVDA": raw(initial_size_x=0.5, stop={"price": 146.49, "type": "price", "sma_days": None},
                scale_in=[{"condition": "above", "price": 175, "size_x": 1.0, "valid_days": None},
                          {"condition": "near", "price": 165, "size_x": 1.0, "valid_days": None}]),
    "GPN": raw(initial_size_x=0.5, stop={"price": 77.63, "type": "sma", "sma_days": 200}),
    "QQQ": raw(initial_size_x=1.0, direction="long", exit_conditions=[{"condition": "below", "price": 680.93}]),
}


# ---------- extraction validation ----------
def test_nbis_plan():
    p = P.validate(RAW["NBIS"], NBIS, "Overweight", 128.64)
    assert (p["initial_x"], p["stop"], p["side"], p["base"]) == (0.5, 110.0, "long", 0.1)
    assert p["scale_in"] == [{"condition": "above", "price": 118.94, "size_x": 0.5, "valid_days": 10}]
    assert p["notes"] == ["Monitor quarterly CapEx efficiency"] and p["not_executed"] == []
    assert P.plan_line("NBIS", p) == "NBIS · Overweight 0.5x · stop $110 · +0.5x if holds > $118.94 · note: Monitor quarterly CapEx efficiency"


def test_nvda_scale_in_clipped_to_slot():
    p = P.validate(RAW["NVDA"], NVDA, "Overweight", 175.2)
    assert p["initial_x"] == 0.5 and p["stop"] == 146.49
    assert [s["size_x"] for s in p["scale_in"]] == [0.5, 0.5]            # 1.0x asked, only 0.5x of the slot is left
    assert sum("clipped" in w for w in p["not_executed"]) == 2


def test_gpn_sma_stop_becomes_price_trigger():
    p = P.validate(RAW["GPN"], GPN, "Overweight", 87.09)
    assert p["stop"] == 77.63 and p["stop_type"] == "sma" and p["initial_x"] == 0.5 and p["scale_in"] == []


def test_hold_exit_condition_and_no_scale_in_for_hold():
    r = dict(RAW["QQQ"], scale_in=[{"condition": "above", "price": 718.46, "size_x": 1.0, "valid_days": None}])
    p = P.validate(r, QQQ, "Hold", 688.38, held="long")
    assert p["side"] == "long" and p["exits"] == [{"condition": "below", "price": 680.93}] and p["stop"] is None
    assert p["scale_in"] == [] and any("Hold has no slot" in w for w in p["not_executed"])
    assert P.plan_line("QQQ", p).startswith("QQQ · Hold · exit < $680.93")


def test_no_stop_is_reported():
    p = P.validate(raw(), "Buy a starter position now.", "Buy", 50.0)
    assert p["stop"] is None and "no stop in the paragraph" in p["not_executed"]


def test_stop_on_wrong_side_and_too_far():
    p = P.validate(raw(stop={"price": 140.0, "type": "price"}), "Stop-loss at $140.", "Buy", 128.0)
    assert p["stop"] is None and "wrong side" in p["not_executed"][-1]
    p = P.validate(raw(stop={"price": 60.0, "type": "price"}), "Stop-loss at $60.", "Buy", 128.0)
    assert p["stop"] is None and "25%" in p["not_executed"][-1]
    s = P.validate(raw(direction="short", stop={"price": 120.0, "type": "price"}), "Cover on a stop at $120.", "Sell", 128.0)
    assert s["side"] == "short" and s["stop"] is None                      # short stop must be ABOVE price
    s = P.validate(raw(direction="short", stop={"price": 140.0, "type": "price"}), "Cover on a stop at $140.", "Sell", 128.0)
    assert s["stop"] == 140.0


def test_hallucinated_price_dropped():
    r = raw(stop={"price": 99.0, "type": "price"}, scale_in=[{"condition": "above", "price": 150.0, "size_x": 0.5}],
            take_profit=[{"price": 200.0, "size_x": 1}], exit_conditions=[{"condition": "below", "price": 90.0}], initial_size_x=0.3)
    p = P.validate(r, "Buy near $128.64 with a stop-loss at $110.00 and a target of $150.", "Buy", 128.64)
    assert p["stop"] is None and p["exits"] == [] and p["initial_x"] == 1.0
    assert p["tp"] is None and p["scale_in"] == []                      # scale_in 150 is "in the text" only as a target: still 150 present
    # (150.0 appears in the text, so the scale-in survives price verification; its size is then clipped to the 0-room slot)
    assert any("unverified" in w for w in p["not_executed"])


def test_price_cross_check_tolerance():
    r, notes = P.verify_prices(raw(stop={"price": 110.005, "type": "price"}), "stop-loss at $110.00")
    assert r["stop"]["price"] == 110.005 and notes == []
    r, notes = P.verify_prices(raw(stop={"price": 110.5, "type": "price"}), "stop-loss at $110.00")
    assert r["stop"]["price"] is None and "unverified" in notes[0]


def test_extraction_failure_degrades_to_rating_only():
    def boom(*a):
        raise RuntimeError("429")
    p = P.build(NBIS, "Overweight", 128.64, None, extract=boom)
    assert p["stop"] is None and p["initial_x"] == 1.0 and "plan extraction failed" in p["not_executed"][0]


def test_gemini_extract_retries_and_parses():
    calls = []

    def post(url, body, headers):
        calls.append(body)
        if len(calls) == 1:
            import urllib.error
            raise urllib.error.HTTPError(url, 429, "limit", {}, None)
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(RAW["NBIS"])}]}}]}
    out = P.gemini_extract(NBIS, "Overweight", None, post=post, sleep=lambda s: None)
    assert out["initial_size_x"] == 0.5 and len(calls) == 2
    assert calls[0]["generationConfig"]["responseMimeType"] == "application/json" and "ONLY numbers" in calls[0]["contents"][0]["parts"][0]["text"]


# ---------- sizing ----------
def test_initial_size_scales_the_order():
    d = [L.make_decision("2026-11-12", "NBIS", "Overweight", "x")]
    full = TB.plan_orders(d, [], {}, {"NBIS": 125.0}, 25000)
    half = TB.plan_orders(d, [], {}, {"NBIS": 125.0}, 25000, sizes={"NBIS": 0.5})
    assert full[0]["qty"] == 20.0 and half[0]["qty"] == 10.0            # 10% vs 5% of $25,000


# ---------- fake broker ----------
class FakeBroker:
    def __init__(self, positions=None, equity=25000.0, open_=True):
        self.pos = dict(positions or {})
        self.px = {}
        self.orders, self.seq, self.log, self.open_ = {}, 0, [], open_
        self.equity = equity
        self.slow_cancel = 0

    def get_clock(self):
        return SimpleNamespace(is_open=self.open_)

    def get_account(self):
        return SimpleNamespace(equity=str(self.equity), account_number="PA1")

    def get_all_positions(self):
        return [SimpleNamespace(symbol=s, qty=str(abs(q)), side="long" if q > 0 else "short", current_price=str(self.px.get(s, 100.0)))
                for s, q in self.pos.items() if q]

    def get_orders(self, filter=None):
        return [o for o in self.orders.values() if o.status in ("new", "accepted")]

    def get_order_by_id(self, oid):
        return self.orders[oid]

    def get_order_by_client_id(self, cid):
        return next(o for o in self.orders.values() if o.client_order_id == cid)

    def cancel_order_by_id(self, oid):
        self.log.append(("cancel", oid))
        o = self.orders[oid]
        if o.status not in ("new", "accepted"):
            raise RuntimeError("not cancelable")
        o.status = "canceled"
        for leg in getattr(o, "_legs", []):
            leg.status = "canceled"

    def submit_order(self, req):
        live = [o for o in self.get_orders() if o.symbol == req.symbol and str(req.side.value) == o.side]
        reserved = sum(float(o.qty) for o in live)
        if str(req.side.value) == "sell" and reserved + float(req.qty) > self.pos.get(req.symbol, 0):
            raise RuntimeError("insufficient qty available for order (held_for_orders)")
        cid = req.client_order_id
        if any(o.client_order_id == cid for o in self.orders.values()):
            raise RuntimeError("client_order_id must be unique")
        self.seq += 1
        typ = req.type.value if hasattr(req, "type") and req.type else "market"
        o = SimpleNamespace(id=f"o{self.seq}", symbol=req.symbol, side=str(req.side.value), qty=str(req.qty), order_type=typ, status="new",
                            client_order_id=cid, stop_price=getattr(req, "stop_price", None), limit_price=getattr(req, "limit_price", None),
                            filled_qty="0", order_class=str(getattr(getattr(req, "order_class", None), "value", "")), tif=str(req.time_in_force.value))
        self.orders[o.id] = o
        self.log.append(("submit", typ, req.symbol, o.side, req.qty, req.stop_loss.stop_price if o.order_class == "oco" else o.stop_price, o.limit_price, o.order_class, o.tif))
        if o.order_class == "oco":   # flat listing: the pair appears as a stop leg + a limit leg
            o.order_type, o.stop_price = "limit", None
            self.seq += 1
            leg = SimpleNamespace(**{**vars(o), "id": f"o{self.seq}", "order_type": "stop", "stop_price": req.stop_loss.stop_price,
                                     "limit_price": None, "client_order_id": None})
            self.orders[leg.id] = leg
            o._legs = [leg]
        if typ == "market":
            o.status, o.filled_qty = "filled", str(req.qty)
            self.pos[req.symbol] = self.pos.get(req.symbol, 0) + (float(req.qty) if o.side == "buy" else -float(req.qty))
        return o


NOW = datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)


def bar(minute, close):
    return (f"2026-11-13T{15 + minute // 60:02d}:{minute % 60:02d}:00Z", close)


def mkstate(sym="NBIS", raw_key="NBIS", text=NBIS, rating="Overweight", ref=128.64, held=19.0, day="2026-11-12"):
    st = {"plans": {}}
    M.register(st, sym, f"{day}|{sym}", day, P.validate(RAW[raw_key], text, rating, ref, "long" if held > 0 else None), held)
    return st


# ---------- stop placement / replacement ----------
def test_stop_placed_gtc_for_whole_position():
    b, st = FakeBroker({"NBIS": 19.0}), mkstate()
    ev = M.protect_all(b, st, now=NOW)
    assert b.log == [("submit", "stop", "NBIS", "sell", 19, 110.0, None, "", "gtc")]
    assert "stop $110" in ev[0] and st["plans"]["NBIS"]["placed"]["qty"] == 19
    assert M.protect_all(b, st, now=NOW) == [] and len(b.log) == 1            # idempotent: live stop already matches


def test_stop_replaced_when_plan_changes_cancel_before_new():
    b, st = FakeBroker({"NBIS": 19.0}), mkstate()
    M.protect_all(b, st, now=NOW)
    new = dict(RAW["NBIS"], stop={"price": 112.0, "type": "price", "sma_days": None})
    assert M.register(st, "NBIS", "2026-11-13|NBIS", "2026-11-13", P.validate(new, NBIS.replace("110.00", "112.00"), "Overweight", 128.64), 19) == "new"
    M.protect_all(b, st, now=NOW)
    kinds = [x[0] for x in b.log]
    assert kinds == ["submit", "cancel", "submit"] and b.log[-1][5] == 112.0   # old stop cancelled (and confirmed) before the new one


def test_same_plan_next_night_keeps_state_and_stop():
    b, st = FakeBroker({"NBIS": 19.0}), mkstate()
    M.protect_all(b, st, now=NOW)
    st["plans"]["NBIS"]["scale"][0]["streak"] = 2
    assert M.register(st, "NBIS", "2026-11-13|NBIS", "2026-11-13", P.validate(RAW["NBIS"], NBIS, "Overweight", 130.0), 19) == "kept"
    assert st["plans"]["NBIS"]["scale"][0]["streak"] == 2
    M.protect_all(b, st, now=NOW)
    assert len(b.log) == 1


def test_missing_stop_is_replaced_and_oco_with_target():
    b, st = FakeBroker({"NBIS": 19.0}), mkstate()
    M.protect_all(b, st, now=NOW)
    for o in b.orders.values():
        o.status = "canceled"                                                # someone cancelled the GTC stop
    M.run_once(b, st, {"NBIS": bar(0, 125.0)}, NOW, sleep=lambda s: None)
    assert [x for x in b.log if x[0] == "submit"][-1][1] == "stop"
    b2, st2 = FakeBroker({"NBIS": 19.0}), mkstate()
    st2["plans"]["NBIS"]["plan"]["tp"] = 150.0
    M.protect_all(b2, st2, now=NOW)
    assert b2.log[0][1] == "limit" and b2.log[0][7] == "oco" and b2.log[0][5] == 110.0 and b2.log[0][6] == 150.0


def test_short_position_gets_buy_stop():
    r = raw(direction="short", stop={"price": 140.0, "type": "price"})
    st = {"plans": {}}
    M.register(st, "X", "d|X", "2026-11-12", P.validate(r, "Cover on a stop at $140.", "Sell", 128.0), -10.0)
    b = FakeBroker({"X": -10.0})
    M.protect_all(b, st, now=NOW)
    assert b.log[0][:4] == ("submit", "stop", "X", "buy")


def test_release_cancels_stop_before_exit_order():
    b, st = FakeBroker({"NBIS": 19.0}), mkstate()
    M.protect_all(b, st, now=NOW)
    out = M.release(b, st, [{"symbol": "NBIS", "side": "sell", "qty": 19.0, "note": "exit"}], sleep=lambda s: None)
    assert out == {"NBIS"} and all(o.status == "canceled" for o in b.orders.values())
    TB_sell = SimpleNamespace(symbol="NBIS", side=SimpleNamespace(value="sell"), qty=19, time_in_force=SimpleNamespace(value="day"), client_order_id="ta-x")
    b.submit_order(TB_sell)                                                  # would raise (reserved shares) without the release
    assert M.release(b, st, [{"symbol": "NBIS", "side": "buy", "qty": 5.0, "note": "resize"}]) == set()   # adds keep the stop


# ---------- monitor triggers ----------
def test_sustain_needs_three_consecutive_bars_and_resets():
    b, st = FakeBroker({"NBIS": 9.0}), mkstate(held=9.0)
    b.px["NBIS"] = 120.0
    M.protect_all(b, st, now=NOW)
    sc = st["plans"]["NBIS"]["scale"][0]
    run = lambda m, c: M.run_once(b, st, {"NBIS": bar(m, c)}, NOW.replace(minute=min(m + 6, 59)), sleep=lambda s: None)
    run(0, 120.0); run(5, 119.0)
    assert sc["streak"] == 2 and b.pos["NBIS"] == 9
    run(10, 118.0)                                                           # dip below 118.94: streak resets
    assert sc["streak"] == 0
    run(15, 120.0); run(20, 120.5); run(20, 120.5)                           # same bar twice counts once
    assert sc["streak"] == 2 and b.pos["NBIS"] == 9
    ev = run(25, 121.0)
    assert sc["state"] == "done" and b.pos["NBIS"] == 19                      # +0.5x of the 10% slot = 10 sh at $121 (room 10 sh)
    assert any("scale-in" in e for e in ev)
    stops = [o for o in b.get_orders() if o.order_type == "stop"]
    assert len(stops) == 1 and float(stops[0].qty) == 19                           # stop extended to the new quantity


def test_scale_in_never_exceeds_slot_and_is_idempotent():
    b, st = FakeBroker({"NBIS": 21.0}), mkstate(held=21.0)                    # already at the slot
    b.px["NBIS"] = 120.0
    for k in range(5):
        M.run_once(b, st, {"NBIS": bar(5 * k, 121.0)}, NOW.replace(minute=5 * k + 6), sleep=lambda s: None)
    assert b.pos["NBIS"] == 21 and st["plans"]["NBIS"]["scale"][0]["note"] == "no room in the slot"
    n = len(b.log)
    M.run_once(b, st, {"NBIS": bar(40, 121.0)}, NOW.replace(minute=50), sleep=lambda s: None)
    assert len(b.log) == n


def test_near_trigger_single_check_and_expiry():
    st = mkstate("NVDA", "NVDA", NVDA, ref=175.2, held=7.0)
    b = FakeBroker({"NVDA": 7.0})
    ev = M.run_once(b, st, {"NVDA": bar(0, 165.5)}, NOW, sleep=lambda s: None)   # within 0.5% of 165
    near = [s for s in st["plans"]["NVDA"]["scale"] if s["condition"] == "near"][0]
    assert near["state"] == "done" and b.pos["NVDA"] > 7 and any("near" in e or "$165" in e for e in ev)
    st2 = mkstate("NVDA", "NVDA", NVDA, ref=175.2, held=7.0)
    late = NOW.replace(day=30, month=11)
    M.run_once(FakeBroker({"NVDA": 7.0}), st2, {"NVDA": ("2026-11-30T15:00:00Z", 165.0)}, late, sleep=lambda s: None)
    assert all(s["state"] == "expired" for s in st2["plans"]["NVDA"]["scale"])   # 10 sessions after 11-12 is 11-27


def test_stale_bar_does_not_count():
    s = {"condition": "above", "price": 10.0, "streak": 2, "last_bar": "x"}
    assert M.check_scale(s, ("2026-11-13T14:00:00Z", 11.0), NOW) is False and s["streak"] == 2


def test_exit_condition_cancels_stop_then_closes():
    st = mkstate("QQQ", "QQQ", QQQ, "Hold", 688.38, held=3.0)
    b = FakeBroker({"QQQ": 3.0})
    ev = M.run_once(b, st, {"QQQ": bar(0, 690.0)}, NOW, sleep=lambda s: None)
    assert ev == [] and b.pos["QQQ"] == 3                                    # above the level: nothing (and no stop: the plan has none)
    ev = M.run_once(b, st, {"QQQ": bar(5, 680.5)}, NOW, sleep=lambda s: None)
    assert b.pos["QQQ"] == 0 and "plan exit below $680.93" in ev[0] and st["plans"]["QQQ"]["status"] == "exited"
    assert M.run_once(b, st, {"QQQ": bar(10, 679.0)}, NOW, sleep=lambda s: None) == []   # no second order


def test_exit_with_live_stop_cancels_first():
    st = mkstate("GPN", "GPN", GPN, ref=87.09, held=28.0)
    st["plans"]["GPN"]["exits"] = [{"condition": "below", "price": 80.0, "state": "pending"}]
    b = FakeBroker({"GPN": 28.0})
    M.protect_all(b, st, now=NOW)
    M.run_once(b, st, {"GPN": bar(0, 79.5)}, NOW, sleep=lambda s: None)
    seq = [x[0] for x in b.log]
    assert seq == ["submit", "cancel", "submit"] and b.log[-1][1] == "market" and b.pos["GPN"] == 0


def test_closed_position_marks_plan_closed_and_cleans_orders():
    b, st = FakeBroker({"NBIS": 19.0}), mkstate()
    M.protect_all(b, st, now=NOW)
    b.pos["NBIS"] = 0                                                         # stop filled
    M.run_once(b, st, {}, NOW, sleep=lambda s: None)
    for o in b.orders.values():
        o.status = "filled"
    M.run_once(b, st, {}, NOW, sleep=lambda s: None)
    assert st["plans"]["NBIS"]["status"] == "closed"


def test_monitor_quiet_run_does_not_touch_the_file(tmp_path):
    path = tmp_path / "plans.json"
    st = mkstate()
    M.save(st, path)
    b = FakeBroker({"NBIS": 19.0})
    M.protect_all(b, st, now=NOW)
    M.save(st, path)
    before = path.read_text()
    sent = []
    M.monitor(b, path, bars_fn=lambda syms, now: {"NBIS": bar(0, 105.0)}, send=sent.append, now=NOW, sleep=lambda s: None)
    assert path.read_text() == before and sent == []
    b.open_ = False
    assert M.monitor(b, path, bars_fn=None, send=sent.append, now=NOW) == 0 and sent == []


def test_monitor_messages_on_order_events(tmp_path):
    path = tmp_path / "plans.json"
    M.save(mkstate(), path)
    b, sent = FakeBroker({"NBIS": 19.0}), []
    M.monitor(b, path, bars_fn=lambda syms, now: {"NBIS": bar(0, 125.0)}, send=sent.append, now=NOW, sleep=lambda s: None)
    assert len(sent) == 1 and "stop $110" in sent[0] and "paper" in sent[0]


# ---------- nightly hooks and message ----------
def test_nightly_registers_once_and_skips_model_call_on_rerun():
    st, calls = {"plans": {}}, []

    def build(text, rating, ref, held):
        calls.append(rating)
        return P.validate(RAW["NBIS"], text, rating, ref, held)
    night = [L.make_decision("2026-11-12", "NBIS", "Overweight", NBIS)]
    plans, sizes = M.nightly(None, st, night, {"NBIS": 128.64}, build, {})
    assert sizes == {"NBIS": 0.5} and len(calls) == 1
    M.nightly(None, st, night, {"NBIS": 128.64}, build, {})
    assert len(calls) == 1


def test_adopt_registers_and_protects_current_positions(tmp_path):
    decs = [L.make_decision("2026-11-12", s, r, t) for s, r, t in (("NBIS", "Overweight", NBIS), ("NVDA", "Overweight", NVDA),
                                                                  ("GPN", "Overweight", GPN), ("QQQ", "Hold", QQQ))]
    (tmp_path / "s.json").write_text(json.dumps({"decisions": decs}))
    b = FakeBroker({"NBIS": 19.0, "NVDA": 15.0, "GPN": 28.0, "QQQ": 3.0})
    b.px = {"NBIS": 115.29, "NVDA": 162.33, "GPN": 89.49, "QQQ": 682.16}
    key = {d["symbol"]: d["symbol"] for d in decs}
    build = lambda text, rating, ref, held: P.validate(RAW[key[[k for k, v in {"NBIS": NBIS, "NVDA": NVDA, "GPN": GPN, "QQQ": QQQ}.items() if v == text][0]]],
                                                       text, rating, ref, held)
    plans, ev = M.adopt("2026-11-12", tmp_path / "s.json", tmp_path / "p.json", broker=b, build=build, sleep=lambda s: None)
    stops = {o.symbol: (o.qty, o.stop_price) for o in b.get_orders()}
    assert {k: (float(v[0]), v[1]) for k, v in stops.items()} == {"NBIS": (19.0, 110.0), "NVDA": (15.0, 146.49), "GPN": (28.0, 77.63)}
    assert M.load(tmp_path / "p.json")["plans"]["QQQ"]["exits"][0]["price"] == 680.93


def test_nightly_message_shows_plan_and_not_executed():
    p = P.validate(RAW["NVDA"], NVDA, "Overweight", 175.2)
    res = [{"symbol": "NVDA", "rating": "Overweight", "rationale": "r", "plan_line": P.plan_line("NVDA", p), "not_executed": p["not_executed"]},
           {"symbol": "orders", "note": "NVDA: placed GTC STOP", "plan_event": True}]
    m = L.nightly_message("2026-11-12", res, 25000, 0.0)
    assert "NVDA · Overweight 0.5x · stop $146.49 · +0.5x if holds &gt; $175" in m and "Not executed:" in m and "clipped" in m
    assert "Stop/target orders:" in m


def test_place_does_not_cancel_plan_orders():
    class C:
        cancelled = []
        def get_orders(self, *a, **k):
            return [SimpleNamespace(client_order_id="tap-stop-NBIS-1", id="s"), SimpleNamespace(client_order_id=None, id="leg")]
        def cancel_order_by_id(self, i):
            self.cancelled.append(i)
    c = C()
    TB.place(c, [], "x")
    assert c.cancelled == []


def test_nightly_run_sizes_order_and_reports_plan(tmp_path, monkeypatch):
    import system_ta as S
    monkeypatch.setattr(TB, "snapshot", lambda client, snapshot_fn=None: {"equity": 25000.0, "cash": 25000.0, "positions": [], "orders": [],
                                                                          "calendar": [], "balance_history": {"rows": []}})
    monkeypatch.setattr(TB, "shortable_fn", lambda c: (lambda s: True))
    b = FakeBroker()
    bars = {s: {d: (d, 128.64, 128.64) for d in ("2026-11-12", "2026-11-13")} for s in ("SPY", "NBIS")}
    state = S.load_state(tmp_path / "x.json")
    decide = lambda tk, day: {"rating": "Overweight", "rationale": NBIS, "horizon": "6-12 months", "calls": 1, "seconds": 0}
    sent = []
    plan_fn = lambda text, rating, ref, held: P.validate(RAW["NBIS"], text, rating, ref, held)
    res, _ = S.run(state, "2026-11-12", ["NBIS"], decide, lambda syms: bars, broker=b, plan_fn=plan_fn, plans_path=tmp_path / "p.json")
    assert [x for x in b.log if x[0] == "submit"][0][4] == 9.0            # 5% of $25,000 at $128.64 = 9 sh (not 19)
    assert "stop $110" in res[0]["plan_line"] and M.load(tmp_path / "p.json")["plans"]["NBIS"]["plan"]["stop"] == 110.0
    assert "NBIS · Overweight 0.5x" in L.nightly_message("2026-11-12", res)


def test_hedge_sentence_is_an_exit_trigger_not_a_stop():
    r = raw(stop={"price": 680.93, "type": "sma", "sma_days": 200}, exit_conditions=[{"condition": "above", "price": 718.46}])
    p = P.validate(r, QQQ, "Hold", 688.38, "long")
    assert p["stop"] is None and p["exits"] == [{"condition": "below", "price": 680.93}]       # 'above 718.46' is an entry confirm, not an exit
    assert any("hedge" in w for w in p["not_executed"]) and any("not an adverse trigger" in w for w in p["not_executed"])
    assert P.stop_wording(GPN, 77.63) == "stop" and P.stop_wording(NVDA, 146.49) == "stop" and P.stop_wording(NBIS, 110.0) == "stop"


def test_max_total_cap_blocks_scale_in():
    r = dict(RAW["GPN"], max_total_x=0.5, scale_in=[{"condition": "near", "price": 87.09, "size_x": 0.5}])
    p = P.validate(r, GPN, "Overweight", 87.09)
    assert p["scale_in"] == [] and "already uses the 0.5x" in p["not_executed"][-1]
