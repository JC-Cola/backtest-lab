"""Congress shadow account: parse -> trade mapping, next-open-after-filing timing, range -> size, full/partial sale, options skipped,
idempotent re-run, unparsed filings recorded, Telegram text. Fake filings, fake prices, no network, no broker."""
import datetime as dt
import json

import pytest

import congress_data as cd
import system_congress as sc

PTR_TEXT = """\
                                   Filing ID #20040001
Name:       Hon. Nancy Pelosi
Status:     Member
State/District: CA11

ID   Owner Asset                                   Type  Date        Notified    Amount
     SP    NVIDIA Corporation - Common Stock (NVDA) [ST]  P  10/03/2026 10/03/2026 $1,000,001 - $5,000,000
           Filing Status: New
     SP    NVIDIA Corporation - Common Stock (NVDA) [OP]  P  10/03/2026 10/03/2026 $250,001 - $500,000
           Filing Status: New
           Description: Purchased 100 call options with a strike price of $100 and an expiration date of 06/17/2027.
     SP    Apple Inc. - Common Stock (AAPL) [ST]  P  10/05/2026 10/05/2026 $15,001 - $50,000
           Filing Status: New
     SP    Apple Inc. - Common Stock (AAPL) [ST]  S (partial)  10/20/2026 10/20/2026 $15,001 - $50,000
           Filing Status: New
     SP    Apple Inc. - Common Stock (AAPL) [ST]  S  10/28/2026 10/28/2026 $15,001 - $50,000
           Filing Status: New
     SP    Some Private LLC [OL]  P  10/05/2026 10/05/2026 $500,001 - $1,000,000
           Filing Status: New
* For the complete list of asset type abbreviations, please visit https://fd.house.gov
Digitally Signed: Hon. Nancy Pelosi , 11/14/2026
"""
FILED = "2026-11-14"      # a Saturday: the first open after it is Monday 2026-11-16


def index_row(doc="20040001", filed=FILED):
    return {"doc_id": doc, "type": "P", "last": "Pelosi", "first": "Nancy", "name": "Hon. Nancy Pelosi", "district": "CA11",
            "year": 2026, "filed": filed, "url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/%s.pdf" % doc}


def weekdays(start, n):
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


DAYS = weekdays("2026-11-02", 40)


def series(px0, step=0.0):
    return {d: {"o": px0 + step * i, "c": px0 + step * i + 0.5} for i, d in enumerate(DAYS)}


def fake_bars(symbols, start, end):
    base = {"SPY": series(500.0), "NVDA": series(100.0, 1.0), "AAPL": series(200.0, 1.0)}
    return {s: base[s] for s in symbols if s in base}


@pytest.fixture
def pdf_cache(tmp_path):
    d = tmp_path / "pdfs"
    d.mkdir()
    return d


def pdf(cache, doc):
    (cache / ("%s.pdf" % doc)).write_bytes(b"%PDF-1.4 fake")


def fake_text(_path):
    return PTR_TEXT


def records(text=PTR_TEXT, first_seen=None, filed=FILED):
    rows = cd.parse_text(text, filed)
    return cd.to_records("Pelosi", index_row(filed=filed), rows, first_seen)


# ---------------------------------------------------------------- parse -> trade mapping

def test_parse_maps_rows_to_kinds_and_dates():
    rs = records()
    kinds = [(r["ticker"], r["kind"]) for r in rs]
    assert kinds == [("NVDA", "buy"), ("NVDA", "option"), ("AAPL", "buy"), ("AAPL", "sell_partial"), ("AAPL", "sell_full"),
                     (None, "noticker")]
    nv = rs[0]
    assert nv["traded"] == "2026-10-03" and nv["filed"] == FILED and nv["lag_days"] == 42
    assert nv["amount_range"] == "$1M–5M" and nv["mid"] == pytest.approx(3_000_000.0, abs=1) and nv["owner"] == "spouse"
    assert rs[1]["status"] == "recorded" and "recorded not traded" in rs[1]["note"]      # option: recorded, never traded


