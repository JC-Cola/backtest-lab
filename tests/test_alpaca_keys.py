"""alpaca_keys.data_keys(): which credential serves market data once the long-term account is gone."""
import io
import json
import urllib.error
from datetime import datetime, timezone

import pytest

import alpaca_keys as ak

REAL_WORKS = ak.works   # conftest replaces ak.works with an always-True stub for the other tests
ALL = [n for pair in ak.ORDER for n in pair]


def env(*names):
    return {n: f"v-{n}" for n in names}


def test_order_is_intraday_dts_ta_then_legacy():
    assert [k for k, _ in ak.ORDER] == ["ALPACA_INTRADAY_API_KEY", "ALPACA_DTS_API_KEY", "ALPACA_TA_API_KEY", "ALPACA_API_KEY"]
    assert ak.data_keys(env(*ALL), probe=False) == ("v-ALPACA_INTRADAY_API_KEY", "v-ALPACA_INTRADAY_SECRET_KEY")


def test_falls_through_missing_and_half_set_pairs():
    e = env("ALPACA_INTRADAY_API_KEY", "ALPACA_TA_API_KEY", "ALPACA_TA_SECRET_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY")   # intraday secret missing
    assert ak.data_keys(e, probe=False)[0] == "v-ALPACA_TA_API_KEY"
    assert ak.data_keys(env("ALPACA_API_KEY", "ALPACA_SECRET_KEY"), probe=False)[0] == "v-ALPACA_API_KEY"   # legacy still works
    assert ak.data_keys({}, probe=False) == (None, None)


def test_probe_skips_a_dead_key(monkeypatch):
    seen = []
    monkeypatch.setattr(ak, "works", lambda k, s, urlopen=None: seen.append(k) or k != "v-ALPACA_INTRADAY_API_KEY")
    assert ak.data_keys(env(*ALL))[0] == "v-ALPACA_DTS_API_KEY"
    assert seen == ["v-ALPACA_INTRADAY_API_KEY", "v-ALPACA_DTS_API_KEY"]


def test_all_dead_returns_first_set_pair(monkeypatch):
    monkeypatch.setattr(ak, "works", lambda *a, **k: False)
    e = env("ALPACA_TA_API_KEY", "ALPACA_TA_SECRET_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY")
    assert ak.data_keys(e)[0] == "v-ALPACA_TA_API_KEY"


def test_single_pair_is_not_probed(monkeypatch):
    monkeypatch.setattr(ak, "works", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no probe needed")))
    assert ak.data_keys(env("ALPACA_INTRADAY_API_KEY", "ALPACA_INTRADAY_SECRET_KEY"))[0] == "v-ALPACA_INTRADAY_API_KEY"


class _Ok:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_works_only_401_403_mean_dead():
    def raiser(code):
        def f(req, timeout=None):
            raise urllib.error.HTTPError("u", code, "x", {}, io.BytesIO())
        return f
    assert REAL_WORKS("k", "s", raiser(401)) is False and REAL_WORKS("k", "s", raiser(403)) is False
    assert REAL_WORKS("k", "s", raiser(500)) is True
    assert REAL_WORKS("k", "s", lambda *a, **k: (_ for _ in ()).throw(OSError("down"))) is True
    assert REAL_WORKS("k", "s", lambda req, timeout=None: _Ok()) is True


def test_real_env_result_is_cached(monkeypatch):
    for n in ALL:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("ALPACA_TA_API_KEY", "a")
    monkeypatch.setenv("ALPACA_TA_SECRET_KEY", "b")
    assert ak.data_keys() == ("a", "b")
    monkeypatch.setenv("ALPACA_INTRADAY_API_KEY", "c")
    monkeypatch.setenv("ALPACA_INTRADAY_SECRET_KEY", "d")
    assert ak.data_keys() == ("a", "b")                 # cached for the process
    ak.clear_cache()
    assert ak.data_keys()[0] == "c"


def test_data_modules_use_the_helper(monkeypatch):
    import news_feed
    import news_scan
    import universe
    for n in ALL:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("ALPACA_INTRADAY_API_KEY", "ik")
    monkeypatch.setenv("ALPACA_INTRADAY_SECRET_KEY", "is")
    assert news_scan._keys(None, None) == {"APCA-API-KEY-ID": "ik", "APCA-API-SECRET-KEY": "is"}
    monkeypatch.setenv("ALPACA_API_KEY", "legacy")      # a stale legacy key never wins over the intraday key
    monkeypatch.setenv("ALPACA_SECRET_KEY", "legacy")
    ak.clear_cache()
    seen = {}

    def fake(req, timeout=None):
        seen["id"] = req.get_header("Apca-api-key-id")
        return _Ok()
    monkeypatch.setattr(json, "load", lambda r: {"news": []})
    news_feed.fetch(None, datetime.now(timezone.utc), urlopen=fake)
    assert seen["id"] == "ik"
    for n in ALL:
        monkeypatch.delenv(n, raising=False)
    ak.clear_cache()
    with pytest.raises(RuntimeError):
        universe.alpaca_tradable()
