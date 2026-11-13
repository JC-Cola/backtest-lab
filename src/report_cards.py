"""Report cards (quantstats) for every account: Noise band, qlib, TradingAgents, day-trader-skill (shadow).

    python report_cards.py            # all accounts: agent_memory/report_cards.json + reports/cards/<account>.html

Daily returns come from each account's snapshot (agent_memory/*.json, `calendar` = equity at every close; falls back to
the daily `history.ALL` points), starting from the account's start equity. qlib also gets a second card from the daily
returns in results/qlib15.json when that file exists. The benchmark is SPY (daily closes from data.load).
A series shorter than MIN_DAYS gets no metrics and no tear sheet ("not enough history yet (N days)"): quantstats
errors or gives meaningless numbers on a handful of points. Read-only: no Alpaca keys, no orders, never raises.
agent_memory/report_cards.json holds metrics only (the Strategy Lab and the evening briefing read it); the tear sheets
are standalone HTML files.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MEM = ROOT / "agent_memory"
OUT_JSON = MEM / "report_cards.json"
CARD_DIR = ROOT / "reports" / "cards"
MIN_DAYS = 5
NY = ZoneInfo("America/New_York")
# (key, label, snapshot file). Keys match the Strategy Lab's switch values.
ACCOUNTS = [("intraday", "Noise band", "alpaca_intraday.json"),
            ("qlib", "qlib", "system_qlib.json"),
            ("ta", "TradingAgents", "system_ta.json"),
            ("dts_shadow", "day-trader-skill (shadow)", "system_dts.json")]
QLIB_BACKTEST = ("qlib_bt", "qlib backtest", ROOT / "results" / "qlib15.json")
METRICS = ("cagr", "sharpe", "sortino", "max_dd", "win_days", "best_day", "worst_day", "vol", "beta")


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _num(x):
    """float or None (NaN / inf / non-numbers become None, so the JSON stays valid)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _naive(idx):
    idx = pd.DatetimeIndex(pd.to_datetime(idx))
    return idx.tz_localize(None) if idx.tz is not None else idx


def returns_from_equity(points, start_equity):
    """Daily returns from [(date, equity at the close)]; the first day is measured against `start_equity`."""
    pts = sorted((pd.Timestamp(d).normalize(), float(e)) for d, e in points if e)
    pts = list(dict(pts).items())   # one point per date (the last one wins)
    if not pts:
        return pd.Series(dtype=float)
    idx = [pts[0][0] - pd.tseries.offsets.BDay(1)] + [p[0] for p in pts]
    eq = pd.Series([float(start_equity or pts[0][1])] + [p[1] for p in pts], index=_naive(idx))
    return eq.pct_change().dropna().rename("returns")


def snapshot_returns(snap):
    """Daily return series of an account snapshot, or an empty series when it has no daily equity yet."""
    if not isinstance(snap, dict):
        return pd.Series(dtype=float)
    start = snap.get("start_equity") or 25000.0
    cal = [(c["date"], c["equity"]) for c in snap.get("calendar") or [] if isinstance(c, dict) and c.get("date") and c.get("equity")]
    if cal:
        return returns_from_equity(cal, start)
    allp = ((snap.get("history") or {}).get("ALL")) or []   # [[unix ts, equity]], daily points
    pts = [(datetime.fromtimestamp(t, timezone.utc).astimezone(NY).date(), e) for t, e in allp if e]
    if len(pts) >= 2:   # the first point is the start balance, not a trading day
        return returns_from_equity(pts[1:], pts[0][1])
    return pd.Series(dtype=float)


def file_returns(path):
    """Daily returns from a results file: {"daily_returns"|"returns"|"daily": {date: r} | [[date, r]] | [{"date","ret"}]}."""
    d = _read(path)
    if not isinstance(d, (dict, list)):
        return pd.Series(dtype=float)
    raw = d
    if isinstance(d, dict):
        raw = next((d[k] for k in ("daily_returns", "returns", "daily") if d.get(k)), None)
    try:
        if isinstance(raw, dict):
            pairs = list(raw.items())
        else:
            pairs = [(x["date"], x.get("ret", x.get("return", x.get("r")))) if isinstance(x, dict) else (x[0], x[1]) for x in raw or []]
        s = pd.Series({pd.Timestamp(k).normalize(): float(v) for k, v in pairs if _num(v) is not None})
        s.index = _naive(s.index)
        return s.sort_index().rename("returns")
    except (TypeError, ValueError, KeyError, IndexError):
        return pd.Series(dtype=float)


def spy_returns():
    """SPY daily returns (benchmark) or None when the data is not available."""
    try:
        import data
        c = data.load("SPY")["Close"]
        return c.pct_change().dropna().rename("SPY")
    except Exception as ex:
        print(f"benchmark unavailable ({type(ex).__name__})")
        return None


