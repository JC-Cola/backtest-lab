"""Congress data: House Clerk Periodic Transaction Reports (PTRs) through the vendored trader-pelosi tool, plus daily prices.

The upstream tool (third_party/day_trader_skill/trader-pelosi/ptr.py, vendored unmodified) does the real work: it reads the
Clerk's yearly index (<YEAR>FD.zip) and parses each PTR PDF (via poppler's `pdftotext -layout`). This file only wraps it:

  * index_filings()  - PTR filings by followed members (no PDF download)
  * read_filing()    - download + parse one PTR into normalised rows, or an "unparsed" record (never a guess)
  * to_records()     - one dict per disclosed transaction, shaped for the shadow ledger (see system_congress.py)
  * daily_bars()     - daily open/close per symbol: Alpaca (data_keys) when keys exist, else yfinance (local runs)

House only: the Senate (efdsearch.senate.gov) sits behind an agreement a person must accept, and the tool documents a manual
route only, so it is NOT automated here. Everything is a PUBLIC disclosure filed up to 45 days after the trade.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PTR_PY = ROOT / "third_party" / "day_trader_skill" / "trader-pelosi" / "ptr.py"
SEC_UA = "backtest-lab research JC-Cola@users.noreply.github.com"
CACHE = ROOT / "data" / "congress"

# Asset-type codes on a PTR: ST stock, OP option, and others we do not copy (bonds, funds, crypto, real estate, ...).
EQUITY_CODES = {"ST", "ET"}          # stock, ETF/ETN-like shares (the Clerk uses ST for common stock)
_PTR = None


def ptr():
    """The vendored ptr.py module, loaded once, with the SEC-style contact user agent (module attribute, file untouched)."""
    global _PTR
    if _PTR is None:
        spec = importlib.util.spec_from_file_location("congress_ptr", PTR_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.UA = SEC_UA
        _PTR = mod
    return _PTR


# ---------------------------------------------------------------- filings index

def index_filings(members, years, since=None, offline=None):
    """PTR filings (FilingType P) for `members` (last names or 'First Last') in the given years, oldest first.
    Each: doc_id, name, last, first, district, year, filed (ISO), url."""
    p = ptr()
    out = []
    for y in years:
        try:
            rows = p.load_index(y, offline)
        except p.PtrError:
            if y == max(years):
                raise            # the current year's index must be reachable; an older year missing is only skipped
            continue
        out += p.select(rows, members, "P", since)
    out = [r for r in out if r["filed"]]
    out.sort(key=lambda r: (r["filed"], r["doc_id"]))
    return out


def member_key(row, members):
    """Which configured member (as written in FOLLOW) this index row belongs to."""
    p = ptr()
    for m in members:
        if p.member_match(row, m):
            return m
    return row["last"]


# ---------------------------------------------------------------- one filing

def read_filing(row, cache_dir=CACHE / "pdfs", text_of=None):
    """Download and parse one PTR. Returns (rows, reason): rows is a list of parsed transactions (possibly empty) and
    reason is None on success or a short string when the PDF could not be read (scanned image, no pdftotext, bad PDF,
    zero rows, or fewer rows parsed than the PDF has date pairs, e.g. the 2020 lowercase layout). `text_of(path)` is a
    test hook replacing pdftotext."""
    p = ptr()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    pdf = cache_dir / ("%s.pdf" % row["doc_id"])
    try:
        if not pdf.exists():
            data = p.download(row["url"])
            if not data.startswith(b"%PDF"):
                return [], "not a PDF"
            pdf.write_bytes(data)
    except p.PtrError as e:
        return [], "download failed: %s" % e
    text = (text_of or p.pdf_to_text)(str(pdf))
    if text is None:
        return [], "pdftotext unavailable"
    if len(text.strip()) < 80:
        return [], "scanned image (no text layer)"
    rep = p.parse_ptr_text(text, filed=row["filed"])
    if not rep["rows"]:
        return [], "no transaction rows recognised"
    expected = len(re.findall(r"\d{2}/\d{2}/\d{4}\s+\d{2}/\d{2}/\d{4}", text))   # one trade-date + notified-date pair per row
    if len(rep["rows"]) < expected:
        return rep["rows"], "parsed %d of %d transaction rows; the rest are unread" % (len(rep["rows"]), expected)
    return rep["rows"], None


def parse_text(text, filed):
    """Parse already-extracted `pdftotext -layout` text (tests, offline)."""
    return ptr().parse_ptr_text(text, filed=filed)["rows"]


# ---------------------------------------------------------------- normalised records

def fmt_money(n):
    if n is None:
        return "?"
    if n >= 1_000_000:
        v = n / 1_000_000
        return ("%gM" % round(v, 1)) if v != int(v) else "%dM" % int(v)
    if n >= 1_000:
        return "%gK" % round(n / 1_000, 1)
    return "%d" % n


def amount_label(low, high, raw=""):
    """'$1M–5M', '$1K–15K', 'over $50M' from the range; falls back to the filing's own text."""
    if low is None:
        return raw or "?"
    if high is None:
        return "over $%s" % fmt_money(low)
    return "$%s–%s" % (fmt_money(low), fmt_money(high))


def midpoint(low, high):
    if low is None:
        return None
    return float(low) if high is None else (low + high) / 2.0


