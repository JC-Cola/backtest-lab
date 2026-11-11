"""System B backtest data: Alpaca 5-minute bars (SIP, split/dividend adjusted), cached in data/dts/<SYM>_5m.pkl.

5-minute is the native bar of the day-trader-skill tools (scanner, entry timer, time machine all read 5-min bars);
1-minute would be ~5x the rows (~30M for 100+ symbols over 2.5 years) for no change in the pipeline's decisions.
Timestamps are bar START times in New York time, regular hours only (09:30 <= start < 16:00).

    python src/dts_data.py            # download CANDIDATES from START (needs any Alpaca paper key pair in the environment)
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

DIR = Path(__file__).resolve().parent.parent / "data" / "dts"
START = "2023-09-01"   # warm-up: the daily scanner needs 30 closes and the time machine ~60 days before 2024-01-01
NY = "America/New_York"
CANDIDATES = """AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO ORCL NFLX AMD COST WMT JPM V MA UNH XOM LLY HD PG JNJ BAC
ABBV KO PEP CRM ADBE CSCO INTC QCOM TXN MU AMAT LRCX KLAC PLTR UBER SHOP COIN MSTR SMCI ARM PANW CRWD SNOW NOW
INTU IBM DIS NKE SBUX MCD BA CAT GE DE HON LMT RTX UPS FDX T VZ TMUS CMCSA WFC C GS MS BLK SCHW AXP PYPL SQ
PFE MRK BMY GILD AMGN CVS MRNA CVX COP OXY SLB F GM RIVN LCID NIO BABA PDD JD TSM ASML SOFI HOOD RBLX DKNG
ABNB DASH SNAP PINS ROKU ZM DOCU NET DDOG MDB TTD AFRM UPST CHWY ETSY LULU TGT LOW CMG MRVL ON WDC STX DELL
HPQ ANET MPWR VRT AAL DAL UAL CCL NCLX RCL MGM WYNN FCX NEM CLF AA X SPY QQQ""".split()


def _client():
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca_keys import data_keys
    k, sec = data_keys()
    if not k:
        raise SystemExit("no Alpaca keys in the environment")
    return StockHistoricalDataClient(k, sec)


def download(sym: str, cl=None, start=START) -> pd.DataFrame:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    cl = cl or _client()
    cap = datetime.now(timezone.utc) - timedelta(minutes=20)   # free plan: no SIP data from the last 15 minutes
    parts = []
    for y in range(int(start[:4]), cap.year + 1):
        a = datetime.fromisoformat(max(start, f"{y}-01-01")).replace(tzinfo=timezone.utc)
        b = min(datetime.fromisoformat(f"{y}-12-31").replace(hour=23, minute=59, tzinfo=timezone.utc), cap)
        df = None
        for attempt in range(6):
            try:
                df = cl.get_stock_bars(StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                                                        start=a, end=b, feed=DataFeed.SIP, adjustment=Adjustment.ALL)).df
                break
            except Exception as e:  # noqa: BLE001 - retry transient API errors (rate limits)
                print(f"  {sym} {y}: retry {attempt + 1} after {type(e).__name__}", flush=True)
                time.sleep(5 * (attempt + 1))
        if df is None:
            raise RuntimeError(f"{sym} {y}: Alpaca kept failing; not cached")
        if not df.empty:
            parts.append(df.droplevel(0))
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated()]
    df.index = df.index.tz_convert(NY)
    t = df.index.hour * 60 + df.index.minute
    df = df[(t >= 570) & (t < 960)][["open", "high", "low", "close", "volume"]]
    DIR.mkdir(parents=True, exist_ok=True)
    df.to_pickle(DIR / f"{sym}_5m.pkl")
    return df


def load(sym: str) -> pd.DataFrame:
    return pd.read_pickle(DIR / f"{sym}_5m.pkl")


def main(symbols=None):
    cl = _client()
    todo = [s for s in (symbols or CANDIDATES) if not (DIR / f"{s}_5m.pkl").exists()]
    t0 = time.time()

    def one(s):
        try:
            df = download(s, cl)
            print(f"{s}: {len(df):,} bars ({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{s}: FAILED {e}", flush=True)
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(one, todo))


if __name__ == "__main__":
    main(sys.argv[1:] or None)