def compute(returns, benchmark=None, html_path=None, title=""):
    """Card dict for one returns series. Metrics need MIN_DAYS points; the tear sheet is written when html_path is given."""
    n = int(len(returns))
    out = {"days": n, "start": None, "end": None, "ok": False, "note": "", "metrics": None, "tearsheet": None}
    if n:
        out["start"], out["end"] = str(returns.index[0].date()), str(returns.index[-1].date())
    if n < MIN_DAYS:
        out["note"] = f"not enough history yet ({n} day{'' if n == 1 else 's'})"
        return out
    import warnings
    import quantstats as qs
    bench = None
    if benchmark is not None:
        bench = benchmark.reindex(returns.index).dropna()
        bench = bench if len(bench) >= MIN_DAYS else None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = {"cagr": qs.stats.cagr(returns), "sharpe": qs.stats.sharpe(returns), "sortino": qs.stats.sortino(returns),
                 "max_dd": qs.stats.max_drawdown(returns), "win_days": qs.stats.win_rate(returns),
                 "best_day": qs.stats.best(returns), "worst_day": qs.stats.worst(returns), "vol": qs.stats.volatility(returns),
                 "beta": None}
            if bench is not None:
                try:
                    m["beta"] = qs.stats.greeks(returns.reindex(bench.index), bench)["beta"]
                except Exception:
                    pass
        out["metrics"] = {k: _num(v) for k, v in m.items()}
        out["ok"] = True
    except Exception as ex:
        out["note"] = f"metrics failed ({type(ex).__name__})"
        return out
    if html_path is not None:
        try:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                qs.reports.html(returns, benchmark=bench, output=str(html_path), title=title, benchmark_title="SPY")
            out["tearsheet"] = str(html_path.relative_to(ROOT)) if html_path.is_relative_to(ROOT) else str(html_path)
        except Exception as ex:
            print(f"{title}: tear sheet failed ({type(ex).__name__}: {str(ex)[:80]})")
    return out


def build(mem=MEM, card_dir=CARD_DIR, benchmark="load", qlib_bt=QLIB_BACKTEST):
    """The whole report_cards.json payload. `benchmark`: a returns Series, None (none), or "load" (SPY)."""
    bench = spy_returns() if isinstance(benchmark, str) else benchmark
    accounts = {}
    for key, label, fname in ACCOUNTS:
        card = compute(snapshot_returns(_read(Path(mem) / fname)), bench, Path(card_dir) / f"{key}.html", label)
        accounts[key] = {"name": label, **card}
    if qlib_bt and Path(qlib_bt[2]).exists():
        card = compute(file_returns(qlib_bt[2]), bench, Path(card_dir) / f"{qlib_bt[0]}.html", qlib_bt[1])
        accounts[qlib_bt[0]] = {"name": qlib_bt[1], **card}
    return {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "benchmark": "SPY", "min_days": MIN_DAYS,
            "accounts": accounts}


# ---------- text for the Telegram briefing ----------
def _pct(x, d=1):
    return ("−" if x < 0 else "") + f"{abs(x) * 100:.{d}f}%"


def line(card):
    """'Noise band: Sharpe 1.2 · max DD −1.1% · 6 days' (or the not-enough-history note)."""
    if not card.get("ok") or not card.get("metrics"):
        return f"{card['name']}: {card.get('note') or 'no data'}"
    m, d = card["metrics"], card["days"]
    parts = []
    if m.get("sharpe") is not None:
        parts.append(f"Sharpe {m['sharpe']:.1f}")
    if m.get("max_dd") is not None:
        parts.append(f"max DD {_pct(m['max_dd'])}")
    parts.append(f"{d} day{'' if d == 1 else 's'}")
    return f"{card['name']}: " + " · ".join(parts)


def briefing_lines(path=OUT_JSON):
    """One line per account (the live-trading accounts and the shadow ledger) from report_cards.json; [] when missing."""
    d = _read(path)
    if not isinstance(d, dict):
        return []
    accts = d.get("accounts") or {}
    return [line(accts[k]) for k, _, _ in ACCOUNTS if isinstance(accts.get(k), dict)]


def main():
    try:
        payload = build()
    except Exception as ex:
        print(f"report cards failed: {type(ex).__name__}: {str(ex)[:120]}")
        return 0
    tmp = OUT_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1) + "\n")
    os.replace(tmp, OUT_JSON)
    for k, a in payload["accounts"].items():
        print(line({**a, "name": f"{k:<10}"}) + (f" · {a['tearsheet']}" if a.get("tearsheet") else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
