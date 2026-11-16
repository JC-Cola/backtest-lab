"""qlib system data: our own daily US data in qlib's binary format (qlib's hosted US data is stale).

    python qlib_data.py build [--full] [--start 2008-01-01]   # download (incremental), then write data/qlib/us_data
    python qlib_data.py status

Source: Yahoo Finance via yfinance, daily, split+dividend ADJUSTED open/high/low/close (auto_adjust=True), so $factor = 1.
Universe: src/universe_fallback.txt (today's S&P 500 + Nasdaq-100, ~517 names; survivorship bias) plus SPY as benchmark.
Cache: data/qlib/csv/<SYM>.csv (gitignored; a GitHub Actions cache in the cloud). Incremental runs download only the last
TAIL_DAYS and rescale the stored history when a new dividend/split changed the adjustment (ratio of the overlap).
Binary layout (what qlib's dump_bin writes): calendars/day.txt, instruments/{all,sp500}.txt, features/<sym lower>/<field>.day.bin
= float32 [index of the first calendar day, v0, v1, ...].
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
QDIR = ROOT / "data" / "qlib"
CSV_DIR = QDIR / "csv"
BIN_DIR = QDIR / "us_data"
MODEL_DIR = QDIR / "models"
UNIVERSE_FILE = Path(__file__).resolve().parent / "universe_fallback.txt"
BENCH = "SPY"
START = "2008-01-01"
TAIL_DAYS = 12
FIELDS = ["open", "high", "low", "close", "volume", "factor"]
MIN_ROWS = 260           # a symbol with less than a year of data is not used


def universe() -> list[str]:
    out = []
    for ln in UNIVERSE_FILE.read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.extend(ln.split())
    return sorted(set(out))


def yahoo_sym(sym: str) -> str:
    return sym.replace(".", "-")


def _frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    return df[~df.index.duplicated(keep="last")].sort_index()


def download(symbols, start, end=None, chunk=60, retries=3, fetch=None):
    """{symbol: DataFrame(open, high, low, close, volume)} for the symbols Yahoo answered; the rest are missing."""
    import yfinance as yf
    fetch = fetch or (lambda tickers, s, e: yf.download(tickers, start=s, end=e, auto_adjust=True, group_by="ticker",
                                                         progress=False, threads=True))
    out = {}
    syms = list(symbols)
    for i in range(0, len(syms), chunk):
        part = syms[i:i + chunk]
        for attempt in range(retries):
            try:
                raw = fetch([yahoo_sym(s) for s in part], start, end)
                break
            except Exception as e:  # noqa: BLE001 - rate limits, resets
                print("  yahoo chunk %d retry %d: %s" % (i // chunk, attempt + 1, type(e).__name__), flush=True)
                time.sleep(5 * (attempt + 1))
                raw = None
        if raw is None or len(raw) == 0:
            continue
        for s in part:
            y = yahoo_sym(s)
            try:
                d = raw[y] if isinstance(raw.columns, pd.MultiIndex) else raw
                d = _frame(d)
            except (KeyError, ValueError):
                continue
            if len(d):
                out[s] = d
    return out


def merge(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    """Append `new` (a recent tail) to `old`. Adjusted prices change when a dividend or split goes ex, so old history is
    rescaled by the ratio of the two on their overlap before the join (constant ratio => same returns, same levels)."""
    if old is None or old.empty:
        return new
    both = old.index.intersection(new.index)
    old = old.copy()
    if len(both):
        ratio = float(np.median(new.loc[both, "close"].values / old.loc[both, "close"].values))
        if np.isfinite(ratio) and abs(ratio - 1.0) > 1e-6:
            old.loc[:, ["open", "high", "low", "close"]] *= ratio
    out = pd.concat([old[old.index < new.index.min()], new]).sort_index()
    return out[~out.index.duplicated(keep="last")]


def update_csv(symbols=None, full=False, start=START, fetch=None, today=None):
    symbols = list(symbols or universe()) + [BENCH]
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    today = today or dt.date.today()
    need_full, need_tail = [], []
    for s in symbols:
        p = CSV_DIR / ("%s.csv" % s)
        (need_full if full or not p.exists() else need_tail).append(s)
    got = {}
    if need_full:
        print("downloading full history for %d symbols" % len(need_full), flush=True)
        got.update({s: (None, d) for s, d in download(need_full, start, fetch=fetch).items()})
    if need_tail:
        since = (pd.Timestamp(today) - pd.Timedelta(days=TAIL_DAYS + 18)).strftime("%Y-%m-%d")
        print("updating the last days for %d symbols" % len(need_tail), flush=True)
        for s, d in download(need_tail, since, fetch=fetch).items():
            got[s] = (pd.read_csv(CSV_DIR / ("%s.csv" % s), index_col=0, parse_dates=True), d)
    failed = []
    for s in symbols:
        if s not in got:
            failed.append(s)
            continue
        old, new = got[s]
        merge(old, new).to_csv(CSV_DIR / ("%s.csv" % s), float_format="%.6g")
    return failed


# ---------------------------------------------------------------- qlib binary format

def write_bin(csv_dir=CSV_DIR, out=BIN_DIR, bench=BENCH):
    """Write the qlib provider directory from the CSVs. Returns {"symbols": n, "first": ..., "last": ..., "skipped": [...]}."""
    frames, skipped = {}, []
    for p in sorted(Path(csv_dir).glob("*.csv")):
        d = pd.read_csv(p, index_col=0, parse_dates=True)
        if len(d) < MIN_ROWS and p.stem != bench:
            skipped.append(p.stem)
            continue
        frames[p.stem] = d.sort_index()
    if bench not in frames:
        raise RuntimeError("no %s data: cannot build the trading calendar" % bench)
    cal = frames[bench].index                      # SPY's sessions = the trading calendar
    out = Path(out)
    for sub in ("calendars", "instruments", "features"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    (out / "calendars" / "day.txt").write_text("\n".join(d.strftime("%Y-%m-%d") for d in cal) + "\n")
    allrows, uni = [], []
    for sym, d in frames.items():
        d = d.reindex(cal).dropna(subset=["close"])
        if d.empty:
            continue
        i0, i1 = cal.get_loc(d.index[0]), cal.get_loc(d.index[-1])
        d = d.reindex(cal[i0:i1 + 1])
        d["factor"] = 1.0
        fdir = out / "features" / sym.lower()
        fdir.mkdir(parents=True, exist_ok=True)
        for f in FIELDS:
            np.hstack([[i0], d[f].to_numpy(dtype="float64")]).astype("<f").tofile(fdir / ("%s.day.bin" % f))
        row = "%s\t%s\t%s" % (sym.upper(), cal[i0].strftime("%Y-%m-%d"), cal[i1].strftime("%Y-%m-%d"))
        allrows.append(row)
        if sym != bench:
            uni.append(row)
    (out / "instruments" / "all.txt").write_text("\n".join(allrows) + "\n")
    (out / "instruments" / "sp500.txt").write_text("\n".join(uni) + "\n")
    return {"symbols": len(uni), "first": cal[0].strftime("%Y-%m-%d"), "last": cal[-1].strftime("%Y-%m-%d"),
            "skipped": skipped}


def coverage(out=BIN_DIR) -> dict:
    rows = [r.split("\t") for r in (Path(out) / "instruments" / "sp500.txt").read_text().split("\n") if r]
    last = max(r[2] for r in rows)
    first_by_year = pd.Series([int(r[1][:4]) for r in rows]).value_counts().sort_index()
    return {"symbols": len(rows), "last_day": last, "symbols_on_last_day": sum(1 for r in rows if r[2] == last),
            "listed_by_start_year": {str(k): int(v) for k, v in first_by_year.items()}}


def init_qlib(provider=BIN_DIR, kernels=4):
    """qlib.init on our data. joblib_backend="loky": qlib's default "multiprocessing" deadlocks on macOS (spawn)."""
    import qlib
    qlib.init(provider_uri=str(provider), region="us", kernels=kernels, joblib_backend="loky", expression_cache=None,
              dataset_cache=None, logging_level="WARNING")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--full", action="store_true")
    b.add_argument("--start", default=START)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        t0 = time.time()
        failed = update_csv(full=a.full, start=a.start)
        info = write_bin()
        info["download_failed"] = failed
        print(info, "%.0fs" % (time.time() - t0))
    else:
        print(coverage())
    return 0


if __name__ == "__main__":
    sys.exit(main())
