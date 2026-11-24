import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import adjustments as adj  # noqa: E402


def make(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps([{"account": "dts", "date": "2026-11-20", "symbol": "X", "usd": 100.0, "why": "a"},
                             {"account": "dts", "date": "2026-11-25", "symbol": "Y", "usd": 50.5, "why": "b"},
                             {"account": "ta", "date": "2026-11-20", "symbol": "Z", "usd": 7.0, "why": "c"}]))
    return p


def test_missing_file_is_empty(tmp_path):
    assert adj.load(tmp_path / "nope.json") == []
    assert adj.cum_on("dts", "2026-12-01", tmp_path / "nope.json") == 0


def test_for_account(tmp_path):
    assert len(adj.for_account("dts", make(tmp_path))) == 2
    assert adj.for_account("zzz", make(tmp_path)) == []


def test_cum_on(tmp_path):
    p = make(tmp_path)
    assert adj.cum_on("dts", "2026-11-19", p) == 0
    assert adj.cum_on("dts", "2026-11-20", p) == 100.0
    assert adj.cum_on("dts", "2026-11-30", p) == 150.5


def test_on_day(tmp_path):
    p = make(tmp_path)
    assert adj.on_day("dts", "2026-11-20", p) == 100.0
    assert adj.on_day("dts", "2026-11-21", p) == 0


def test_committed_file_loads():
    assert adj.cum_on("dts", "2026-11-20") == 628.67
