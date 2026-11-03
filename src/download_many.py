"""Download study-2 minute bars (2019 on) for many symbols in parallel (4 at a time)."""
import sys
from concurrent.futures import ThreadPoolExecutor

import intraday_data

SYMS = sys.argv[1:]


def one(s):
    try:
        if (intraday_data.DIR / f"{s}.pkl").exists():
            return f"{s}: already there"
        df = intraday_data.download(s, start="2019-01-01")
        return f"{s}: {len(df):,} bars"
    except Exception as e:
        return f"{s}: FAILED {str(e)[:120]}"


with ThreadPoolExecutor(4) as ex:
    for msg in ex.map(one, SYMS):
        print(msg, flush=True)
