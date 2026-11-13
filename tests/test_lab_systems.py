import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
TPL = ROOT / "report" / "lab_template.html"
BUILT = ROOT / "report" / "lab.html"


def test_template_has_account_switch_by_repo_name_and_compare():
    t = TPL.read_text()
    nav = t.split('id="lfilter"')[1].split("</nav>")[0]
    for needle in ('data-f="intraday"', 'data-f="qlib"', 'data-f="ta"', 'data-f="dts_shadow"', 'data-f="compare"', "<b>Noise band</b>", "<b>qlib</b>",
                   "<b>TradingAgents</b>", "<b>day-trader-skill (shadow)</b>", "ours · SPY+QQQ intraday",
                   "AI analysts decide after the close, holds overnight", "Compare"):
        assert needle in nav
    assert not re.search(r"System [ABC]\b|Ours ·", t)   # no "System whatever" labels anywhere on the page
    assert 'data-f="longterm"' not in nav
    for needle in ('id="view-compare"', "renderCompare", 'id="cmp-cards"', 'id="cmp-plot"', "Since start", "Trades (closed)", "Open positions"):
        assert needle in t
    # same storage key as before; the old "longterm" value falls back to Ours
    assert 'localStorage.getItem("lab-lfilter")' in t and 'localStorage.setItem("lab-lfilter"' in t
    assert 'if (f === "dts") f = "qlib"; if (f === "compare" || ACCF[f]) lfilter = f' in t   # the old System B value now opens qlib


def test_template_reads_system_snapshots_and_shows_decisions():
    t = TPL.read_text()
    for needle in ('snap: "alpaca_intraday"', 'snap: "system_qlib"', 'snap: "system_ta"', 'snap: "system_dts"', "Latest AI decision per ticker", "latest_scan",
                   "Latest qlib picks", "report_cards", "Report card"):
        assert needle in t
    assert not re.search(r"D\.alpaca\b(?!_)", t)
    assert "alpaca_live('dts')" in t and "key: null" in t   # the shadow ledger has no live query; 'dts' is qlib's account


def test_build_lab_embeds_accounts_and_report_cards(tmp_path):
    import build_lab
    src = (ROOT / "src" / "build_lab.py").read_text()
    for needle in ('"system_qlib"', '"system_dts"', '"system_ta"', '"report_cards"', "system_qlib.json", "system_dts.json", "system_ta.json"):
        assert needle in src
    for gone in ('"alpaca": alpaca', "build_crypto9(", "build_crypto10(", "alpaca_account.json", "mix_portfolio("):
        assert gone not in src
    assert build_lab._snapshot("does_not_exist.json") is None  # gracefully empty
    assert build_lab.slim_dts_shadow(None) is None
    slim = build_lab.slim_dts_shadow({"equity": 1, "days": {"2026-11-11": {"scan": {"status": "done"}},
                                                          "2026-11-12": {"scan": {"status": "done", "at": "10:15", "universe": 5928, "hits": ["A", "B"],
                                                                                  "decisions": [{"symbol": "A", "status": "skipped", "gate": "odds", "reason": "x" * 500}]}}}})
    assert "days" not in slim and slim["latest_scan"]["date"] == "2026-11-12" and slim["latest_scan"]["hits"] == 2
    assert len(slim["latest_scan"]["decisions"][0]["reason"]) == 160
    assert build_lab.slim_qlib(None) is None
    q = build_lab.slim_qlib({"equity": 1, "picks": [{"symbol": f"S{i}", "score": 0.1, "rank": i} for i in range(50)] + ["junk"]})
    assert len(q["picks"]) == 20 and build_lab.slim_qlib({"equity": 1})["picks"] == []


def test_built_page_has_the_accounts_and_no_long_term():
    if not BUILT.exists():
        return
    m = re.search(r'<script id="lab" type="application/json">(.*?)</script>', BUILT.read_text(), re.S)
    D = json.loads(m.group(1).replace("<\\/", "</"))
    assert "system_qlib" in D and "system_dts" in D and "system_ta" in D and "alpaca_intraday" in D and "report_cards" in D
    assert "system_b" not in D and "system_c" not in D
    assert all("tearsheet" not in v for v in ((D["report_cards"] or {}).get("accounts") or {}).values())   # metrics only, no tear-sheet html
    for gone in ("alpaca", "research", "paper"):
        assert gone not in D
    assert D["book"] and all(b["family"] == "Intraday" for b in D["book"])
