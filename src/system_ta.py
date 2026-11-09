"""System C: TradingAgents (multi-agent LLM framework) run in parallel with our system, on the Google Gemini FREE API tier
(llm_provider "google", GOOGLE_API_KEY). Shadow only: NO real orders.

    python system_ta.py [--asof YYYY-MM-DD] [--tickers A,B] [--max-tickers N] [--force] [--dry-run] [--list-models]

Nightly after the close: watchlist = SPY, QQQ, NVDA + the day's biggest news movers (agent_memory/news_shadow.json),
capped at TA_MAX_TICKERS (default 5) to protect the subscription. TradingAgents rates each ticker for the day's date; the
five-tier rating becomes a shadow position (ta_ledger.py: entry at the NEXT open, 20%/10% of a $25,000 account, shorts
allowed). Ledger: agent_memory/system_ta.json. One Telegram message per night. Auth/limit failure -> "no decision" for
the rest of the night, one Telegram alert per UTC day with the exact re-login command. Never raises into the workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import ta_ledger as L

ROOT = Path(__file__).resolve().parents[1]
MEM = ROOT / "agent_memory"
PATH = MEM / "system_ta.json"
NEWS_PATH = MEM / "news_shadow.json"
MEMORY_LOG = MEM / "ta_memory.md"   # TradingAgents' own decision log / lessons (no secrets), committed with the ledger
NY = ZoneInfo("America/New_York")
BASE = ("SPY", "QQQ", "NVDA")
MAX_TICKERS = 5
DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
PROVIDER = "google"                       # TradingAgents v0.7.1 provider name; key from GOOGLE_API_KEY
QUICK_MODEL, DEEP_MODEL = "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"   # the Flash models have only 20 requests/day free
# Measured on the free tier (429 bodies, 2026-11-09): per-project, per-model requests per minute. Daily limits are not
# published and not visible until hit. Our own limiter stays under these.
FREE_RPM = {"gemini-3.5-flash-lite": 15, "gemini-3.1-flash-lite": 15, "gemini-3.5-flash": 5, "gemini-3-flash-preview": 5}   # gemini-3.5-flash also has RPD 20 (routes to 3.6-flash)
RPM_MARGIN = 0.8
MOVER_SLOTS = 2


# ---------- watchlist ----------
def movers(day, path=NEWS_PATH, n=MOVER_SLOTS, exclude=BASE):
    """Best news movers of `day` from the news shadow file (already ranked best first), [] when missing."""
    try:
        shocks = json.loads(Path(path).read_text()).get("shocks", [])
    except (OSError, ValueError, AttributeError):
        return []
    todays = [s for s in shocks if isinstance(s, dict) and s.get("date") == str(day) and s.get("symbol") and s["symbol"] not in exclude]
    todays.sort(key=lambda s: -(s.get("score") or 0))
    return [s["symbol"] for s in todays][:n]


def watchlist(day, cap=MAX_TICKERS, news_path=NEWS_PATH, base=BASE):
    out = list(base)
    for s in movers(day, news_path):
        if s not in out:
            out.append(s)
    return out[:max(1, cap)]


# ---------- bars (Alpaca daily bars, yfinance fallback) ----------
def _alpaca_bars(symbols, start, end, urlopen=urllib.request.urlopen):
    try:   # shared data-key picker (first working pair among the configured accounts)
        import alpaca_keys
        key, sec = alpaca_keys.data_keys()
    except Exception:
        key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not (key and sec):
        raise RuntimeError("no Alpaca keys")
    last = None
    for feed in ("sip", "iex"):
        try:
            out, token = {}, None
            while True:
                q = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": str(start), "end": str(end),
                     "adjustment": "split", "feed": feed, "limit": 10000}
                if token:
                    q["page_token"] = token
                req = urllib.request.Request(f"{DATA_URL}?{urllib.parse.urlencode(q)}",
                                             headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
                with urlopen(req, timeout=30) as r:
                    j = json.load(r)
                for sym, rows in (j.get("bars") or {}).items():
                    for b in rows:
                        d = b["t"][:10]
                        out.setdefault(sym, {})[d] = (d, float(b["o"]), float(b["c"]))
                token = j.get("next_page_token")
                if not token:
                    return out
        except Exception as ex:
            last = ex
    raise last


def _yf_bars(symbols, start, end):
    import yfinance as yf
    out = {}
    for s in symbols:
        h = yf.Ticker(s).history(start=str(start), end=str(date.fromisoformat(str(end)) + timedelta(days=1)), auto_adjust=False)
        for ts, row in h.iterrows():
            d = ts.strftime("%Y-%m-%d")
            out.setdefault(s, {})[d] = (d, float(row["Open"]), float(row["Close"]))
    return out


def fetch_bars(symbols, start, end, alpaca=_alpaca_bars, yahoo=_yf_bars):
    """{symbol: {date: (date, open, close)}}; Alpaca first, Yahoo if that fails or returns nothing."""
    symbols = sorted(set(symbols))
    try:
        got = alpaca(symbols, start, end)
        if got and all(s in got for s in symbols):
            return got
    except Exception as ex:
        print(f"system_ta: Alpaca bars unavailable ({type(ex).__name__}); using Yahoo", file=sys.stderr)
    return yahoo(symbols, start, end)


# ---------- failures ----------
def classify(exc):
    """'auth' (sign-in needs redoing), 'limit' (plan usage limit / rate limit) or 'error' (this ticker only)."""
    chain, e = [], exc
    while e is not None and e not in chain:
        chain.append(e)
        e = e.__cause__ or e.__context__
    for e in chain:
        name, msg = type(e).__name__, str(e).lower()
        code = getattr(e, "status_code", None) or getattr(e, "code", None)
        if name == "ChatGPTUnreachable":
            return "error"
        if name == "ChatGPTAuthError" or "not signed in" in msg or "invalid_grant" in msg or code in (401, 403) \
                or name in ("AuthenticationError", "PermissionDeniedError") or "api key not valid" in msg or "api_key_invalid" in msg:
            return "auth"
        if name in ("RateLimitError", "ResourceExhausted") or code == 429 or "resource_exhausted" in msg or "usage limit" in msg \
                or "rate limit" in msg or "exceeded your current quota" in msg:
            return "limit"
    return "error"


def scrub(text, n=160):
    t = re.sub(r"\s+", " ", str(text))
    t = re.sub(r"(sk-|eyJ)[A-Za-z0-9_\-\.]{8,}|[A-Za-z0-9_\-]{40,}", "[redacted]", t)
    return t[:n]


def alert_text(kind, day):
    if kind == "auth":
        return (f"⚠️ System C (TradingAgents) {day}: Google API key rejected, no decisions tonight. "
                "Check the GOOGLE_API_KEY secret (aistudio.google.com/apikey).")
    return (f"⚠️ System C (TradingAgents) {day}: Gemini free-tier quota/rate limit hit, remaining tickers skipped tonight "
            "(daily quota resets at midnight Pacific).")


# ---------- state ----------
def load_state(path=PATH):
    try:
        s = json.loads(Path(path).read_text())
        if isinstance(s, dict) and "decisions" in s:
            return s
    except (OSError, ValueError):
        pass
    return {"label": L.LABEL, "started": None, "decisions": [], "runs": [], "health": {}}


def save_state(state, path=PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")


def apply_ledger(state, bars, broker=None, day=None, stamp=None):
    """Shadow replay always (state['shadow']); with a paper broker the top-level ledger is the account's real state and
    tonight's orders are placed first. Broker trouble never loses the shadow ledger."""
    import ta_broker
    led = L.replay(state["decisions"], bars)
    state["shadow"] = {k: led[k] for k in ("equity", "positions", "pending", "trades", "calendar")} | {"stats": L.stats(led)}
    if broker is None:
        state.update({k: led[k] for k in ("start_equity", "equity", "cash", "positions", "pending", "trades", "calendar",
                                          "balance_history")})
        state["stats"], state["mode"] = L.stats(led), "shadow"
        return led
    try:
        night = [d for d in state["decisions"] if d["date"] == str(day)]
        history = [d for d in state["decisions"] if d["date"] < str(day)]
        prices = {d["symbol"]: bars[d["symbol"]][str(day)][2] for d in night if str(day) in bars.get(d["symbol"], {})}
        equity = float(broker.get_account().equity)
        orders = ta_broker.plan_orders(night, history, ta_broker.held_qty(broker), prices, equity, ta_broker.shortable_fn(broker))
        rows = ta_broker.place(broker, orders, stamp or str(day))
        state["orders_log"] = (state.get("orders_log", []) + [{"date": str(day), **r} for r in rows])[-200:]
        snap = ta_broker.snapshot(broker)
        by = {}
        for d in state["decisions"]:
            if d.get("source") == "ta":
                by[d["symbol"]] = d
        for p in snap.get("positions", []):
            p["rating"], p["rationale"] = by.get(p["symbol"], {}).get("rating"), by.get(p["symbol"], {}).get("rationale", "")
        snap.pop("strategies_by_symbol", None)
        state.update(snap)
        state["mode"] = "paper"
        state["trades"] = ta_broker.trades_from_rows((snap.get("balance_history") or {}).get("rows", []), state["decisions"])
        state["pending"] = [r for r in rows if r.get("status") not in ("skipped",)]
    except Exception as ex:
        print(f"system_ta: paper account step failed ({type(ex).__name__}): {scrub(ex)}", file=sys.stderr)
        state["mode"] = "paper (account step failed tonight; shadow ledger shown)"
        state.update({k: led[k] for k in ("equity", "positions", "pending", "trades", "calendar")})
    return led


