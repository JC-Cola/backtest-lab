"""The news-scan universe: current S&P 500 + Nasdaq-100 constituents that Alpaca lets us trade AND short.

    python universe.py [--refresh] [--write-fallback]

Order of preference (never raises, never returns an empty list):
  1. agent_memory/universe.csv, when it is less than REFRESH_DAYS old
  2. a rebuilt list: S&P 500 (GitHub datasets CSV) + Nasdaq-100 (nasdaq.com list) filtered by Alpaca's
     GET /v2/assets (tradable, shortable, easy_to_borrow); if the Alpaca call fails the filter is skipped
  3. the stale cache
  4. src/universe_fallback.txt (committed static list)
Idea borrowed from the day-trader-skill scanner: scan a broad, liquid universe built from public symbol lists rather
than a hand-picked watchlist. Cache lives in agent_memory/ (not data/, whose *.csv files are git-ignored) so the nightly
job commits it and the morning job can read it.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from report import MEM

CACHE = MEM / "universe.csv"
FALLBACK = Path(__file__).resolve().parent / "universe_fallback.txt"
REFRESH_DAYS = 7
MIN_SIZE = 300          # a rebuilt list smaller than this is treated as a failed download
SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
NDX_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity"
TIMEOUT = 30
SYM_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
UA = {"User-Agent": "Mozilla/5.0 (backtest-lab universe loader)", "Accept": "application/json, text/csv"}


def _get(url, headers=None, urlopen=urllib.request.urlopen):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def clean(symbols):
    """Upper-case, '-' -> '.' (BRK-B -> BRK.B, Alpaca's spelling), drop anything that is not a plain ticker, dedupe, sort."""
    out = {str(s).strip().upper().replace("-", ".").replace("/", ".") for s in symbols}
    return sorted(s for s in out if SYM_RE.match(s))


def sp500(urlopen=urllib.request.urlopen):
    return clean(r["Symbol"] for r in csv.DictReader(io.StringIO(_get(SP500_URL, urlopen=urlopen))))


def nasdaq100(urlopen=urllib.request.urlopen):
    rows = json.loads(_get(NDX_URL, urlopen=urlopen))["data"]["data"]["rows"]
    return clean(r["symbol"] for r in rows)


def alpaca_tradable(key=None, secret=None, urlopen=urllib.request.urlopen):
    """Symbols with tradable and shortable and easy_to_borrow all true. Raises on HTTP errors / missing keys."""
    key, secret = key or os.environ.get("ALPACA_API_KEY"), secret or os.environ.get("ALPACA_SECRET_KEY")
    if not (key and secret):
        raise RuntimeError("no Alpaca keys")
    rows = json.loads(_get(ASSETS_URL, {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, urlopen))
    return {a["symbol"] for a in rows if a.get("tradable") and a.get("shortable") and a.get("easy_to_borrow")}


def rebuild(urlopen=urllib.request.urlopen, tradable_fn=alpaca_tradable):
    """-> (symbols, note). Raises RuntimeError when the constituent lists cannot be had (or are too small)."""
    got, errs = set(), []
    for name, fn in (("S&P 500", sp500), ("Nasdaq-100", nasdaq100)):
        try:
            got.update(fn(urlopen))
        except Exception as ex:
            errs.append(f"{name}: {type(ex).__name__}")
    if len(got) < MIN_SIZE:
        raise RuntimeError(f"constituent lists unavailable or too small ({len(got)}; {', '.join(errs) or 'ok'})")
    note = "constituents" + (f" (partial: {', '.join(errs)})" if errs else "")
    try:
        ok = tradable_fn()
        kept = sorted(got & ok)
        if len(kept) < MIN_SIZE:
            raise RuntimeError(f"only {len(kept)} tradable")
        return kept, f"{note} + Alpaca tradable/shortable filter"
    except Exception as ex:
        return sorted(got), f"{note}; Alpaca filter skipped ({type(ex).__name__})"


def read_cache(path=CACHE):
    """-> (symbols, refreshed date) or ([], None)."""
    try:
        rows = list(csv.DictReader(open(path)))
        syms = clean(r["symbol"] for r in rows)
        return syms, date.fromisoformat(rows[0]["refreshed"]) if syms else None
    except (OSError, ValueError, KeyError, IndexError):
        return [], None


def write_cache(symbols, today, path=CACHE):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "refreshed"])
        w.writerows([[s, str(today)] for s in symbols])


def read_fallback(path=FALLBACK):
    try:
        return clean(re.findall(r"[A-Za-z][A-Za-z.\-]*", re.sub(r"#.*", "", open(path).read())))
    except OSError:
        return []


def load(today=None, refresh=True, write=True, urlopen=urllib.request.urlopen, tradable_fn=alpaca_tradable,
         cache=CACHE, fallback=FALLBACK):
    """-> (symbols, source). refresh=False never touches the network (morning job); write=False never writes the cache."""
    today = today or date.today()
    cached, when = read_cache(cache)
    if cached and when and (not refresh or today - when < timedelta(days=REFRESH_DAYS)):
        return cached, f"cache {when}"
    if refresh:
        try:
            syms, note = rebuild(urlopen, tradable_fn)
            if write:
                write_cache(syms, today, cache)
            return syms, f"rebuilt: {note}"
        except Exception as ex:
            print(f"universe: rebuild failed ({type(ex).__name__}: {str(ex)[:100]})", file=sys.stderr)
    if cached:
        return cached, f"stale cache {when}"
    fb = read_fallback(fallback)
    if fb:
        return fb, "static fallback"
    return [], "none"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="rebuild now and rewrite the cache")
    ap.add_argument("--write-fallback", action="store_true", help="also rewrite src/universe_fallback.txt from the result")
    a = ap.parse_args(argv)
    if a.refresh:
        syms, note = rebuild()
        write_cache(syms, date.today())
        src = f"rebuilt: {note}"
    else:
        syms, src = load()
    if a.write_fallback and syms:
        FALLBACK.write_text("# Static S&P 500 + Nasdaq-100 fallback (tradable and shortable on Alpaca), used when downloads fail.\n"
                            f"# Written {date.today()} by `python universe.py --write-fallback`.\n" + "\n".join(syms) + "\n")
    print(f"{len(syms)} symbols ({src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