def test_exercise_of_options_is_not_a_stock_buy():
    row = {"asset_type": "ST", "type": "P", "ticker": "AMZN", "description": "Exercised 30 call options (3,000 shares) expiring 1/17/20"}
    assert cd.classify(row)[0] == "option"


def test_amount_labels():
    assert cd.amount_label(1001, 15000) == "$1K–15K"
    assert cd.amount_label(5_000_001, 25_000_000) == "$5M–25M"
    assert cd.amount_label(50_000_000, None) == "over $50M"


# ---------------------------------------------------------------- replay: timing, size, sales

def test_enters_at_first_open_strictly_after_filing():
    rs = records()
    rp = sc.replay(rs, fake_bars(["NVDA", "AAPL", "SPY"], "", ""))
    nv = rs[0]
    assert nv["exec_date"] == "2026-11-16" and nv["status"] == "copied"        # Saturday filing -> Monday open
    bar = fake_bars(["NVDA"], "", "")["NVDA"]["2026-11-16"]
    assert nv["fill"] == pytest.approx(bar["o"] * 1.0005)                       # 0.05% worse than the open
    assert rp["orders"][0]["date"] == "2026-11-16" and rp["orders"][0]["side"] == "buy"


def test_filing_day_open_is_never_used_even_on_a_trading_day():
    rs = records(filed="2026-11-10")            # a Tuesday: acts Wednesday 11-11, not Tuesday's open
    sc.replay(rs, fake_bars(["NVDA", "AAPL", "SPY"], "", ""))
    assert rs[0]["exec_date"] == "2026-11-11"


def test_first_seen_after_filing_delays_the_entry():
    rs = records(first_seen="2026-11-18")        # we only saw it Wednesday night
    sc.replay(rs, fake_bars(["NVDA", "AAPL", "SPY"], "", ""))
    assert rs[0]["exec_date"] == "2026-11-19"


def test_range_midpoint_sets_size_and_caps_at_20_percent():
    rs = records()
    sc.replay(rs, fake_bars(["NVDA", "AAPL", "SPY"], "", ""))
    nv, aapl = rs[0], rs[2]
    assert nv["weight"] == pytest.approx(0.20, abs=0.002)                      # $1M-5M band = a full 20% slot
    assert aapl["weight"] == pytest.approx(0.20 * 32_500.5 / 3_000_000, abs=0.0002)   # $15K-50K mid, proportional
    big = records(PTR_TEXT.replace("$1,000,001 - $5,000,000", "$5,000,001 - $25,000,000"))
    sc.replay(big, fake_bars(["NVDA", "AAPL", "SPY"], "", ""))
    assert big[0]["weight"] == pytest.approx(0.20, abs=0.002)                  # clamped


def test_second_buy_of_same_name_cannot_pass_the_cap():
    text = PTR_TEXT + ""
    rs = records(text)
    extra = dict(rs[0], key="x:9", mid=3_000_000.0)
    rp = sc.replay(rs + [extra], fake_bars(["NVDA", "AAPL", "SPY"], "", ""))
    assert extra["status"] == "skipped" and "cap" in extra["note"]
    assert [o["symbol"] for o in rp["orders"]].count("NVDA") == 1


def test_partial_sale_halves_and_full_sale_closes():
    rs = records()
    rp = sc.replay(rs, fake_bars(["NVDA", "AAPL", "SPY"], "", ""))
    buy, part, full = rs[2], rs[3], rs[4]
    assert part["status"] == "copied" and full["status"] == "copied"
    assert part["qty"] == pytest.approx(buy["qty"] / 2, abs=1e-3)
    assert full["qty"] == pytest.approx(buy["qty"] / 2, abs=1e-3)              # what was left after the half
    assert all(p["symbol"] != "AAPL" for p in rp["positions"])                # closed
    assert len(rp["trades"]) == 2 and rp["trades"][0]["reason"].startswith("partial sale")
    assert any(p["symbol"] == "NVDA" for p in rp["positions"])


