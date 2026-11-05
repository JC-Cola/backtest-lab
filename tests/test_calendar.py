from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from alpaca_paper import calendar_from_history

TPL = Path(__file__).resolve().parent.parent / "report" / "lab_template.html"


def test_calendar_basic_and_skips_unstarted():
    # 2026-11-02 (Mon) and 11-03 midnight ET = 05:00 UTC (EST after Nov 1)
    # real Alpaca stamps: 20:00 New York on Nov 2, 3 (funding day, no P/L) and 4
    ts = [1793667600, 1793754000, 1793840400]
    out = calendar_from_history(ts, [0, 25000, 25007.32], [0, 0, 7.32], [0, 0, 0.000293])
    assert out == [{"date": "2026-11-04", "pl": 7.32, "pct": 0.029, "equity": 25007.32}]


def test_calendar_handles_none_and_empty():
    assert calendar_from_history(None, None, None, None) == []
    assert calendar_from_history([1793682000], [None], [None], [None]) == []
    assert calendar_from_history([1793682000], [100.0], [-5.0], [None])[0]["pct"] == 0.0


def test_template_has_calendar_panel():
    t = TPL.read_text()
    for needle in ('id="pcal"', 'id="pcal-prev"', 'id="pcal-next"', "No trading days yet.", "renderCal"):
        assert needle in t