def classify(row):
    """(kind, note): kind is one of buy / sell_full / sell_partial (copied) or option / exchange / nonequity / noticker (recorded only)."""
    code = (row.get("asset_type") or "").upper()
    desc = (row.get("description") or "").lower()
    opt = (row.get("option") is not None or code == "OP" or "call option" in desc or "put option" in desc
           or desc.startswith("exercised"))
    t = row.get("type")
    if opt:
        return "option", "option contract, recorded not traded"
    if t == "E":
        return "exchange", "exchange, not a clear buy or sell"
    if not row.get("ticker"):
        return "noticker", "no ticker (private or non-listed asset)"
    if code and code not in EQUITY_CODES:
        return "nonequity", "asset type %s, not a listed stock" % code
    if t == "P":
        return "buy", ""
    if t == "S":
        return "sell_full", ""
    if t == "S (partial)":
        return "sell_partial", ""
    return "nonequity", "unknown transaction type %s" % t


def to_records(member, row, rows, first_seen=None):
    """One record per parsed transaction of filing `row`. Dates are ISO; lag_days = filed - traded."""
    recs = []
    for i, r in enumerate(rows):
        kind, note = classify(r)
        low, high = r.get("amount_low"), r.get("amount_high")
        traded = r.get("date")
        lag = (dt.date.fromisoformat(row["filed"]) - dt.date.fromisoformat(traded)).days if traded else None
        recs.append({"key": "%s:%d" % (row["doc_id"], i), "doc_id": row["doc_id"], "member": member, "filed": row["filed"],
                     "traded": traded, "ticker": r.get("ticker"), "asset": r.get("asset"), "type": r.get("type_name"),
                     "kind": kind, "owner": r.get("owner_name"), "amount_range": amount_label(low, high, r.get("amount", "")),
                     "amount_low": low, "amount_high": high, "mid": midpoint(low, high), "lag_days": lag,
                     "link": row["url"], "first_seen": first_seen, "note": note,
                     "status": "recorded" if kind in ("option", "exchange", "nonequity", "noticker") else "pending"})
    return recs


def unparsed_record(member, row, reason, first_seen=None):
    return {"key": "%s:unparsed" % row["doc_id"], "doc_id": row["doc_id"], "member": member, "filed": row["filed"],
            "traded": None, "ticker": None, "asset": None, "type": None, "kind": "unparsed", "owner": None,
            "amount_range": None, "amount_low": None, "amount_high": None, "mid": None, "lag_days": None,
            "link": row["url"], "first_seen": first_seen, "note": reason, "status": "unparsed"}


# ---------------------------------------------------------------- prices

def _alpaca_bars(symbols, start, end, key, secret):
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    cl = StockHistoricalDataClient(key, secret)
    cap = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)    # free plan: SIP lags 15 minutes
    a = dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc)
    b = min(dt.datetime.fromisoformat(end).replace(hour=23, minute=59, tzinfo=dt.timezone.utc), cap)
    out = {}
    syms = list(symbols)
    for i in range(0, len(syms), 40):
        chunk = syms[i:i + 40]
        df = cl.get_stock_bars(StockBarsRequest(symbol_or_symbols=chunk, timeframe=TimeFrame.Day, start=a, end=b,
                                                feed=DataFeed.SIP, adjustment=Adjustment.ALL)).df
        if df.empty:
            continue
        for s in chunk:
            if s in df.index.get_level_values(0):
                d = df.xs(s, level=0)
                d.index = [x.date().isoformat() for x in d.index.tz_convert("America/New_York")]
                out[s] = {k: {"o": float(r["open"]), "c": float(r["close"])} for k, r in d.iterrows()}
    return out


def _yf_bars(symbols, start, end):
    import yfinance as yf
    out = {}
    for s in symbols:
        try:
            df = yf.Ticker(s.replace(".", "-")).history(start=start, end=(dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat(),
                                                        auto_adjust=True)
        except Exception:  # noqa: BLE001 - a dead ticker simply has no bars
            continue
        if df is None or df.empty:
            continue
        out[s] = {x.date().isoformat(): {"o": float(r["Open"]), "c": float(r["Close"])} for x, r in df.iterrows()
                  if r["Open"] == r["Open"] and r["Close"] == r["Close"]}
    return out


def daily_bars(symbols, start, end, source="auto"):
    """{symbol: {date: {"o": open, "c": close}}} split+dividend adjusted. Alpaca daily bars (data_keys()) when keys are set,
    else yfinance (local runs). A symbol with no data is simply absent."""
    symbols = sorted(set(symbols))
    if not symbols:
        return {}
    if source in ("auto", "alpaca"):
        from alpaca_keys import data_keys
        k, s = data_keys()
        if k:
            return _alpaca_bars(symbols, start, end, k, s)
        if source == "alpaca":
            raise RuntimeError("no Alpaca data keys in the environment")
    return _yf_bars(symbols, start, end)


def cached_bars(symbols, start, end, source="auto"):
    """daily_bars with a per-symbol JSON cache in data/congress/bars (local studies; never used by the nightly job)."""
    d = CACHE / "bars"
    d.mkdir(parents=True, exist_ok=True)
    out, miss = {}, []
    for s in sorted(set(symbols)):
        f = d / ("%s.json" % s.replace("/", "_"))
        if f.exists():
            got = json.loads(f.read_text())
            if got and max(got) >= end[:10]:
                out[s] = got
                continue
            if not got and f.stat().st_mtime > dt.datetime.now().timestamp() - 7 * 86400:
                continue          # known-empty symbol, looked up within the week
        miss.append(s)
    if miss:
        fresh = daily_bars(miss, start, end, source)
        for s in miss:
            (d / ("%s.json" % s.replace("/", "_"))).write_text(json.dumps(fresh.get(s, {})))
            if s in fresh:
                out[s] = fresh[s]
    return out
