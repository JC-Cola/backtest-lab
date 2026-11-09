"""Pre-market "gappers with news" for the morning briefing (shown only, never traded).

    python news_gappers.py        # print what the morning card would show

Idea from day-trader-skill's pre-market scan: stocks gapping vs the prior close with a fresh catalyst and real
pre-market volume. For every symbol of the universe (read from the cache/fallback, no network refresh) one batched
Alpaca snapshot call set gives the latest price, the prior daily close and today's pre-market volume; symbols gapping
by >= MIN_GAP with >= MIN_PM_VOL shares traded and a news article in the last NEWS_HOURS hours (market-wide news,
fetched once) are ranked by |gap| and the top N returned. Never raises: any failure gives [].
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import news_scan
from report import NY

SNAP_URL = "https://data.alpaca.markets/v2/stocks/snapshots"
MIN_GAP = 0.03
MIN_PRICE = 5.0
MIN_PM_VOL = 1000      # shares; the free IEX feed shows only a few percent of consolidated volume, so keep this low
NEWS_HOURS = 16
TOP_N = 5
CHUNK = 100


def fetch_snapshots(symbols, key=None, secret=None, urlopen=urllib.request.urlopen, chunk=CHUNK):
    """{symbol: snapshot} for all symbols, `chunk` per request, concurrently. Tries SIP once, then IEX for everything
    (the free plan refuses recent SIP data). Raises when no feed works."""
    headers = news_scan._keys(key, secret)

    def one(batch, feed):
        q = urllib.parse.urlencode({"symbols": ",".join(batch), "feed": feed})
        with urlopen(urllib.request.Request(f"{SNAP_URL}?{q}", headers=headers), timeout=news_scan.TIMEOUT) as r:
            return json.load(r)

    batches = news_scan.chunks(symbols, chunk)
    last = None
    for feed in ("sip", "iex"):
        try:
            out = {}
            with ThreadPoolExecutor(max_workers=news_scan.WORKERS) as pool:
                for part in pool.map(lambda b: one(b, feed), batches):
                    out.update(part)
            return out
        except Exception as ex:
            last = ex
    raise last


def gap_rows(snaps, today, min_gap=MIN_GAP, min_price=MIN_PRICE, min_pm_vol=MIN_PM_VOL):
    """Pure: [{'symbol','gap','price','prev_close','pm_vol'}] from snapshots, biggest |gap| first. `today` = New York
    date string; pre-market volume is today's daily bar volume (0 when that bar is not from today)."""
    out = []
    for sym, s in snaps.items():
        try:
            prev = float(s["prevDailyBar"]["c"])
            px = float((s.get("latestTrade") or {})["p"])
            bar = s.get("dailyBar") or {}
            vol = float(bar.get("v") or 0) if str(bar.get("t", ""))[:10] == today else 0.0
        except (KeyError, TypeError, ValueError):
            continue
        if prev <= 0 or px < min_price or vol < min_pm_vol:
            continue
        g = px / prev - 1
        if abs(g) >= min_gap:
            out.append({"symbol": sym, "gap": round(g, 5), "price": round(px, 2), "prev_close": round(prev, 2), "pm_vol": int(vol)})
    out.sort(key=lambda r: (-abs(r["gap"]), r["symbol"]))
    return out


def gappers(symbols, now, snap_fn=fetch_snapshots, news_fn=news_scan.fetch_news_window, n=TOP_N):
    """Top `n` pre-market gappers with news: [{'symbol','gap','price','pm_vol','headline'}]. Never raises."""
    try:
        now = now.astimezone(NY)
        rows = gap_rows(snap_fn(symbols), str(now.date()))
        if not rows:
            return []
        start = now - timedelta(hours=NEWS_HOURS)
        nmap = news_scan.news_by_symbol(news_fn(start, now), {r["symbol"] for r in rows}, start.astimezone(timezone.utc),
                                        now.astimezone(timezone.utc), n=1)
        return [{**r, "headline": nmap[r["symbol"]][0]} for r in rows if nmap.get(r["symbol"])][:n]
    except Exception as ex:
        print(f"gappers failed: {type(ex).__name__}: {str(ex)[:80]}", file=sys.stderr)
        return []


def gappers_now(now=None, n=TOP_N):
    """Morning entry point: universe from the cache (or static fallback), then gappers(). Never raises."""
    try:
        import universe
        now = now or datetime.now(NY)
        syms, _ = universe.load(now.astimezone(NY).date(), refresh=False, write=False)
        return gappers(syms, now, n=n) if syms else []
    except Exception as ex:
        print(f"gappers failed: {type(ex).__name__}: {str(ex)[:80]}", file=sys.stderr)
        return []


def fmt_vol(v):
    return f"{v / 1e6:.1f}M" if v >= 1e6 else f"{v / 1e3:.0f}K" if v >= 1e3 else str(int(v))


def main():
    for g in gappers_now():
        print(f"{g['symbol']} {g['gap'] * 100:+.1f}% @ {g['price']} pm vol {fmt_vol(g['pm_vol'])}  {g['headline']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
