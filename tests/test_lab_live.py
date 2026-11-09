import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from alpaca_paper import ledger_from_fills

TPL = ROOT / "report" / "lab_template.html"


def test_template_has_live_code():
    t = TPL.read_text()
    for needle in ("alpaca_live(", 'claude.use("mcp")', "watchTool", "execute_sql", "LIVE-PURE-BEGIN", 'id="live-badge"',
                   "Live data not set up yet", "Live unavailable"):
        assert needle in t
    for bad in ("APCA", "ALPACA_SECRET", "ALPACA_API_KEY", "insert into", "delete from"):
        assert bad not in t


def _js_ledger(fills, start):
    t = TPL.read_text()
    pure = t.split("// LIVE-PURE-BEGIN")[1].split("\n", 1)[1].split("// LIVE-PURE-END")[0]
    prog = pure + "\nconst IN = JSON.parse(require('fs').readFileSync(0, 'utf8'));\nconsole.log(JSON.stringify(ledgerFromFills(IN.fills, IN.start)));"
    r = subprocess.run(["node", "-e", prog], input=json.dumps({"fills": fills, "start": start}), capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_ledger_matches_python():
    rnd = random.Random(7)
    for trial in range(30):
        fills = []
        for i in range(rnd.randint(1, 60)):
            sym = rnd.choice(["SPY", "QQQ", "BTC/USD"])
            fills.append({"id": f"{i:04d}", "time": f"2026-11-0{rnd.randint(2, 6)}T{rnd.randint(14, 20)}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}.{rnd.randint(0, 999999):06d}Z",
                          "symbol": sym, "side": rnd.choice(["buy", "sell"]),
                          "qty": str(round(rnd.uniform(0.01, 9), 4)) if sym == "BTC/USD" else str(rnd.randint(1, 20)),
                          "price": str(round(rnd.uniform(50, 800), 2))})
        py = ledger_from_fills(fills, 25000.0)
        assert _js_ledger(fills, 25000.0) == py


def test_template_long_term_account_is_retired():
    t = TPL.read_text()
    assert "alpaca_live('lt')" not in t and 'lt: "select' not in t          # the retired account is never queried
    assert "Long-term account retired" in t and 'const RETIRED = "2026-11-09"' in t
    assert 'let lfilter = "intraday"' in t and 'getItem("lab-lfilter")' not in t   # always opens on Intraday
