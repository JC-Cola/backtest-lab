"""TradingAgents-style LLM review of a news shock (shadow only: it never trades).

One Claude CLI call per shock plays three roles in sequence, like TradingAgents' pipeline but compressed:
  1. news analyst   - classify the catalyst, is it company-specific and NEW, sentiment -2..+2
  2. bull vs bear   - one short case each, from the same facts (the bull/bear researcher debate)
  3. risk manager   - a skeptical verdict on the next 1-5 days: continue / reverse / unclear, confidence 0..1
No web access: Claude only sees the headlines and numbers we pass in. Any failure -> {"status": "no review"}; the
nightly job never blocks on it. Calls are capped (MAX_CALLS) and time-boxed (BUDGET_S), run 3 at a time, and the
whole batch stops early if the first calls all fail (login refused / CLI missing).
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

CATALYSTS = ["earnings", "guidance", "analyst_rating", "product", "legal_regulatory", "macro_sector", "m_and_a",
             "other", "unrelated_headline"]
DIRECTIONS = ["continue", "reverse", "unclear"]
MAX_CALLS = 15
BUDGET_S = 600          # wall-clock cap for the whole batch
CALL_TIMEOUT = 120
WORKERS = 3
BAIL_AFTER = 3          # this many failures with zero successes -> stop calling
NO_REVIEW = {"status": "no review"}

SCHEMA = {
    "type": "object",
    "properties": {
        "catalyst": {"type": "string", "enum": CATALYSTS},
        "company_specific": {"type": "boolean"},
        "is_new": {"type": "boolean"},
        "sentiment": {"type": "integer", "minimum": -2, "maximum": 2},
        "bull_case": {"type": "string"},
        "bear_case": {"type": "string"},
        "direction": {"type": "string", "enum": DIRECTIONS},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
    },
    "required": ["catalyst", "company_specific", "is_new", "sentiment", "bull_case", "bear_case", "direction", "confidence"],
}

# Headline keyword tags, borrowed from day-trader-skill's trader-news (earnings / analyst / legal / deal ...): a cheap
# hint handed to the analyst, never a decision on its own.
KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults\b|\bEPS\b|\brevenue\b|\bquarter"),
    ("guidance", r"\bguidance\b|\boutlook\b|\bforecast\b|\braises?\b|\bcuts?\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|\boutperform\b|\bunderperform\b|\binitiates?\b"),
    ("deal", r"\bacquir|\bacquisition\b|\bmerger\b|\bbuyout\b|\btakeover\b|\bto buy\b"),
    ("legal", r"\blawsuit\b|\bsues?\b|\bprobe\b|\binvestigation\b|\bsettle|\bSEC\b|\bDOJ\b|\bFDA\b|\bantitrust\b"),
    ("capital", r"\boffering\b|\bbuyback\b|\bdividend\b|\bconvertible\b"),
    ("risk", r"\brecall\b|\bbankrupt|\bdefault\b|\bdelist|\bhack|\bbreach\b|\bhalt"),
    ("macro", r"\bFed\b|\binflation\b|\btariff|\bCPI\b|\bjobs report\b|\bsector\b"),
]


def keyword_tags(headlines):
    text = " ".join(headlines)
    return [label for label, rx in KEYWORDS if re.search(rx, text, re.I)]


def _pct(x, d=1):
    return "n/a" if x is None else f"{x * 100:+.{d}f}%"


def build_prompt(sh):
    """Prompt text for one shock record (symbol, date, ret, mult, relvol, gap, spy_ret, beta, abn, headlines)."""
    heads = "\n".join(f"- {h}" for h in sh["headlines"])
    return (
        "You are a three-person review desk for a trading research log. Nothing here is traded; we want honest, "
        "skeptical calls that we will later score against what really happened. Use ONLY the facts below (no web, "
        "no memory of what happened afterwards).\n\n"
        f"Stock: {sh['symbol']}   Session: {sh['date']}\n"
        f"Close-to-close move: {_pct(sh['ret'])} = {sh.get('mult', 0):.1f}x its normal daily volatility\n"
        f"Opening gap vs prior close: {_pct(sh.get('gap'))}\n"
        f"Relative volume (today / 20-day average): {sh.get('relvol') if sh.get('relvol') is not None else 'n/a'}\n"
        f"S&P 500 (SPY) move that day: {_pct(sh.get('spy_ret'))}; stock beta {sh.get('beta') if sh.get('beta') is not None else 'n/a'}; "
        f"move beyond what beta x market explains: {_pct(sh.get('abn'))}\n"
        f"Keyword hints from the headlines: {', '.join(keyword_tags(sh['headlines'])) or 'none'}\n"
        f"Headlines in the window (16:00 ET the day before to 16:00 ET that session):\n{heads}\n\n"
        "Step 1, NEWS ANALYST: pick the catalyst type (use unrelated_headline when the headlines do not really explain "
        "this stock's move, macro_sector when the move is mostly the market or sector); say whether the news is "
        "genuinely about this company and genuinely new information rather than a recap; sentiment -2 (very negative) "
        "to +2 (very positive).\n"
        "Step 2, BULL vs BEAR: one or two sentences each, the strongest honest case for the move continuing and for it "
        "reversing, given the numbers.\n"
        "Step 3, RISK MANAGER (skeptical, default to doubt): over the next 1 to 5 trading days, is the most likely path "
        "continue (keeps going the same way), reverse (gives back a good part of the move) or unclear? Choose unclear "
        "unless the evidence leans clearly one way. A move that is mostly market-wide, a headline that does not explain "
        "the move, or already-priced old news are reasons for unclear. confidence 0 to 1 is your probability that your "
        "direction is right; 0.5 means a coin flip, do not inflate it. Add a one-sentence rationale."
    )


def parse_review(out):
    """Validate the CLI's structured output -> compact review dict, or None when unusable."""
    if not isinstance(out, dict):
        return None
    try:
        cat, d = out["catalyst"], out["direction"]
        if cat not in CATALYSTS or d not in DIRECTIONS:
            return None
        conf = float(out["confidence"])
        sent = int(round(float(out["sentiment"])))
        cut = lambda k, n: " ".join(str(out.get(k, "")).split())[:n]
        return {"status": "ok", "catalyst": cat, "company_specific": bool(out["company_specific"]), "is_new": bool(out["is_new"]),
                "sentiment": max(-2, min(2, sent)), "bull": cut("bull_case", 300), "bear": cut("bear_case", 300),
                "direction": d, "confidence": round(max(0.0, min(1.0, conf)), 2), "rationale": cut("rationale", 240)}
    except (KeyError, TypeError, ValueError):
        return None