def rotation_exits(state, wl, day):
    """Open positions in tickers that left the watchlist get an exit decision (target 0) for `day`."""
    have = {d["id"] for d in state["decisions"]}
    new = []
    for p in state.get("positions", []):
        d = L.make_decision(day, p["symbol"], "Exit", "dropped off tonight's watchlist: position closed at the next open",
                            source="rotation")
        if p["symbol"] not in wl and d["id"] not in have:
            new.append(d)
    return new


# ---------- TradingAgents ----------
def ta_config(results_dir=None):
    from tradingagents.default_config import DEFAULT_CONFIG
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(llm_provider=os.environ.get("TA_PROVIDER") or PROVIDER, backend_url=None, llm_max_retries=6,
               memory_log_path=str(MEMORY_LOG), google_thinking_level=os.environ.get("TA_THINKING") or "low",
               deep_think_llm=os.environ.get("TA_DEEP_MODEL") or DEEP_MODEL,
               quick_think_llm=os.environ.get("TA_QUICK_MODEL") or QUICK_MODEL)
    if results_dir:
        cfg["results_dir"] = str(results_dir)
    return cfg


class Counter:
    """langchain callback handler counting model calls (built lazily so tests need no langchain)."""

    @staticmethod
    def make():
        from langchain_core.callbacks import BaseCallbackHandler

        class _C(BaseCallbackHandler):
            calls = 0

            def on_chat_model_start(self, *a, **k):
                self.calls += 1

            def on_llm_start(self, *a, **k):
                pass
        return _C()


