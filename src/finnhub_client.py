"""Tiny Finnhub REST client (https://finnhub.io/docs/api, free tier ~60 calls/min).

Token from env FINNHUB_API_KEY (never printed or logged). Every public function returns [] / None when the key is missing,
the endpoint is not on the free plan (HTTP 403 / "You don't have access") or anything else goes wrong: it never raises into
callers. Calls are paced to <= 50/min, a 429 is retried with backoff, answers are cached on disk (data/finnhub/, git-ignored)
with a TTL per endpoint (a 403 is cached too, so a paid-only endpoint costs one call a day).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

BASE = "https://finnhub.io/api/v1"
ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "finnhub"
MAX_PER_MIN = 50
RETRIES = 3
TIMEOUT = 20
HOUR, DAY = 3600, 86400
TTL = {"calendar/earnings": 6 * HOUR, "stock/earnings": DAY, "stock/recommendation": DAY, "stock/insider-transactions": DAY,
       "company-news": HOUR, "news-sentiment": DAY}
DENIED_TTL = DAY


class Denied(Exception):
    """Endpoint is not on this plan (403)."""


class _Limiter:
    """Sliding window: at most `n` calls in any 60 s. Injectable clock/sleep for tests."""

    def __init__(self, n=MAX_PER_MIN, clock=time.monotonic, sleep=time.sleep):
        self.n, self.clock, self.sleep, self.stamps, self.lock = n, clock, sleep, [], threading.Lock()

    def wait(self):
        with self.lock:
            now = self.clock()
            self.stamps = [t for t in self.stamps if now - t < 60]
            if len(self.stamps) >= self.n:
                pause = 60 - (now - self.stamps[0]) + 0.05
                self.sleep(max(0.0, pause))
                now = self.clock()
                self.stamps = [t for t in self.stamps if now - t < 60]
            self.stamps.append(self.clock())


_limiter = _Limiter()


def token():
    return os.environ.get("FINNHUB_API_KEY") or None


def available():
    return token() is not None


def _cache_path(path, params, cache_dir):
    q = urllib.parse.urlencode(sorted(params.items()))
    h = hashlib.sha1(f"{path}?{q}".encode()).hexdigest()[:16]
    return Path(cache_dir) / f"{path.replace('/', '_')}_{h}.json"


def _read_cache(p, ttl, now):
    try:
        j = json.loads(p.read_text())
        if now - float(j["t"]) <= ttl:
            return True, j["data"], j.get("denied", False)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return False, None, False


def _write_cache(p, data, denied, now):
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"t": now, "data": data, "denied": denied}))
    except OSError:
        pass


def _fetch(path, params, urlopen, sleep, limiter):
    """One GET with pacing and 429 backoff. Raises Denied on 403, other errors propagate."""
    q = urllib.parse.urlencode({**params, "token": token()})
    delay = 2.0
    for attempt in range(RETRIES + 1):
        limiter.wait()
        try:
            req = urllib.request.Request(f"{BASE}/{path}?{q}", headers={"User-Agent": "backtest-lab"})
            with urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as ex:
            if ex.code == 403:
                raise Denied(path) from None
            if ex.code == 429 and attempt < RETRIES:
                sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("retries exhausted")


def get(path, params=None, ttl=None, urlopen=urllib.request.urlopen, sleep=time.sleep, now=time.time, cache_dir=None,
        limiter=None):
    """JSON for `path` or None (no key / not on the plan / error). Cached for `ttl` seconds (default per endpoint)."""
    if not token():
        return None
    params = {k: v for k, v in (params or {}).items() if v is not None}
    ttl = TTL.get(path, HOUR) if ttl is None else ttl
    p = _cache_path(path, params, cache_dir or CACHE_DIR)
    t = now()
    hit, data, denied = _read_cache(p, ttl, t)
    if hit:
        return None if denied else data
    hit, _, denied = _read_cache(p, DENIED_TTL, t)
    if hit and denied:
        return None
    try:
        data = _fetch(path, params, urlopen, sleep, limiter or _limiter)
    except Denied:
        _write_cache(p, None, True, t)
        return None
    except Exception as ex:
        print(f"finnhub: {path} failed ({type(ex).__name__})", file=sys.stderr)
        return None
    if isinstance(data, dict) and "error" in data:   # e.g. {"error": "You don't have access to this resource."}
        if "access" in str(data["error"]).lower():
            _write_cache(p, None, True, t)
        return None
    _write_cache(p, data, False, t)
    return data


def _d(x):
    return x.isoformat() if isinstance(x, (date, datetime)) else str(x)


def earnings_calendar(frm, to, symbol=None, **kw):
    """[{symbol, date, hour (bmo/amc/dmh), epsEstimate, epsActual, revenueEstimate, revenueActual, quarter, year}]"""
    j = get("calendar/earnings", {"from": _d(frm), "to": _d(to), "symbol": symbol}, **kw)
    return list(j.get("earningsCalendar") or []) if isinstance(j, dict) else []


def earnings_surprises(symbol, **kw):
    """[{symbol, period, actual, estimate, surprise, surprisePercent, quarter, year}] newest first (free plan: ~4 quarters)."""
    j = get("stock/earnings", {"symbol": symbol}, **kw)
    return list(j) if isinstance(j, list) else []


def recommendation_trends(symbol, **kw):
    """[{symbol, period, strongBuy, buy, hold, sell, strongSell}] newest first."""
    j = get("stock/recommendation", {"symbol": symbol}, **kw)
    return list(j) if isinstance(j, list) else []


def insider_transactions(symbol, frm=None, to=None, **kw):
    j = get("stock/insider-transactions", {"symbol": symbol, "from": _d(frm) if frm else None, "to": _d(to) if to else None}, **kw)
    return list(j.get("data") or []) if isinstance(j, dict) else []


def company_news(symbol, frm, to, **kw):
    j = get("company-news", {"symbol": symbol, "from": _d(frm), "to": _d(to)}, **kw)
    return list(j) if isinstance(j, list) else []


def news_sentiment(symbol, **kw):
    """dict or None (premium on the free plan)."""
    j = get("news-sentiment", {"symbol": symbol}, **kw)
    return j if isinstance(j, dict) and j else None


def rec_change(trends):
    """Analyst recommendation change between the two newest months: {'period','score','prev_score','delta'} or None.
    score = (2*strongBuy + buy - sell - 2*strongSell) / total analysts, in [-2, 2]."""
    def score(r):
        n = sum(int(r.get(k) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
        if not n:
            return None
        return (2 * int(r.get("strongBuy") or 0) + int(r.get("buy") or 0) - int(r.get("sell") or 0) - 2 * int(r.get("strongSell") or 0)) / n
    rows = sorted((r for r in trends or [] if isinstance(r, dict) and r.get("period")), key=lambda r: r["period"], reverse=True)
    if len(rows) < 2:
        return None
    a, b = score(rows[0]), score(rows[1])
    if a is None or b is None:
        return None
    return {"period": rows[0]["period"], "score": round(a, 3), "prev_score": round(b, 3), "delta": round(a - b, 3)}


def latest_surprise(rows, before=None):
    """Newest surprise row with period <= before (a date), as {'period','actual','estimate','surprise_pct'} or None."""
    ok = [r for r in rows or [] if isinstance(r, dict) and r.get("period") and r.get("surprisePercent") is not None
          and (before is None or str(r["period"]) <= str(before))]
    if not ok:
        return None
    r = max(ok, key=lambda r: r["period"])
    return {"period": r["period"], "actual": r.get("actual"), "estimate": r.get("estimate"),
            "surprise_pct": round(float(r["surprisePercent"]), 2)}