def test_sale_of_unheld_name_is_ignored_and_long_only():
    r = records()[4]                              # a full sale with no earlier buy in this list
    rp = sc.replay([r], fake_bars(["AAPL", "SPY"], "", ""))
    assert r["status"] == "ignored" and rp["trades"] == [] and rp["positions"] == []


def test_options_exchanges_private_assets_never_trade():
    rs = records()
    rp = sc.replay(rs, fake_bars(["NVDA", "AAPL", "SPY"], "", ""))
    assert [o["symbol"] for o in rp["orders"]] == ["NVDA", "AAPL", "AAPL", "AAPL"]
    assert rs[1]["status"] == "recorded" and rs[5]["status"] == "recorded"


def test_no_price_and_pending():
    rs = records()
    sc.replay(rs, fake_bars(["SPY"], "", ""))
    assert rs[0]["status"] == "no_price"
    short = {"SPY": series(500.0), "NVDA": {d: v for d, v in series(100.0).items() if d <= "2026-11-13"}, "AAPL": series(200.0)}
    sc.replay(rs, {**short, "SPY": {d: v for d, v in series(500.0).items() if d <= "2026-11-13"}})
    assert rs[0]["status"] == "pending"          # the open after the filing has not happened yet


# ---------------------------------------------------------------- the nightly run

def run(path, pdf_cache, today, notify=None, rows=None):
    return sc.run(dt.date.fromisoformat(today), path, sc.CFG, index_fn=lambda: rows if rows is not None else [index_row()],
                  bars_fn=fake_bars, notify=notify, text_of=fake_text, cache_dir=pdf_cache,
                  now=dt.datetime.fromisoformat(today + "T23:15:00"))


def test_run_builds_alpaca_shaped_shadow_snapshot(tmp_path, pdf_cache):
    pdf(pdf_cache, "20040001")
    st, sent = run(tmp_path / "s.json", pdf_cache, "2026-11-20")
    assert st["mode"] == "shadow" and st["label"] == "Pelosi (shadow)" and st["follow"] == ["Pelosi"]
    for k in ("equity", "last_equity", "positions", "orders", "calendar", "history", "balance_history", "trades", "filings"):
        assert k in st
    assert {"date", "pl", "pct", "equity"} <= set(st["calendar"][0])
    assert st["balance_history"]["rows"] and st["history"]["ALL"]
    f = next(r for r in st["filings"] if r["ticker"] == "NVDA" and r["kind"] == "buy")
    assert {"member", "filed", "traded", "ticker", "type", "amount_range", "lag_days", "link", "status"} <= set(f)
    assert sent == []                              # the first run is the backfill: it never pings
    assert f["backfilled"] is True


def test_rerun_is_idempotent(tmp_path, pdf_cache):
    pdf(pdf_cache, "20040001")
    path = tmp_path / "s.json"
    a, _ = run(path, pdf_cache, "2026-11-20")
    first = json.loads(path.read_text())
    b, sent = run(path, pdf_cache, "2026-11-20")
    second = json.loads(path.read_text())
    assert first == second and sent == []
    c, _ = run(path, pdf_cache, "2026-11-20")
    assert json.loads(path.read_text()) == first
    assert len(c["orders"]) == len(a["orders"]) == 4