def review_one(sh, ask, timeout=CALL_TIMEOUT):
    """One review; never raises. `ask(prompt, schema, timeout)` is claude_cli.ask-compatible."""
    try:
        return parse_review(ask(build_prompt(sh), SCHEMA, timeout=timeout)) or dict(NO_REVIEW)
    except Exception:
        return dict(NO_REVIEW)


def review_all(shocks, ask=None, max_calls=MAX_CALLS, budget_s=BUDGET_S, timeout=CALL_TIMEOUT, workers=WORKERS,
               clock=time.monotonic):
    """{shock id: review dict} for the first `max_calls` shocks (callers pass them best-first); every other shock, and
    every one after the time budget ran out or the CLI looks dead, gets {"status": "no review"}."""
    if ask is None:
        from agent import claude_cli
        ask = claude_cli.ask
    t0 = clock()
    state = {"ok": 0, "bad": 0}

    def work(sh):
        if clock() - t0 > budget_s or (state["ok"] == 0 and state["bad"] >= BAIL_AFTER):
            return dict(NO_REVIEW)
        r = review_one(sh, ask, min(timeout, max(5, int(budget_s - (clock() - t0)))))
        state["ok" if r.get("status") == "ok" else "bad"] += 1
        return r

    todo = list(shocks)[:max_calls]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(work, todo))
    out = {sh["id"]: r for sh, r in zip(todo, results)}
    for sh in shocks:
        out.setdefault(sh["id"], dict(NO_REVIEW))
    return out
