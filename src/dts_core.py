"""System B shared bits: config, vendored-module loader, small time/bar helpers (see system_dts.py)."""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "third_party" / "day_trader_skill"
STATE_PATH = ROOT / "agent_memory" / "system_dts.json"
UNIVERSE_PATH = ROOT / "agent_memory" / "dts_universe.txt"
SEC_UA = "backtest-lab research JC-Cola@users.noreply.github.com"   # SEC wants a real contact: repo owner's noreply

CFG = {
    "risk_pct": 0.5,              # % of equity risked per trade (max loss at the stop)
    "max_concurrent": 4,
    "max_position_pct": 25,       # % of equity per position
    "or_minutes": 15,
    "scan_at": (10, 15),          # scan from this New York time ...
    "scan_latest": (11, 30),      # ... until this one (a late dispatch still scans once; after it, the day is skipped)
    "min_price": 5.0,
    "min_volume": 50_000,         # shares traded by the cutoff (our liquidity floor; the scanner's own default is 0)
    "max_confirm": 300,           # stage-2 v8 charts, most traded first
    "max_eval": 20,               # ranked setups pushed through the pipeline per scan
    "news_hours": 18,
    "distress_items": {"1.03", "1.05", "2.04", "2.06", "3.01", "4.02", "5.01"},
    "tm_replays": 10000, "tm_seed": 19, "tm_min_lean": 0.05, "odds_gate": True, "tm_hist_min_trades": 10,
    "telegram_cap": 10,
    "universe_days": 7,
}

_MODS: dict = {}


def vend(folder: str, name: str):
    key = folder + "/" + name
    if key not in _MODS:
        spec = importlib.util.spec_from_file_location("dts_" + name, VENDOR / folder / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODS[key] = mod
    return _MODS[key]


def scan_m(): return vend("trader-scanner", "scan")
def news_m(): return vend("trader-news", "news")
def entry_m(): return vend("trader-entry", "entry")
def exit_m(): return vend("trader-exit", "exit")
def tm_m(): return vend("trader-timemachine", "timemachine")
def size_m(): return vend("trader-budget", "size")
def risk_m(): return vend("trader-risk", "check")





def hhmm(t) -> dt.time:
    return dt.time(*t)


def floor5(et: dt.datetime) -> dt.datetime:
    return et.replace(minute=et.minute - et.minute % 5, second=0, microsecond=0)


def bars_of(series, day, step_min, close_t):
    """Series (scanner shape) -> sorted bars of `day` on the grid inside the session."""
    s = scan_m()
    out = {}
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        et = s.ts_to_eastern(ts)
        if et.date() != day or et.second or et.minute % step_min or et.time() < s.OPEN or et.time() >= close_t:
            continue
        g = lambda k: series[k][i] if series.get(k) and series[k][i] is not None else c
        out[et] = {"start": et, "end": et + dt.timedelta(minutes=step_min), "o": float(g("o")), "h": float(g("h")),
                   "l": float(g("l")), "c": float(c)}
    return [out[k] for k in sorted(out)]


