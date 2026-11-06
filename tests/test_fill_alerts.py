from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fill_alerts as fa

_n = [0]


def F(t, sym, side, qty, px, oid=None):
    _n[0] += 1
    return {"id": f"{_n[0]:04d}", "time": f"2026-11-04T{t}Z", "symbol": sym, "side": side, "qty": qty, "price": px,
            "order_id": oid}


def msgs(fills, seen=0, label="Intraday"):
    fs = sorted(fills, key=fa._key)
    return [t for _, t in fa.build_messages(label, fs, {f["id"] for f in fs[seen:]}, 25000)]


def test_open_long_and_short():
    m = msgs([F("15:01:37", "SPY", "buy", 16, 740.62), F("15:02:00", "QQQ", "sell", 3, 100)])
    assert m[0].startswith("🟢 Intraday · OPEN LONG SPY\n16 @ $740.62 = $11,849.92\n") and "New York" in m[0]
    assert m[1].startswith("🔴 Intraday · OPEN SHORT QQQ")


def test_close_short_with_balance():
    m = msgs([F("15:00:00", "SPY", "sell", 16, 737.25), F("20:00:00", "SPY", "buy", 16, 735.37)], seen=1)
    assert len(m) == 1
    assert m[0].startswith("✅ Intraday · CLOSE SHORT SPY  +$30.08\n16 @ $735.37 (avg was $737.25)\nBalance $25,000.00 → $25,030.08")


def test_loss_is_red_with_minus():
    m = msgs([F("15:00:00", "SPY", "buy", 10, 100), F("16:00:00", "SPY", "sell", 10, 99)], seen=1)
    assert m[0].startswith("❌ Intraday · CLOSE LONG SPY  -$10.00")


def test_add_and_reduce():
    fills = [F("15:00:00", "SPY", "buy", 10, 100), F("15:10:00", "SPY", "buy", 10, 110), F("15:20:00", "SPY", "sell", 5, 120)]
    m = msgs(fills, seen=1)
    assert m[0].startswith("🟢 Intraday · ADD LONG SPY") and "avg $105.00" in m[0]
    assert m[1].startswith("✅ Intraday · REDUCE LONG SPY  +$75.00") and "(avg was $105.00)" in m[1]


def test_flip():
    m = msgs([F("15:00:00", "SPY", "buy", 5, 100), F("15:30:00", "SPY", "sell", 8, 110)], seen=1)
    assert m[0].startswith("✅ Intraday · FLIP LONG→SHORT SPY  +$50.00") and "5 closed, 3 opened" in m[0]


def test_partial_fills_grouped():
    fills = [F("15:00:00", "SPY", "buy", 10, 100, "o1"),
             F("16:00:00", "SPY", "sell", 4, 101, "o2"), F("16:00:01", "SPY", "sell", 6, 102, "o2")]
    m = msgs(fills, seen=1)
    assert len(m) == 1
    assert m[0].startswith("✅ Intraday · CLOSE LONG SPY  +$16.00\n10 @ $101.60 (avg was $100.00)\nBalance $25,000.00 → $25,016.00")


def test_crypto_symbol_and_decimals():
    m = msgs([F("15:00:00", "BTCUSD", "buy", 0.0123456789, 60000)])
    assert "OPEN LONG BTC/USD" in m[0] and "0.012346 @ $60,000.00" in m[0]


def test_first_run_init_sends_nothing():
    sent = []
    fills = [F("15:00:00", "SPY", "buy", 10, 100)]
    out, st = fa.process([("intraday", "Intraday", fills)], {}, lambda t: sent.append(t) or True)
    assert out == [] and sent == [] and st["intraday"]["id"] == fills[-1]["id"]
    out2, st2 = fa.process([("intraday", "Intraday", fills)], st, lambda t: sent.append(t) or True)
    assert sent == [] and st2 == st


def test_empty_account_init_then_new_fill_sent():
    sent = []
    _, st = fa.process([("intraday", "Intraday", [])], {}, lambda t: True)
    assert "intraday" in st
    fills = [F("15:00:00", "SPY", "buy", 10, 100)]
    fa.process([("intraday", "Intraday", fills)], st, lambda t: sent.append(t) or True)
    assert len(sent) == 1


def test_failed_send_does_not_advance():
    fills = [F("15:00:00", "SPY", "buy", 10, 100), F("15:10:00", "QQQ", "buy", 5, 50)]
    st0 = {"intraday": fa.mark_of(fills[0])}
    out, st = fa.process([("intraday", "Intraday", fills)], st0, lambda t: False)
    assert st == st0
    ok = []
    out, st = fa.process([("intraday", "Intraday", fills)], st0, lambda t: ok.append(t) or True)
    assert len(ok) == 1 and st["intraday"]["id"] == fills[1]["id"]


def test_cap_of_20():
    fills = [F(f"15:{i:02d}:00", f"S{i}", "buy", 1, 10) for i in range(30)]
    sent = []
    st0 = {"intraday": {"id": "0000", "time": "2000-01-01T00:00:00Z"}}
    out, st = fa.process([("intraday", "Intraday", fills)], st0, lambda t: sent.append(t) or True)
    assert len(sent) == 21 and sent[-1].startswith("+10 more fills")
    assert st["intraday"]["id"] == fills[-1]["id"]