_limiters: dict = {}


def rpm_for(model):
    env = os.environ.get("TA_RPM_" + re.sub(r"\W", "_", model).upper())
    return float(env) if env else FREE_RPM.get(model, 5) * RPM_MARGIN


def install_rate_limits():
    """Every Gemini client TradingAgents builds (also inside run_backtest) shares ONE limiter per model name, because the
    free tier's requests-per-minute quota is per project and model. Idempotent."""
    from langchain_core.rate_limiters import InMemoryRateLimiter
    from tradingagents.llm_clients import google_client as g
    if getattr(g.GoogleClient, "_limited", False):
        return
    orig = g.GoogleClient.get_llm

    def get_llm(self):
        llm = orig(self)
        if self.model not in _limiters:
            _limiters[self.model] = InMemoryRateLimiter(requests_per_second=rpm_for(self.model) / 60, check_every_n_seconds=0.1,
                                                        max_bucket_size=2)
        llm.rate_limiter = _limiters[self.model]
        return llm
    g.GoogleClient.get_llm, g.GoogleClient._limited = get_llm, True


def make_decider(config=None):
    """decide(ticker, day) -> {'rating','rationale','horizon','calls','seconds'}. Builds the graph once."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    install_rate_limits()
    counter = Counter.make()
    graph = TradingAgentsGraph(config=config or ta_config(), callbacks=[counter])

    def decide(ticker, day):
        before, t0 = counter.calls, time.monotonic()
        final_state, signal = graph.propagate(ticker, day)
        summary, horizon = L.parse_decision(final_state.get("final_trade_decision"))
        return {"rating": signal, "rationale": summary or one_liner(final_state.get("final_trade_decision")),
                "horizon": horizon, "calls": counter.calls - before, "seconds": round(time.monotonic() - t0, 1)}
    return decide


def one_liner(t):
    return L.one_line(re.sub(r"[*#]", "", str(t or "")), 300)


def session_day(now=None, asof=None):
    """The last finished session date (NY): today after 16:15 ET, else the previous weekday."""
    if asof:
        return date.fromisoformat(asof)
    now = now or datetime.now(NY)
    d = now.date() if now.time() >= dtime(16, 15) else now.date() - timedelta(days=1)
    while d.weekday() > 4:
        d -= timedelta(days=1)
    return d


# ---------- the nightly pass ----------
def run(state, day, tickers, decide, bars_fn, force=False, send=None, broker=None):
    """One night: decide each ticker (skipping ones already decided for `day` unless force), update the ledger.
    Returns (results for the message, alert kind or None)."""
    day, results, alert, t0 = str(day), [], None, time.monotonic()
    calls = sum(r["calls"] for r in state["runs"] if r["date"] == day)   # a re-run adds to the night's count
    have = {d["id"]: d for d in state["decisions"]}
    for i, tk in enumerate(tickers):
        did = L.decision_id(day, tk)
        if did in have and not force:
            d = have[did]
            results.append({"symbol": tk, "rating": d["rating"], "rationale": d["rationale"], "horizon": d.get("horizon")})
            continue
        if alert:
            results.append({"symbol": tk, "note": f"skipped after {alert} failure"})
            continue
        try:
            r = decide(tk, day)
            rating = str(r.get("rating") or "").strip().title()
            if L.target_for(rating) is None and rating != "Hold":
                results.append({"symbol": tk, "note": f"unreadable rating ({scrub(rating, 20)})"})
                continue
            calls += int(r.get("calls") or 0)
            d = L.make_decision(day, tk, rating, r.get("rationale", ""), r.get("horizon"))
            have[did] = d
            state["decisions"] = [x for x in state["decisions"] if x["id"] != did] + [d]
            results.append({"symbol": tk, "rating": rating, "rationale": d["rationale"], "horizon": d["horizon"]})
        except Exception as ex:
            kind = classify(ex)
            print(f"system_ta: {tk} failed ({kind}): {scrub(ex)}", file=sys.stderr)
            results.append({"symbol": tk, "note": f"{kind}: {scrub(ex, 80)}"})
            if kind in ("auth", "limit"):
                alert = kind
    new_exits = rotation_exits(state, tickers, day)
    state["decisions"] += new_exits
    bars = bars_fn(sorted({d["symbol"] for d in state["decisions"]} | {"SPY"}))
    apply_ledger(state, bars, broker, day, datetime.now(timezone.utc).strftime("%Y%m%d%H%M"))
    state["started"] = state.get("started") or (state["decisions"][0]["date"] if state["decisions"] else None)
    state["runs"] = [r for r in state["runs"] if r["date"] != day][-120:] + [{
        "date": day, "tickers": {r["symbol"]: r.get("rating") or "no decision" for r in results},
        "calls": calls, "seconds": round(time.monotonic() - t0)}]
    h = state.setdefault("health", {})
    if alert:
        h.update(status=alert, last_error=day)
        if h.get("last_alert_date") != datetime.now(timezone.utc).date().isoformat() and send and send(alert_text(alert, day)):
            h["last_alert_date"] = datetime.now(timezone.utc).date().isoformat()
    elif any(r.get("rating") for r in results):
        h.update(status="ok", last_ok=day)
    return results, alert


def bars_since(state, day):
    start = min([d["date"] for d in state["decisions"]] + [str(day)])
    return lambda syms: fetch_bars(syms, date.fromisoformat(start) - timedelta(days=5), day + timedelta(days=0))


def list_models():
    """Models this GOOGLE_API_KEY can call (names only)."""
    import urllib.request
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                 headers={"x-goog-api-key": os.environ.get("GOOGLE_API_KEY", "")})
    for m in json.load(urllib.request.urlopen(req, timeout=30))["models"]:
        if "generateContent" in m.get("supportedGenerationMethods", []) and m["name"].startswith("models/gemini"):
            print(m["name"][7:])


def main(argv=None, decide=None, send=None, bars_fn=None, broker=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof")
    ap.add_argument("--tickers")
    ap.add_argument("--max-tickers", type=int, default=int(os.environ.get("TA_MAX_TICKERS") or MAX_TICKERS))
    ap.add_argument("--force", action="store_true", help="re-run tickers already decided for the day")
    ap.add_argument("--dry-run", action="store_true", help="show the watchlist; no model calls, nothing written or sent")
    ap.add_argument("--list-models", action="store_true", help="print the models the signed-in plan offers")
    a = ap.parse_args(argv)
    if a.list_models:
        list_models()
        return 0
    day = session_day(asof=a.asof)
    state = load_state()
    wl = [t.strip().upper() for t in a.tickers.split(",")] if a.tickers else watchlist(day, a.max_tickers)
    print(f"system_ta: {day} watchlist {wl} (cap {a.max_tickers})")
    if a.dry_run:
        return 0
    if send is None:
        import telegram
        send = telegram.send
    bars_fn = bars_fn or bars_since(state, day)
    try:   # is `day` a session? SPY must have its bar
        if str(day) not in bars_fn(["SPY"]).get("SPY", {}):
            print(f"system_ta: no SPY bar for {day}; not a session or data not ready, nothing to do")
            return 0
    except Exception as ex:
        print(f"system_ta: price data unavailable ({type(ex).__name__}); nothing done", file=sys.stderr)
        return 0
    if broker is None:
        try:
            import ta_broker
            broker = ta_broker.client_from_env()
        except Exception as ex:   # e.g. not a paper account: refuse, keep the shadow ledger
            print(f"system_ta: paper account unavailable ({scrub(ex)}); shadow ledger only", file=sys.stderr)
        print("system_ta: " + ("placing orders on the System C PAPER account" if broker else "no ALPACA_TA keys: shadow ledger only"))
    if decide is None:
        try:
            decide = make_decider()
        except Exception as ex:   # not signed in, TradingAgents missing, ...
            kind = classify(ex)
            print(f"system_ta: cannot start TradingAgents ({kind}): {scrub(ex)}", file=sys.stderr)
            decide = lambda tk, d, _ex=ex: (_ for _ in ()).throw(_ex)
    try:
        results, alert = run(state, day, wl, decide, bars_fn, force=a.force, send=send, broker=broker)
    except Exception as ex:   # never crash the workflow
        print(f"system_ta: run failed ({type(ex).__name__}): {scrub(ex)}", file=sys.stderr)
        return 0
    state["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)
    eq = state.get("equity")
    pct = (eq / L.START_EQUITY - 1) * 100 if eq else None
    send(L.nightly_message(day, results, eq, pct))
    print(f"system_ta: done, {state['runs'][-1]['calls']} model calls in {state['runs'][-1]['seconds']}s, equity {eq}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
