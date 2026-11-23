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
    for needle in ('data-f="intraday"', 'data-f="qlib"', 'data-f="ta"', 'data-f="news_shadow"', 'data-f="compare"', "<b>Noise band</b>", "<b>qlib</b>",
                   "<b>TradingAgents</b>", "<b>News trader (shadow)</b>", "ours · SPY+QQQ intraday",
                   "AI analysts decide after the close, holds overnight", "Compare"):
        assert needle in nav
    assert "dts_shadow" not in t and "pelosi_shadow" not in t and nav.index('data-f="ta"') < nav.index('data-f="news_shadow"') < nav.index('data-f="compare"')
    assert not re.search(r"System [ABC]\b|Ours ·", t)   # no "System whatever" labels anywhere on the page
    assert 'data-f="longterm"' not in nav
    for needle in ('id="view-compare"', "renderCompare", 'id="cmp-cards"', 'id="cmp-plot"', "Since start", "Trades (closed)", "Open positions"):
        assert needle in t
    # same storage key as before; the old "longterm" value falls back to Ours
    assert 'localStorage.getItem("lab-lfilter")' in t and 'localStorage.setItem("lab-lfilter"' in t
    assert 'if (f === "dts") f = "qlib"; if (f === "compare" || f === "news_shadow" || ACCF[f]) lfilter = f' in t   # the old System B value now opens qlib; retired shadows fall back to Noise band


def test_template_reads_system_snapshots_and_shows_decisions():
    t = TPL.read_text()
    for needle in ('snap: "alpaca_intraday"', 'snap: "system_qlib"', 'snap: "system_ta"', "Latest AI decision per ticker",
                   "Latest qlib picks", "report_cards", "Report card", "D.news_shadow", "Latest shocks",
                   "first rebalance tonight at 17:00 New York", "little stock-picking signal"):
        assert needle in t
    assert not re.search(r"D\.alpaca\b(?!_)", t)
    assert "alpaca_live('dts')" in t   # 'dts' is qlib's account


def test_build_lab_embeds_accounts_and_report_cards(tmp_path):
    import build_lab
    src = (ROOT / "src" / "build_lab.py").read_text()
    for needle in ('"system_qlib"', '"system_ta"', '"report_cards"', "system_qlib.json", "system_ta.json", '"news_shadow"', "news_shadow.json", "qlib15.json"):
        assert needle in src
    for gone in ('"alpaca": alpaca', "build_crypto9(", "build_crypto10(", "alpaca_account.json", "mix_portfolio("):
        assert gone not in src
    assert build_lab._snapshot("does_not_exist.json") is None  # gracefully empty
    assert build_lab.slim_news(None) is None and build_lab.slim_qlib_backtest({}) is None
    sh = [{"id": f"S{i}|2026-11-{i:02d}", "symbol": f"S{i}", "date": f"2026-11-{i:02d}", "ret": 0.1, "mult": 3, "relvol": 2, "headlines": ["h" * 300], "score": i,
           "review": {"status": "ok", "catalyst": "earnings" if i % 2 else "macro", "direction": "continue", "confidence": 0.6, "rationale": "x" * 900}} for i in range(1, 28)]
    sh += [{"id": f"T{i}|2026-10-{i:02d}", "symbol": f"T{i}", "date": f"2026-10-{i:02d}", "ret": -0.1, "headlines": []} for i in range(1, 28)]
    tr = [{"id": "a", "symbol": "S1", "signal_date": "2026-11-01", "rule": "momentum", "hold": 1, "side": "long", "status": "closed", "entry_date": "2026-11-02", "entry_px": 10, "exit_date": "2026-11-03", "exit_px": 11, "pnl_pct": 9.9},
          {"id": "b", "symbol": "S2", "signal_date": "2026-11-02", "rule": "momentum", "hold": 1, "side": "long", "status": "closed", "entry_date": "2026-11-03", "entry_px": 10, "exit_date": "2026-11-04", "exit_px": 9, "pnl_pct": -10.1},
          {"id": "c", "symbol": "S3", "signal_date": "2026-11-03", "rule": "llm", "hold": 5, "side": "short", "status": "open", "entry_date": "2026-11-04", "entry_px": 10},
          {"id": "d", "symbol": "S4", "signal_date": "2026-11-04", "rule": "llm", "hold": 5, "side": "short", "status": "pending"}]
    n = build_lab.slim_news({"started": "2026-11-10", "shocks": sh, "trades": tr})
    assert len(n["shocks"]) == 40 and n["shocks"][0]["date"] == "2026-11-27" and len(n["shocks"][0]["headline"]) == 140 and "rationale" not in n["shocks"][0]
    assert n["n_closed"] == 2 and n["n_pending"] == 1 and [o["symbol"] for o in n["open"]] == ["S3"] and n["closed"][0] == ["2026-11-03", "momentum", 1, 9.9]
    g = n["by"]["rule"]["all shocks"]["momentum 1d"]
    assert g["n"] == 2 and g["win"] == 0.5 and g["avg"] == -0.1 and "earnings" in n["by"]["catalyst"] and "conf 0.5-0.7" in n["by"]["confidence"]
    assert build_lab.slim_qlib(None) is None
    q = build_lab.slim_qlib({"equity": 1, "picks": [{"symbol": f"S{i}", "score": 0.1, "rank": i} for i in range(50)] + ["junk"]})
    assert len(q["picks"]) == 20 and build_lab.slim_qlib({"equity": 1})["picks"] == []


def test_built_page_has_the_accounts_and_no_long_term():
    if not BUILT.exists():
        return
    m = re.search(r'<script id="lab" type="application/json">(.*?)</script>', BUILT.read_text(), re.S)
    D = json.loads(m.group(1).replace("<\\/", "</"))
    assert "system_qlib" in D and "system_ta" in D and "news_shadow" in D and "system_dts" not in D and "system_congress" not in D and "qlib_backtest" in D and "alpaca_intraday" in D and "report_cards" in D
    assert len(D["news_shadow"]["shocks"]) <= 40 and "weights" not in json.dumps(D["qlib_backtest"])
    assert "system_b" not in D and "system_c" not in D
    assert all("tearsheet" not in v for v in ((D["report_cards"] or {}).get("accounts") or {}).values())   # metrics only, no tear-sheet html
    for gone in ("alpaca", "research", "paper"):
        assert gone not in D
    assert D["book"] and all(b["family"] == "Intraday" for b in D["book"])
