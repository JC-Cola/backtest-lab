import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
TPL = ROOT / "report" / "lab_template.html"
BUILT = ROOT / "report" / "lab.html"


def test_template_has_three_account_switch_and_compare():
    t = TPL.read_text()
    nav = t.split('id="lfilter"')[1].split("</nav>")[0]
    for needle in ('data-f="intraday"', 'data-f="dts"', 'data-f="ta"', 'data-f="compare"', "Ours · Intraday", "System B · day-trader-skill",
                   "System C · TradingAgents", "hourly checks, flat by 15:56", "scans ~5,900 stocks at 10:15, flat by 15:55",
                   "AI analysts decide after the close, holds overnight", "Compare"):
        assert needle in nav
    assert 'data-f="longterm"' not in nav
    for needle in ('id="view-compare"', "renderCompare", 'id="cmp-cards"', 'id="cmp-plot"', "Since start", "Trades (closed)", "Open positions"):
        assert needle in t
    # same storage key as before; the old "longterm" value falls back to Ours
    assert 'localStorage.getItem("lab-lfilter")' in t and 'localStorage.setItem("lab-lfilter"' in t
    assert 'if (f === "compare" || ACCF[f]) lfilter = f' in t


def test_template_reads_system_snapshots_and_shows_decisions():
    t = TPL.read_text()
    for needle in ('snap: "alpaca_intraday"', 'snap: "system_b"', 'snap: "system_c"', "Latest AI decision per ticker", "latest_scan"):
        assert needle in t
    assert not re.search(r"D\.alpaca\b(?!_)", t)


def test_build_lab_embeds_system_b_and_c(tmp_path):
    import build_lab
    src = (ROOT / "src" / "build_lab.py").read_text()
    for needle in ('"system_b"', '"system_c"', "system_dts.json", "system_ta.json"):
        assert needle in src
    for gone in ('"alpaca": alpaca', "build_crypto9(", "build_crypto10(", "alpaca_account.json", "mix_portfolio("):
        assert gone not in src
    assert build_lab._snapshot("does_not_exist.json") is None  # gracefully empty
    assert build_lab.slim_system_b(None) is None
    slim = build_lab.slim_system_b({"equity": 1, "days": {"2026-11-11": {"scan": {"status": "done"}},
                                                          "2026-11-12": {"scan": {"status": "done", "at": "10:15", "universe": 5928, "hits": ["A", "B"],
                                                                                  "decisions": [{"symbol": "A", "status": "skipped", "gate": "odds", "reason": "x" * 500}]}}}})
    assert "days" not in slim and slim["latest_scan"]["date"] == "2026-11-12" and slim["latest_scan"]["hits"] == 2
    assert len(slim["latest_scan"]["decisions"][0]["reason"]) == 160


def test_built_page_has_the_three_systems_and_no_long_term():
    if not BUILT.exists():
        return
    m = re.search(r'<script id="lab" type="application/json">(.*?)</script>', BUILT.read_text(), re.S)
    D = json.loads(m.group(1).replace("<\\/", "</"))
    assert "system_b" in D and "system_c" in D and "alpaca_intraday" in D
    for gone in ("alpaca", "research", "paper"):
        assert gone not in D
    assert D["book"] and all(b["family"] == "Intraday" for b in D["book"])
