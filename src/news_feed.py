"""Top headlines for the morning briefing, from the Alpaca News API (last ~24h).

    python news_feed.py           # print what the morning card would show (sends nothing)

Queries (1) SPY, QQQ, NVDA + the intraday account's holdings and (2) market-wide news, dedupes, prefers symbols we
hold or trade, then newest first. Never raises: any failure gives []. Keys: alpaca_keys.data_keys() (intraday key first).
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import alpaca_keys
from report import MEM, read_json

URL = "https://data.alpaca.markets/v1beta1/news"
CORE = ("SPY", "QQQ", "NVDA")
N_SHOWN = 5
PER_TAG = 2          # at most this many headlines with the same tag
TIMEOUT = 15


def held_symbols(positions=None):
    """Symbols of the intraday holdings: from `positions` (briefing data) or agent_memory/alpaca_intraday.json."""
    if positions is None:
        positions = (read_json(MEM / "alpaca_intraday.json") or {}).get("positions", [])
    return [str(p["symbol"]).replace("/", "") for p in positions if p.get("symbol")]


def fetch(symbols, start, limit=50, key=None, secret=None, urlopen=urllib.request.urlopen, end=None):
    """One News API call: articles (newest first). symbols=None means market-wide. Raises on HTTP/network errors."""
    if not (key and secret):
        key, secret = alpaca_keys.data_keys()
    if not (key and secret):
        raise RuntimeError("no Alpaca keys")
    q = {"start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": limit, "sort": "desc"}
    if end is not None:
        q["end"] = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if symbols:
        q["symbols"] = ",".join(symbols)
    req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(q)}",
                                 headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
    with urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r).get("news", [])


def _norm(h):
    return re.sub(r"[^a-z0-9]+", " ", h.lower()).strip()[:60]


def pick(articles, wanted, n=N_SHOWN, per_tag=PER_TAG):
    """Choose n articles. `articles` need headline, symbols, created_at. wanted = symbols we hold/trade (upper-case).
    Priority: mentions a wanted symbol (CORE first), then newest. Returns [{'tag','headline','ts'}]."""
    seen, cands = set(), []
    for a in articles:
        h = " ".join(str(a.get("headline", "")).split())
        k = _norm(h)
        if not h or k in seen:
            continue
        seen.add(k)
        syms = [s for s in a.get("symbols", []) if s]
        hit = [s for s in syms if s.replace("/", "") in wanted]
        tag = hit[0] if hit else "MKT"
        rank = 0 if hit else 1
        cands.append((rank, -_ts(a).timestamp(), {"tag": tag, "headline": h, "ts": a.get("created_at", "")}))
    cands.sort(key=lambda c: (c[0], c[1]))
    out, count = [], {}
    for _, _, c in cands:
        if count.get(c["tag"], 0) >= per_tag:
            continue
        count[c["tag"]] = count.get(c["tag"], 0) + 1
        out.append(c)
        if len(out) == n:
            break
    return out


def _ts(a):
    try:
        return datetime.fromisoformat(str(a.get("created_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, timezone.utc)


def headlines(positions=None, now=None, fetcher=fetch, n=N_SHOWN):
    """The ~5 most relevant headlines of the last 24h as [{'tag','headline','ts'}]; [] on any error."""
    try:
        now = now or datetime.now(timezone.utc)
        start = now - timedelta(hours=24)
        held = held_symbols(positions)
        syms = list(dict.fromkeys([*CORE, *held]))
        arts, errs = [], 0
        for s in (syms, None):
            try:
                arts += fetcher(s, start)
            except Exception as ex:
                errs += 1
                print(f"news fetch failed: {type(ex).__name__}: {str(ex)[:80]}", file=sys.stderr)
        if errs == 2:
            return []
        return pick(arts, set(syms), n)
    except Exception as ex:
        print(f"news_feed failed: {type(ex).__name__}: {str(ex)[:80]}", file=sys.stderr)
        return []


def main():
    for h in headlines():
        print(f"[{h['tag']}] {h['headline']}  ({h['ts']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
