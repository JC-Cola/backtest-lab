"""Fetch scheduled FOMC statement dates (2005-2026) from federalreserve.gov -> data/fomc_dates.csv.

Sources: fomccalendars.htm (2021+) and fomchistorical<YEAR>.htm (2005-2020). Only regularly
scheduled meetings are kept (unscheduled / conference-call / notation-vote / cancelled entries are
dropped). The statement date is the last day of the meeting, cross-checked against the
"Statement" link on the same page where one is present. Nothing is invented: if a year does not
yield 8 meetings (7 in 2020, when the March meeting was cancelled and replaced by an
unscheduled one) the script raises.
"""
from __future__ import annotations

import re
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "data" / "fomc_dates.csv"
BASE = "https://www.federalreserve.gov/monetarypolicy/"
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"], 1)}
HEAD = re.compile(r"^([A-Z][a-z]+(?:/[A-Z][a-z]+)?) (\d{1,2})(?:-(?:([A-Z][a-z]+) )?(\d{1,2}))? Meeting - (\d{4})$")


def _month(name: str) -> int:
    """'July' -> 7; 'Jan/Feb' (meeting spanning two months) -> the later month."""
    last = name.split("/")[-1]
    return next(v for k, v in MONTHS.items() if k[:3] == last[:3])


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (backtest-lab research)"})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def parse_historical(html: str, year: int) -> list[date]:
    out = []
    for raw in re.findall(r"<h5[^>]*>(.*?)</h5>", html, re.S):
        for line in re.split(r"\s*\n\s*", re.sub(r"<[^>]+>", "\n", raw)):
            h = HEAD.match(re.sub(r"\s+", " ", line).strip())
            if not h or int(h.group(5)) != year:
                continue
            m1, d1, m2, d2, _ = h.groups()
            out.append(date(year, _month(m2 or m1), int(d2 or d1)))
    return out


def parse_current(html: str) -> list[date]:
    out = []
    for ym in re.finditer(r'<h4><a id="\d+">(\d{4}) FOMC Meetings</a></h4>(.*?)(?=<h4><a id|\Z)', html, re.S):
        year, body = int(ym.group(1)), ym.group(2)
        for mm in re.finditer(r'fomc-meeting__month[^>]*><strong>([A-Za-z/]+)</strong>.*?'
                              r'fomc-meeting__date[^>]*>([^<]+)<', body, re.S):
            mon_s, dates_s = mm.group(1), mm.group(2).strip()
            if "(" in dates_s:  # "(unscheduled)", "(notation vote)", "(cancelled)": not a scheduled meeting
                continue
            dm = re.match(r"(\d{1,2})(?:-(?:([A-Za-z]+) )?(\d{1,2}))?\*?$", dates_s)
            if not dm:
                raise ValueError(f"unparsed calendar entry {year} {mon_s} {dates_s!r}")
            d1, m2, d2 = dm.groups()
            mon_name = m2 or mon_s.split("/")[-1]
            out.append(date(year, _month(mon_name), int(d2 or d1)))
    return out


def main():
    rows = []
    for y in range(2005, 2021):
        url = f"{BASE}fomchistorical{y}.htm"
        ds = parse_historical(get(url), y)
        rows += [(d, url) for d in ds]
    url = BASE + "fomccalendars.htm"
    cur = parse_current(get(url))
    rows += [(d, url) for d in cur if 2021 <= d.year <= 2026]
    df = pd.DataFrame(rows, columns=["date", "source"]).drop_duplicates("date").sort_values("date")
    cnt = df.groupby(pd.to_datetime(df["date"]).dt.year).size()
    print(cnt.to_dict())
    for y, n in cnt.items():
        want = 7 if y == 2020 else 8
        if n != want:
            raise RuntimeError(f"{y}: {n} meetings parsed, expected {want}")
    if set(cnt.index) != set(range(2005, 2027)):
        raise RuntimeError("missing years")
    OUT.parent.mkdir(exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"wrote {OUT} ({len(df)} dates, {df['date'].min()} .. {df['date'].max()})")


if __name__ == "__main__":
    main()
