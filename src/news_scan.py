"""Market-wide data helpers for the nightly news-shock scan (all read-only Alpaca data calls).

  fetch_bars        daily bars for hundreds of symbols in a few batched, concurrent multi-symbol requests
  fetch_news_window ALL market news in a time window (no symbols filter, paged) in a handful of calls
  news_by_symbol    maps those articles to universe symbols -> {symbol: [headline, ...]} (specific stories first)
  features          day-trader-skill style extras for one shock: relative volume, gap, SPY move, beta, abnormal move
  rank_score        |abnormal move| x relative volume

Bars are (date, open, close, volume) tuples, oldest first (older 3-tuples without volume still work everywhere).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
MARKET = "SPY"
CHUNK = 100            # symbols per bars request (100 x ~90 sessions fits one 10000-row page; paging handles the rest)
WORKERS = 4
TIMEOUT = 30
NEWS_PAGE = 50         # the News API maximum
NEWS_MAX_PAGES = 120   # safety stop: 6000 articles in one window
BETA_DAYS = 60
BETA_MIN = 40
RELVOL_DAYS = 20


def _keys(key, secret):
    key, secret = key or os.environ.get("ALPACA_API_KEY"), secret or os.environ.get("ALPACA_SECRET_KEY")
    if not (key and secret):
        raise RuntimeError("no Alpaca keys")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def chunks(items, n=CHUNK):
    items = list(items)
    return [items[i:i + n] for i in range(0, len(items), n)]


def _bars_chunk(symbols, start, end, feed, headers, urlopen):
    out, token = {}, None
    while True:
        q = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": str(start), "end": str(end + timedelta(days=1)),
             "limit": 10000, "adjustment": "split", "feed": feed}
        if token:
            q["page_token"] = token
        with urlopen(urllib.request.Request(f"{BARS_URL}?{urllib.parse.urlencode(q)}", headers=headers), timeout=TIMEOUT) as r:
            j = json.load(r)
        for sym, rows in (j.get("bars") or {}).items():
            out.setdefault(sym, []).extend((b["t"][:10], float(b["o"]), float(b["c"]), float(b.get("v") or 0)) for b in rows)
        token = j.get("next_page_token")
        if not token:
            return out


def fetch_bars(symbols, start, end, key=None, secret=None, urlopen=urllib.request.urlopen, chunk=CHUNK, workers=WORKERS):
    """{symbol: [(date, open, close, volume), ...]} split-adjusted daily bars. Batches of `chunk` symbols, `workers`
    requests at a time; each batch tries SIP, then IEX. A batch that fails both raises (the caller decides)."""
    headers = _keys(key, secret)

    def one(batch):
        last = None
        for feed in ("sip", "iex"):
            try:
                return _bars_chunk(batch, start, end, feed, headers, urlopen)
            except Exception as ex:
                last = ex
        raise last

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(one, chunks(symbols, chunk)):
            for sym, rows in part.items():
                out.setdefault(sym, []).extend(rows)
    return {k: sorted(set(v)) for k, v in out.items()}


def fetch_news_window(start, end=None, key=None, secret=None, urlopen=urllib.request.urlopen, max_pages=NEWS_MAX_PAGES):
    """Every article between `start` and `end` (aware datetimes), market-wide, newest first, following next_page_token.
    Raises on the first HTTP error (a partial window would silently hide news)."""
    headers = _keys(key, secret)
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, token = [], None
    for _ in range(max_pages):
        q = {"start": fmt(start), "limit": NEWS_PAGE, "sort": "desc"}
        if end is not None:
            q["end"] = fmt(end)
        if token:
            q["page_token"] = token
        with urlopen(urllib.request.Request(f"{NEWS_URL}?{urllib.parse.urlencode(q)}", headers=headers), timeout=TIMEOUT) as r:
            j = json.load(r)
        out += j.get("news") or []
        token = j.get("next_page_token")
        if not token:
            break
    else:
        print(f"news: stopped after {max_pages} pages ({len(out)} articles)", file=sys.stderr)
    return out


def news_by_symbol(articles, symbols, start=None, end=None, n=6):
    """{symbol: [headline, ...]} for symbols in `symbols`: articles inside [start, end] (when given) mentioning the
    symbol; stories that tag few symbols first (more company-specific), then newest; deduped; at most n each."""
    want = set(symbols)
    tmp = {}
    for a in articles:
        h = " ".join(str(a.get("headline", "")).split())
        if not h:
            continue
        try:
            t = datetime.fromisoformat(str(a.get("created_at", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if (start and t < start) or (end and t > end):
            continue
        syms = [s for s in (a.get("symbols") or []) if s]
        for s in syms:
            if s in want:
                tmp.setdefault(s, []).append((len(syms), -t.timestamp(), h))
    out = {}
    for s, rows in tmp.items():
        rows.sort()
        heads = []
        for _, _, h in rows:
            if h not in heads:
                heads.append(h)
        out[s] = heads[:n]
    return out


def _vol(row):
    return row[3] if len(row) > 3 else None


def _returns_by_date(bars):
    return {bars[k][0]: bars[k][2] / bars[k - 1][2] - 1 for k in range(1, len(bars)) if bars[k - 1][2]}


def beta_vs(bars, mkt_bars, day, days=BETA_DAYS, min_pts=BETA_MIN):
    """OLS beta of the stock's daily returns on the market's over the `days` sessions BEFORE `day`; None if too little data."""
    sr, mr = _returns_by_date(bars), _returns_by_date(mkt_bars)
    dates = sorted(d for d in sr if d in mr and d < day)[-days:]
    if len(dates) < min_pts:
        return None
    x, y = [mr[d] for d in dates], [sr[d] for d in dates]
    mx, my = statistics.fmean(x), statistics.fmean(y)
    var = sum((a - mx) ** 2 for a in x)
    if var <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / var


def features(bars, day, ret, mkt_bars=None):
    """Extras for a shock on `day` (the stock's own close-to-close `ret` is given). Missing inputs -> None fields.
    relvol = day volume / mean volume of the previous 20 sessions; gap = open / previous close - 1;
    spy_ret = market move that day; beta from the 60 sessions before; abn = ret - beta * spy_ret
    (beta falls back to 1.0 when it cannot be estimated, so a market-wide move is still netted out)."""
    dates = [b[0] for b in bars]
    i = dates.index(day)
    f = {"relvol": None, "gap": None, "spy_ret": None, "beta": None, "abn": round(ret, 6)}
    v = _vol(bars[i])
    prior = [x for x in (_vol(b) for b in bars[max(0, i - RELVOL_DAYS):i]) if x]
    if v and len(prior) >= 10:
        f["relvol"] = round(v / statistics.fmean(prior), 3)
    if i >= 1 and bars[i - 1][2]:
        f["gap"] = round(bars[i][1] / bars[i - 1][2] - 1, 6)
    if mkt_bars:
        md = _returns_by_date(mkt_bars)
        if day in md:
            b = beta_vs(bars, mkt_bars, day)
            f["spy_ret"] = round(md[day], 6)
            f["beta"] = None if b is None else round(b, 3)
            f["abn"] = round(ret - (1.0 if b is None else max(-1.0, min(3.0, b))) * md[day], 6)
    return f


def rank_score(rec):
    """|abnormal move| x relative volume (relative volume counts as 1 when unknown)."""
    return abs(rec.get("abn", rec.get("ret", 0.0))) * (rec.get("relvol") or 1.0)