def test_new_filing_after_backfill_is_copied_and_announced_once(tmp_path, pdf_cache):
    path = tmp_path / "s.json"
    pdf(pdf_cache, "20040001")
    run(path, pdf_cache, "2026-11-16")                                       # backfill
    keep = ("Filing ID", "Name:", "Status:", "State/", "Digitally", "ID   Owner", "Filing Status")
    new = "\n".join(l for l in PTR_TEXT.splitlines() if ("[ST]  P  10/05" in l and "AAPL" in l) or l.strip().startswith(keep))
    texts = {"20040001": PTR_TEXT, "20040009": new}
    pdf(pdf_cache, "20040009")
    sent = []
    rows = [index_row(), index_row("20040009", "2026-11-17")]
    cur = {}

    def text_of(p):
        return texts[p.split("/")[-1][:-4]]
    st, s1 = sc.run(dt.date(2026, 11, 17), path, sc.CFG, index_fn=lambda: rows, bars_fn=fake_bars,
                    notify=lambda t: sent.append(t) or True, text_of=text_of, cache_dir=pdf_cache,
                    now=dt.datetime(2026, 11, 17, 23, 15))
    assert len(s1) == 1 and "20040001" not in s1[0]
    assert s1[0].startswith("🏛️ Pelosi filed (traded Oct 5, filed Nov 17): BUY AAPL $15K–50K → shadow buys at tomorrow's open")
    new_rec = next(r for r in st["filings"] if r["doc_id"] == "20040009")
    assert new_rec["first_seen"] == "2026-11-17" and new_rec["status"] in ("pending", "copied")
    st2, s2 = sc.run(dt.date(2026, 11, 18), path, sc.CFG, index_fn=lambda: rows, bars_fn=fake_bars,
                     notify=lambda t: sent.append(t) or True, text_of=text_of, cache_dir=pdf_cache,
                     now=dt.datetime(2026, 11, 18, 23, 15))
    assert s2 == [] and len(sent) == 1                                      # announced once
    again = next(r for r in st2["filings"] if r["doc_id"] == "20040009")
    assert again["exec_date"] == "2026-11-18" and again["status"] == "copied"


def test_unparsed_filing_is_recorded_with_its_link(tmp_path, pdf_cache):
    pdf(pdf_cache, "20040002")
    path = tmp_path / "s.json"
    st, _ = sc.run(dt.date(2026, 11, 16), path, sc.CFG, index_fn=lambda: [index_row("20040002")], bars_fn=fake_bars,
                   text_of=lambda p: "   ", cache_dir=pdf_cache, now=dt.datetime(2026, 11, 16, 23, 15))
    r = st["filings"][0]
    assert r["kind"] == "unparsed" and r["status"] == "unparsed" and r["link"].endswith("20040002.pdf")
    assert "scanned image" in r["note"] and st["stats"]["unparsed"] == 1
    assert st["docs"]["20040002"]["status"] == "unread"
    st2, _ = sc.run(dt.date(2026, 11, 17), path, sc.CFG, index_fn=lambda: [index_row("20040002")], bars_fn=fake_bars,
                    text_of=lambda p: "   ", cache_dir=pdf_cache, now=dt.datetime(2026, 11, 17, 23, 15))
    assert len([x for x in st2["filings"] if x["kind"] == "unparsed"]) == 1      # not duplicated


def test_partially_read_filing_keeps_rows_and_flags_the_rest(tmp_path, pdf_cache):
    pdf(pdf_cache, "20040003")
    extra = PTR_TEXT + "\n  s  01/16/2020 01/16/2020  $250,001 - $500,000\n"      # a row the upstream regex cannot read
    rows, why = cd.read_filing(index_row("20040003"), pdf_cache, text_of=lambda p: extra)
    assert len(rows) == 6 and "6 of 7" in why


def test_message_texts():
    buy, part, full = records()[0], records()[3], records()[4]
    today = dt.date(2026, 11, 13)                        # Friday night: the next open is Monday
    assert sc.message(buy, today) == "🏛️ Pelosi filed (traded Oct 3, filed Nov 14): BUY NVDA $1M–5M → shadow buys at Monday's open"
    assert "sells half" in sc.message(part, today) and "closes it" in sc.message(full, today)
    assert "recorded only" in sc.message(records()[1], today)
    un = cd.unparsed_record("Pelosi", index_row(), "scanned image (no text layer)")
    assert "could not read" in sc.message(un, today) and un["link"] in sc.message(un, today)
