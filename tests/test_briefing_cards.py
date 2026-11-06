"""Briefing picture cards, captions, send_photo and the text fallback, on fixture data (no network)."""
import struct
from datetime import datetime

import pytest

import briefing as bf
import briefing_cards as bc
import telegram as tg
from test_briefing import CAND, DAY, PREV, Resp, acct, order, window

RES = {"daily": {"tested": 5, "scored": 3, "best": 0.31, "holdout": 0},
       "intraday": {"tested": 3, "scored": 2, "best": 0.97, "holdout": 1}}
NO_RES = {k: {"tested": 0, "scored": 0, "best": None, "holdout": 0} for k in ("daily", "intraday")}


def size(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", png[16:24])


def evening(trades=True, days=7, bt=True, res=RES, cands=CAND, lt_eq=25100.0):
    its = [order("SPY", "buy", 16, 767.0, (10, 5)), order("SPY", "sell", 16, 768.0, (15, 56)),
           order("QQQ", "sell", 5, 500.0, (11, 0)), order("QQQ", "buy", 5, 497.0, (13, 30))] if trades else []
    lts = [order(s, "buy" if i % 2 else "sell", 1, 100.0, (9, 30)) for i, s in enumerate("ABCDEFGHIJ")] if trades else []
    hist = [25000.0] + [25000.0 + 20 * i for i in range(1, days)]
    b = {"long-term": window(), "intraday": window(sd=0.003)} if bt else {"long-term": None, "intraday": None}
    return bf.evening_data(DAY, {"long-term": acct(lt_eq, 25000, orders=lts), "intraday": acct(25040, 25000, orders=its)},
                           {"long-term": hist, "intraday": hist}, [], [], [], cands, b, res)


def morning(positions=2, waiting=2, flags=1):
    pos = [{"symbol": f"S{i}", "qty": 1.5 + i, "pl": 10.0 * (1 if i % 2 else -1)} for i in range(positions)]
    orders = [{"symbol": f"W{i}", "side": "buy", "qty": 3, "status": "accepted"} for i in range(waiting)]
    accts = {"long-term": acct(25500, 25400, pos, orders), "intraday": acct(24800, 24900, note="x" * 200 if flags else "")}
    d = bf.morning_data(DAY, accts, {"long-term": [25000.0, 25400.0], "intraday": [25000.0, 24900.0]}, [], CAND, PREV, flags < 2)
    d["flags"] = [f"warning number {i} " + "long text " * 12 for i in range(flags)]
    return d


@pytest.mark.parametrize("data", [
    evening(), evening(trades=False, days=1, bt=False, res=NO_RES, cands={}), evening(days=4), evening(days=40),
    morning(), morning(positions=0, waiting=0, flags=0), morning(positions=12, waiting=12, flags=6)])
def test_cards_render_png(data):
    w, h = size(bc.render(data))
    assert w == 1080 and 1350 <= h <= 1800          # taller than 1350 only when the content needs it
    assert 0 < len(bc.caption(data)) <= 1024 and len(bc.caption(data).splitlines()) <= 3


def test_card_without_account_numbers():
    d = evening()
    d["accounts"]["intraday"].update(equity=None, today=None, since=None, verdict=None, path=[], fan=None)
    assert size(bc.render(d))[0] == 1080
    m = morning()
    m["accounts"]["long-term"].update(equity=None, last=None, since=None, dd=None)
    assert size(bc.render(m))[0] == 1080


def test_data_feeds_text_and_card():
    d = evening()
    assert d["accounts"]["long-term"]["days"] == 7 and d["accounts"]["long-term"]["fan"]["days"][0] == 0
    assert [t[0] for t in d["accounts"]["intraday"]["trips"]] == ["QQQ", "SPY"]
    assert "Long-term" in bf.text_evening(d) and bf.build_morning(DAY, {"long-term": acct(1, 1), "intraday": acct(1, 1)}, {}, [], {})


def test_captions():
    cap = bc.caption(evening(days=1)).splitlines()
    assert cap[0] == "<b>Evening 4 Nov</b> · Long-term +0.4% · Intraday +0.2%" and "Too early" in cap[1] and cap[2].startswith("14 trades · 8 ideas tested")
    assert "candidate awaiting approval" in cap[2]
    assert bc.caption(evening(days=7)).splitlines()[1].startswith(("In line", "Backtest:"))
    assert bc.caption(morning(flags=0)).splitlines()[1] == "All clear before the open"
    assert "3 warnings" in bc.caption(morning(flags=3))


def hist(n=60, drop=False, t0=1793900000):
    s = -1 if drop else 1
    return {"1D": [[t0 + 300 * i, 25000 + s * 3 * i] for i in range(n)], "ALL": [[t0 - 86400 * 3 + 86400 * i, 25000 + s * 40 * i] for i in range(3)]}


def balance(h=None, lt_eq=25100.0):
    h = hist() if h is None else h
    return bf.balance_data(DAY, {"long-term": {"equity": lt_eq, "last_equity": 25000.0, "history": h},
                                 "intraday": {"equity": 24900.0, "last_equity": 25000.0, "history": hist(drop=True)}})


# ---------- balance history image ----------
@pytest.mark.parametrize("h", [None, {}, {"1D": [], "ALL": []}, {"1D": [[1793900000, 25000.0]], "ALL": [[1793900000, 25000.0]]}, hist(), hist(2)])
def test_balance_card_renders(h):
    d = balance(h)
    assert size(bc.render_balance(d)) == (1080, 1080)
    assert bc.balance_caption(d) == "Balance history · 4 Nov"


def test_balance_card_missing_account():
    d = bf.balance_data(DAY, {})
    assert d["accounts"]["long-term"]["equity"] is None and size(bc.render_balance(d)) == (1080, 1080)


def test_balance_data_today_change():
    a = balance()["accounts"]["long-term"]
    assert a["today"][0] == pytest.approx(100.0) and a["today"][1] == pytest.approx(0.004) and a["since"] == pytest.approx(0.004)


def test_balance_cli_sends_one_photo(cli):
    assert bf.main(["balance"]) == 0
    assert len(cli["photo"]) == 1 and cli["photo"][0][1].startswith("Balance history") and cli["text"] == []


def test_balance_cli_dry_run(cli, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bf, "DRY_DIR", tmp_path)
    assert bf.main(["balance", "--dry-run"]) == 0
    assert size((tmp_path / "briefing-balance.png").read_bytes()) == (1080, 1080) and cli["photo"] == []
    assert "Balance history" in capsys.readouterr().out


def test_evening_balance_failure_never_breaks_briefing(cli, monkeypatch):
    monkeypatch.setattr(bf, "gather_balance", lambda now=None: (_ for _ in ()).throw(RuntimeError("boom")))
    assert bf.main(["evening"]) == 0
    assert len(cli["photo"]) == 1 and cli["text"] == []


def test_evening_text_flag_sends_no_balance(cli):
    bf.main(["evening", "--text"])
    assert cli["photo"] == []


def test_gather_balance_falls_back_to_snapshots(monkeypatch):
    for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_INTRADAY_API_KEY", "ALPACA_INTRADAY_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(bf.R, "read_json", lambda p: {"equity": 25100.0, "last_equity": 25000.0, "history": hist()})
    d = bf.gather_balance()
    assert d["accounts"]["intraday"]["equity"] == 25100.0 and len(d["accounts"]["long-term"]["h1d"]) >= 60


# ---------- telegram.send_photo ----------
def test_send_photo_noop_without_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(tg.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not call the network"))
    assert tg.send_photo(b"png", "hi") is False


def test_send_photo_multipart_request(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    seen = []
    monkeypatch.setattr(tg.urllib.request, "urlopen", lambda req, timeout=None: seen.append(req) or Resp())
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    assert tg.send_photo(png, "<b>hi</b>" + "x" * 2000) is True
    req = seen[0]
    ctype = req.get_header("Content-type")
    boundary = ctype.split("boundary=")[1]
    assert req.full_url.endswith("/botTOK/sendPhoto") and ctype.startswith("multipart/form-data;") and req.get_method() == "POST"
    body = req.data
    assert png in body and body.endswith(f"--{boundary}--\r\n".encode())
    assert b'name="chat_id"\r\n\r\n42' in body and b'name="parse_mode"\r\n\r\nHTML' in body
    assert b'name="photo"; filename="briefing.png"' in body and b"Content-Type: image/png" in body
    cap = body.split(b'name="caption"\r\n\r\n')[1].split(b"\r\n--")[0]
    assert len(cap) == tg.CAPTION_LIMIT and cap.startswith(b"<b>hi</b>")


def test_send_photo_never_raises(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    def boom(*a, **k):
        raise OSError("down https://api.telegram.org/botSECRET")
    monkeypatch.setattr(tg.urllib.request, "urlopen", boom)
    assert tg.send_photo(b"x", "c") is False
    assert "SECRET" not in capsys.readouterr().out


# ---------- briefing CLI: card, --text, fallback, --dry-run ----------
@pytest.fixture
def cli(monkeypatch):
    sent = {"photo": [], "text": []}
    monkeypatch.setattr(bf, "gather_evening", lambda now=None: evening())
    monkeypatch.setattr(bf, "gather_morning", lambda now=None: morning())
    monkeypatch.setattr(bf, "gather_balance", lambda now=None: balance())
    monkeypatch.setattr(tg, "send_photo", lambda png, cap="": sent["photo"].append((png, cap)) or True)
    monkeypatch.setattr(tg, "send", lambda text: sent["text"].append(text) or True)
    return sent


def test_default_sends_card_not_text(cli):
    assert bf.main(["evening"]) == 0 and bf.main(["morning", "--force"]) == 0
    assert len(cli["photo"]) == 3 and cli["text"] == []    # evening card + balance image, then the morning card
    assert size(cli["photo"][0][0])[0] == 1080 and cli["photo"][0][1].startswith("<b>Evening")
    assert size(cli["photo"][1][0]) == (1080, 1080) and cli["photo"][1][1] == "Balance history · 4 Nov"


def test_text_flag_forces_text(cli):
    bf.main(["evening", "--text"])
    assert cli["photo"] == [] and "Matches the backtest?" in cli["text"][0]


def test_render_failure_falls_back_to_text(cli, monkeypatch, capsys):
    monkeypatch.setattr(bc, "render", lambda d: (_ for _ in ()).throw(ValueError("bad")))
    bf.main(["evening"])
    assert cli["photo"] == [] and "Matches the backtest?" in cli["text"][0]
    assert bf.make_card(evening()) is None


def test_photo_rejected_falls_back_to_text(cli, monkeypatch):
    monkeypatch.setattr(tg, "send_photo", lambda *a: False)
    bf.main(["morning", "--force"])
    assert "Risk" in cli["text"][0]


def test_dry_run_writes_png_and_prints_caption(cli, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bf, "DRY_DIR", tmp_path / "sub")
    bf.main(["evening", "--dry-run"])
    out = capsys.readouterr().out
    assert size((tmp_path / "sub" / "briefing-evening.png").read_bytes())[0] == 1080
    assert "briefing-evening.png" in out and "<b>Evening 4 Nov</b>" in out
    assert cli["photo"] == [] and cli["text"] == []


def test_dry_run_text_flag_prints_text(cli, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bf, "DRY_DIR", tmp_path)
    bf.main(["morning", "--dry-run", "--text"])
    assert "Risk" in capsys.readouterr().out and not list(tmp_path.iterdir())
