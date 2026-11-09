"""Alpaca News API (Benzinga) download + jsonl cache: data/news/<SYM>.jsonl (gitignored). Reusable.

    python src/news_data.py NVDA AAPL ...      # needs ALPACA_API_KEY / ALPACA_SECRET_KEY in the environment

Each line: {id, created_at (UTC ISO), headline, summary (<=500 chars), symbols, source, tags?, channels?}.
Resumable: a re-run continues from the newest cached created_at. Keys are read from the environment and never printed.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

DIR = Path(__file__).resolve().parent.parent / "data" / "news"
URL = "https://data.alpaca.markets/v1beta1/news"
START = "2015-12-01T00:00:00Z"
ET = "US/Eastern"


def _headers():
    import alpaca_keys
    k, s = alpaca_keys.data_keys()
    if not (k and s):
        raise RuntimeError("no working Alpaca data keys (see src/alpaca_keys.py)")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}


def slim(a: dict) -> dict:
    out = {"id": a["id"], "created_at": a["created_at"], "headline": a.get("headline") or "",
           "summary": (a.get("summary") or "")[:500], "symbols": a.get("symbols") or [], "source": a.get("source")}
    for k in ("tags", "channels"):
        if a.get(k):
            out[k] = a[k]
    return out


def _get(params, headers, tries=8):
    for i in range(tries):
        try:
            r = requests.get(URL, params=params, headers=headers, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(60, 2 ** i))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(min(60, 2 ** i))
    raise RuntimeError("news request kept failing")


def path(sym: str) -> Path:
    return DIR / f"{sym}.jsonl"


def last_created(sym: str):
    p = path(sym)
    if not p.exists() or p.stat().st_size == 0:
        return None
    last = None
    with p.open() as f:
        for line in f:
            last = line
    return json.loads(last)["created_at"] if last else None


def download(sym: str, end: str | None = None, verbose=True) -> int:
    """Download (or resume) all articles tagged `sym`; returns the number of NEW articles written."""
    DIR.mkdir(parents=True, exist_ok=True)
    headers = _headers()
    start = last_created(sym) or START
    seen = set()
    p = path(sym)
    if p.exists():
        with p.open() as f:
            seen = {json.loads(l)["id"] for l in f}
    params = {"symbols": sym, "start": start, "limit": 50, "sort": "asc", "include_content": "false"}
    if end:
        params["end"] = end
    new, page = 0, 0
    with p.open("a") as f:
        while True:
            j = _get(params, headers)
            for a in j.get("news") or []:
                if a["id"] in seen:
                    continue
                seen.add(a["id"])
                f.write(json.dumps(slim(a)) + "\n")
                new += 1
            page += 1
            if verbose and page % 50 == 0:
                print(f"  {sym}: {page} pages, {new} new, at {a['created_at'] if j.get('news') else '-'}", flush=True)
            tok = j.get("next_page_token")
            if not tok:
                break
            params["page_token"] = tok
            time.sleep(0.35)          # stay under 200 requests/minute
    return new


def load(sym: str) -> pd.DataFrame:
    """Cached articles as a DataFrame (created_at tz-aware US/Eastern), sorted, de-duplicated by id."""
    rows = [json.loads(l) for l in path(sym).open()] if path(sym).exists() else []
    df = pd.DataFrame(rows, columns=["id", "created_at", "headline", "summary", "symbols", "source", "tags", "channels"])
    if df.empty:
        df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
        return df
    df = df.drop_duplicates("id")
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True).dt.tz_convert(ET)
    return df.sort_values("created_at").reset_index(drop=True)


if __name__ == "__main__":
    for s in sys.argv[1:]:
        n = download(s)
        print(f"{s}: {n} new articles, {len(load(s))} cached", flush=True)
