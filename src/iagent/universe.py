"""The intraday agent's research universe: SPY, QQQ plus the N most-traded liquid US stocks/ETFs, refreshed monthly and
stored in agent_memory/intraday/universe.json.

Liquidity comes from the vendored day-trader-skill scanner (third_party/day_trader_skill/trader-scanner/scan.py, used
unmodified): its Yahoo spark reader pre-ranks the candidates (price floor + last-session dollar volume), its v8 chart
reader gives 250 sessions of closes and volumes for the median dollar volume. Candidates are the S&P 500 + Nasdaq-100
list of src/universe.py plus a few liquid ETFs.

LOCKBOX: about 20% of the names are drawn at random (seeded by the refresh month) and NEVER used in search. They are
sticky: once a name is a lockbox name it stays one (ever_lockbox), and a name that was ever searched can never become one.
A held-out pass must also hold on the lockbox names (see shots.lockbox_check)."""
from __future__ import annotations

import json
import random
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

CORE = ["SPY", "QQQ"]
ETFS = ["IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "XLY", "XLP", "XLI", "XLU", "SMH", "TLT", "GLD", "SLV", "HYG", "EEM", "ARKK"]
N_NAMES, LOCKBOX_FRAC, MIN_PRICE, REFRESH_DAYS, PRE_TOP, SESSIONS = 40, 0.2, 5.0, 30, 100, 250
SCANNER_DIR = Path(__file__).resolve().parents[2] / "third_party" / "day_trader_skill" / "trader-scanner"
FILE = "universe.json"


def scanner():
    """The vendored scanner module (imported in place, never modified)."""
    if str(SCANNER_DIR) not in sys.path:
        sys.path.insert(0, str(SCANNER_DIR))
    import scan
    return scan


def default() -> dict:
    return {"refreshed": None, "search": list(CORE), "lockbox": [], "ever_lockbox": [], "ranked": []}


def read(root: Path) -> dict | None:
    try:
        d = json.loads((Path(root) / FILE).read_text())
        return d if d.get("search") else None
    except (OSError, ValueError):
        return None


def fetch_liquidity(symbols, scan=None, pause: float = 0.0) -> dict:
    """{symbol: {"price", "median_dollar_volume", "sessions"}} using the scanner's readers. Stage 1: spark (20 symbols a
    request) keeps price >= MIN_PRICE and ranks by last-session dollar volume; stage 2: v8 daily chart of the PRE_TOP best,
    median of close x volume over the last SESSIONS sessions. Symbols Yahoo does not answer are simply missing."""
    scan = scan or scanner()
    pre = {}
    for i in range(0, len(symbols), scan.SPARK_MAX):
        chunk = symbols[i:i + scan.SPARK_MAX]
        try:
            got = scan.parse_spark(scan.yahoo_get(scan.spark_path(chunk, "5d", "1d")), {scan.to_yahoo(s): s for s in chunk})
        except Exception as ex:                     # a failed chunk is a gap, not a crash
            print(f"universe: spark chunk failed ({type(ex).__name__})", file=sys.stderr)
            continue
        for sym, ser in got.items():
            meta = ser.get("meta") or {}
            px, vol = meta.get("regularMarketPrice"), meta.get("regularMarketVolume")
            if px is not None and px >= MIN_PRICE and vol:
                pre[sym] = px * vol
        time.sleep(pause)
    out = {}
    for sym in sorted(pre, key=pre.get, reverse=True)[:PRE_TOP]:
        try:
            ser = scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, "1y", "1d")), sym)
        except Exception:
            continue
        dv = [c * v for c, v in zip(ser["c"], ser.get("v") or []) if c is not None and v][-SESSIONS:]
        if len(dv) >= 100:
            out[sym] = {"price": round(float(ser["c"][-1] or 0), 2), "median_dollar_volume": float(statistics.median(dv)),
                        "sessions": len(dv)}
        time.sleep(pause)
    return out


def select(liq: dict, today: date, prev: dict | None = None, ever_search=(), n: int = N_NAMES) -> dict:
    """Pure: the n most-traded names (price >= MIN_PRICE) -> universe dict. Core SPY/QQQ are always searched and never
    lockbox. Lockbox: previous lockbox names stay (sticky), topped up to LOCKBOX_FRAC of n by a seeded random draw from
    names that were never searched and never lockbox."""
    prev = prev or default()
    ranked = sorted((s for s, v in liq.items() if v["price"] >= MIN_PRICE and s not in CORE),
                    key=lambda s: -liq[s]["median_dollar_volume"])[:n]
    ever_lock = set(prev.get("ever_lockbox", [])) | set(prev.get("lockbox", []))
    lock = [s for s in ranked if s in ever_lock]
    pool = sorted(s for s in ranked if s not in ever_lock and s not in set(ever_search) and s not in set(prev.get("search", [])))
    need = max(0, round(LOCKBOX_FRAC * n) - len(lock))
    lock += random.Random(f"lockbox-{today:%Y-%m}").sample(pool, min(need, len(pool)))
    lock = sorted(lock)
    search = CORE + [s for s in ranked if s not in lock]
    return {"refreshed": str(today), "n": n, "search": search, "lockbox": lock,
            "ever_lockbox": sorted(ever_lock | set(lock)),
            "ranked": [{"symbol": s} | liq[s] for s in ranked]}


def ensure(root: Path, today: date | None = None, candidates=None, fetch=fetch_liquidity, ever_search=None) -> dict:
    """Read agent_memory/intraday/universe.json, rebuilding it when missing or older than REFRESH_DAYS. Never raises:
    on failure the old file (or SPY/QQQ only) is used."""
    today = today or date.today()
    cur = read(root)
    if cur and cur.get("refreshed") and today - date.fromisoformat(cur["refreshed"]) < timedelta(days=REFRESH_DAYS):
        return cur
    try:
        if candidates is None:
            import universe as base
            candidates = base.load(today=today)[0]
        syms = sorted(set(candidates) | set(ETFS) - set(CORE))
        liq = fetch(syms)
        if len(liq) < 10:
            raise RuntimeError(f"only {len(liq)} liquid symbols answered")
        if ever_search is None:
            ever_search = {json.loads(l)["spec"]["market"] for l in (Path(root) / "journal.jsonl").read_text().splitlines()
                           if l.strip()} if (Path(root) / "journal.jsonl").exists() else set()
        uni = select(liq, today, cur, ever_search)
        Path(root).mkdir(parents=True, exist_ok=True)
        (Path(root) / FILE).write_text(json.dumps(uni, indent=1) + "\n")
        return uni
    except Exception as ex:
        print(f"universe: refresh failed ({type(ex).__name__}: {str(ex)[:100]}); using {'the old file' if cur else 'SPY/QQQ only'}",
              file=sys.stderr)
        return cur or default()


def run_names(uni: dict, k: int = 6, seed: int = 0) -> list[str]:
    """SPY/QQQ plus a rotating subset of k other search names (memory and download budget of one 30-minute run)."""
    others = [s for s in uni["search"] if s not in CORE]
    return CORE + sorted(random.Random(seed).sample(others, min(k, len(others))))
