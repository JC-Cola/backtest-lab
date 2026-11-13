"""System C trading-plan extraction: the Portfolio Manager's free-text rationale -> a validated, executable plan.

One Gemini call per new decision (quick Flash-Lite model, same GOOGLE_API_KEY, strict JSON schema, temperature 0) turns
the paragraph into {direction, initial_size_x, stop, take_profit, scale_in, exit_conditions, time_horizon,
non_price_notes}. Everything else here is pure and deterministic:

* verify_prices(): every price the model returned must literally appear in the paragraph (+-0.01), else that field is dropped
  and noted "unverified" (no invented numbers ever reach an order).
* validate(): stop on the right side of the reference price and within 25% of it; scale-in sizes clipped so the position
  never exceeds the rating's slot (and never System C's 20% per-ticker cap); anything invalid is skipped and listed in
  plan["not_executed"].

Sizes are fractions ("x") of the rating's own target (Buy 20%, Overweight 10%, Underweight -10%, Sell -20% of equity), so
Overweight 0.5x = 5% of equity. SMA-based levels become plain price triggers using the price written in the paragraph.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

import ta_ledger as L

MODEL = "gemini-3.5-flash-lite"           # same quick tier as system_ta.QUICK_MODEL
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_STOP_DIST = 0.25
DEFAULT_VALID_DAYS = 10
TOL = 0.011

_NUM = {"type": "NUMBER", "nullable": True}
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "direction": {"type": "STRING", "enum": ["long", "short", "flat"]},
        "initial_size_x": {"type": "NUMBER"},
        "max_total_x": {"type": "NUMBER", "nullable": True},
        "stop": {"type": "OBJECT", "properties": {
            "price": _NUM, "type": {"type": "STRING", "enum": ["price", "sma", "none"]},
            "sma_days": {"type": "INTEGER", "nullable": True}}, "required": ["type"]},
        "take_profit": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "price": {"type": "NUMBER"}, "size_x": {"type": "NUMBER"}}, "required": ["price", "size_x"]}},
        "scale_in": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "condition": {"type": "STRING", "enum": ["above", "below", "near"]}, "price": {"type": "NUMBER"},
            "size_x": {"type": "NUMBER"}, "valid_days": {"type": "INTEGER", "nullable": True}},
            "required": ["condition", "price", "size_x"]}},
        "exit_conditions": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "condition": {"type": "STRING", "enum": ["above", "below"]}, "price": {"type": "NUMBER"}},
            "required": ["condition", "price"]}},
        "time_horizon": {"type": "STRING", "nullable": True},
        "non_price_notes": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["direction", "initial_size_x", "stop", "take_profit", "scale_in", "exit_conditions", "non_price_notes"],
}

PROMPT = """You convert a portfolio manager's trading paragraph into a JSON trading plan.
RULES
- Use ONLY numbers that appear in the paragraph. Never invent, round, compute or infer a price.
- direction: long, short or flat as the paragraph describes the position for this rating ({rating}).
- initial_size_x: the fraction of a full allocation to open now ("0.5x tranche", "0.5x position" = 0.5). 1 if no size is stated.
- max_total_x: the largest total size the paragraph allows ("capping exposure at a 0.5x allocation" = 0.5, "full 1.0x" = 1.0), else null.
- stop: ONLY an explicit stop-loss, risk-off trigger or exit-below level for the position (a stop-loss / risk-off / exit-below level for the position. type "price" for a plain price, "sma" when the level is a
  moving average (still give its quoted price in `price`, and sma_days), "none" if the paragraph gives no stop (price null).
  Vague hedges ("prepares to reduce risk should X fail to hold") are NOT a stop: put them in exit_conditions.)
- take_profit: price targets to sell into, each with size_x (fraction of the position, 1 = all).
- scale_in: ADDITIONAL buys later. condition "above" (once price holds above the level), "below", or "near" (consolidates
  near the level). size_x is the ADDITIONAL fraction: "scale to a full 1.0x" after an initial 0.5x means size_x 0.5.
  valid_days only if the paragraph states how long the trigger lasts, else null.
- exit_conditions: price triggers that close or de-risk an existing position and are NOT the stop (a long exits when price falls
  "below" a level, a short when it rises "above"). A level that must be CONFIRMED before opening/adding is NOT an exit.
- non_price_notes: anything to monitor that is not a price (earnings, capex, catalysts), short strings.
- Empty arrays when there is nothing; do not make up entries.
Current rating: {rating}. Held side now: {held}.
PARAGRAPH:
{text}
"""


# ---------- model call ----------
def gemini_extract(text, rating, held=None, post=None, sleep=time.sleep, tries=4):
    """Raw plan dict from Gemini (JSON mode + schema), or raises. `post(url, body, headers)->dict` is injectable."""
    body = {"contents": [{"parts": [{"text": PROMPT.format(rating=rating, held=held or "none", text=text)}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA}}
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key and post is None:
        raise RuntimeError("no GOOGLE_API_KEY")
    post = post or _post
    last = None
    for i in range(tries):
        try:
            j = post(URL.format(model=os.environ.get("TA_PLAN_MODEL") or MODEL), body, {"x-goog-api-key": key, "Content-Type": "application/json"})
            return json.loads(j["candidates"][0]["content"]["parts"][0]["text"])
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 503):
                raise
        except (ValueError, KeyError, IndexError) as e:   # unparsable answer: one more try
            last = e
        sleep(15 * (i + 1))
    raise last


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


# ---------- deterministic checks ----------
def numbers_in(text):
    """Every number written in the paragraph ($128.64, 1,200, 0.5x ...)."""
    out = []
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", str(text)):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def in_text(price, nums):
    try:
        return any(abs(float(price) - n) <= TOL for n in nums)
    except (TypeError, ValueError):
        return False


def _num(x):
    try:
        v = float(x)
        return v if v == v and v > 0 else None
    except (TypeError, ValueError):
        return None


HEDGE = re.compile(r"reduce (?:the )?(?:risk|exposure)|fail(?:s)? to hold|prepare", re.I)
STOPWORD = re.compile(r"stop|risk-off|exit|liquidat|cut (?:the )?(?:loss|position)|invalidat", re.I)


def stop_wording(text, price):
    """'stop' when the sentence quoting `price` is an explicit stop / risk-off / exit order, 'hedge' when it only says one
    would reduce risk if a level fails, else None (the model may not turn a vague sentence into a hard stop)."""
    for sent in re.split(r"(?<=[.;])\s+", str(text)):
        if in_text(price, numbers_in(sent)):
            if STOPWORD.search(sent) and not (HEDGE.search(sent) and not re.search(r"stop|risk-off", sent, re.I)):
                return "stop"
            if HEDGE.search(sent):
                return "hedge"
    return None


def rating_side(rating, held=None):
    t = L.target_for(rating)
    if t is None:
        return held   # Hold: whatever is open (None when flat)
    return "long" if t > 0 else "short"


def verify_prices(raw, text):
    """(cleaned raw, notes). Any price not literally in `text` drops its field/entry."""
    nums, notes, raw = numbers_in(text), [], dict(raw or {})
    st = dict(raw.get("stop") or {})
    if st.get("price") is not None and not in_text(st["price"], nums):
        notes.append(f"stop ${st['price']} unverified (not in the paragraph): dropped")
        st = {"price": None, "type": "none", "sma_days": None}
    raw["stop"] = st
    for key in ("take_profit", "scale_in", "exit_conditions"):
        keep = []
        for e in raw.get(key) or []:
            if isinstance(e, dict) and in_text(e.get("price"), nums):
                keep.append(e)
            else:
                notes.append(f"{key.replace('_', ' ')} price {e.get('price') if isinstance(e, dict) else e} unverified: dropped")
        raw[key] = keep
    x = _num(raw.get("initial_size_x"))
    if x is not None and abs(x - 1.0) > 1e-9 and not any(abs(x - n) < 1e-9 for n in nums):
        notes.append(f"initial size {x:g}x unverified: using the default 1x")
        x = None
    raw["initial_size_x"] = x
    m = _num(raw.get("max_total_x"))
    if m is not None and not any(abs(m - n) < 1e-9 for n in nums):
        notes.append(f"size cap {m:g}x unverified: ignored")
        m = None
    raw["max_total_x"] = m
    return raw, notes


def validate(raw, text, rating, ref_price, held=None, cap=L.SLOT):
    """Validated executable plan (never raises). ref_price = the last close. Everything rejected is listed in not_executed."""
    plan = {"rating": rating, "side": rating_side(rating, held), "ref_price": ref_price, "initial_x": 1.0, "stop": None,
            "stop_type": None, "tp": None, "scale_in": [], "exits": [], "horizon": None, "notes": [], "not_executed": []}
    if not isinstance(raw, dict):
        plan["not_executed"].append("no readable plan: rating only")
        return plan
    raw, bad = verify_prices(raw, text)
    plan["not_executed"] += bad
    side, target = plan["side"], L.target_for(rating)
    base = abs(target) if target else None
    plan["base"] = base
    d = raw.get("direction")
    if side and d in ("long", "short", "flat") and d != side:
        plan["not_executed"].append(f"plan direction '{d}' conflicts with the {rating} rating: rating wins")
    x = raw.get("initial_size_x")
    plan["initial_x"] = min(1.0, x) if x else 1.0
    plan["horizon"] = raw.get("time_horizon") or None
    plan["notes"] = [str(n)[:160] for n in (raw.get("non_price_notes") or []) if str(n).strip()][:6]
    if not (side and ref_price):
        if raw.get("stop", {}).get("price") or raw.get("scale_in") or raw.get("exit_conditions"):
            plan["not_executed"].append("no open position or no price to validate against: levels not executed")
        return plan
    sgn = 1 if side == "long" else -1
    # stop
    st = raw.get("stop") or {}
    p = _num(st.get("price"))
    if p is not None and stop_wording(text, p) != "stop":
        if stop_wording(text, p) == "hedge" and not any(abs(_num(e.get("price")) or 0) == p for e in raw.get("exit_conditions") or []):
            raw["exit_conditions"] = list(raw.get("exit_conditions") or []) + [{"condition": "below" if side == "long" else "above", "price": p}]
            plan["not_executed"].append(f"${p:g} is a 'reduce risk if it fails' hedge, not a stop: watched as an exit trigger instead")
        else:
            plan["not_executed"].append(f"stop ${p:g} is not worded as a stop in the paragraph: skipped")
        p = None
    if st.get("type") == "none" or p is None:
        plan["not_executed"].append("no stop in the paragraph") if not any("stop" in n for n in plan["not_executed"]) else None
    elif (p - ref_price) * sgn >= 0:
        plan["not_executed"].append(f"stop ${p:g} is on the wrong side of ${ref_price:g} for a {side}: skipped")
    elif abs(p / ref_price - 1) > MAX_STOP_DIST:
        plan["not_executed"].append(f"stop ${p:g} is more than {MAX_STOP_DIST:.0%} from ${ref_price:g}: skipped")
    else:
        plan["stop"], plan["stop_type"] = p, "sma" if st.get("type") == "sma" else "price"
    # take profit (first valid level; one OCO leg per position)
    tps = []
    for t in raw.get("take_profit") or []:
        tp = _num(t.get("price"))
        if tp is None or (tp - ref_price) * sgn <= 0:
            plan["not_executed"].append(f"take-profit ${t.get('price')} is on the wrong side of ${ref_price:g}: skipped")
        else:
            tps.append(tp)
    if tps:
        plan["tp"] = min(tps) if side == "long" else max(tps)
        if len(tps) > 1:
            plan["not_executed"].append(f"{len(tps) - 1} further take-profit level(s): only the nearest is placed")
    # scale-ins: clipped to the room left in the slot
    cap_x = min(1.0, raw.get("max_total_x") or 1.0)
    plan["max_total_x"] = cap_x
    room = max(0.0, round(cap_x - plan["initial_x"], 6)) if base else 0.0
    for s in raw.get("scale_in") or []:
        px, sz, cond = _num(s.get("price")), _num(s.get("size_x")), s.get("condition")
        if base is None:
            plan["not_executed"].append(f"scale-in {cond} ${s.get('price')}: Hold has no slot to scale into")
        elif px is None or sz is None or cond not in ("above", "below", "near"):
            plan["not_executed"].append("scale-in with missing price/size: skipped")
        elif room <= 0:
            plan["not_executed"].append(f"scale-in {cond} ${px:g}: initial entry already uses the {cap_x:g}x allowed")
        else:
            if sz > room + 1e-9:
                plan["not_executed"].append(f"scale-in {cond} ${px:g} size {sz:g}x clipped to the {room:g}x left in the slot")
                sz = room
            plan["scale_in"].append({"condition": cond, "price": px, "size_x": round(sz, 4),
                                     "valid_days": int(s["valid_days"]) if s.get("valid_days") else DEFAULT_VALID_DAYS})
    # exit conditions: price triggers the monitor watches
    for e in raw.get("exit_conditions") or []:
        px, cond = _num(e.get("price")), e.get("condition")
        if px is None or cond not in ("above", "below"):
            plan["not_executed"].append("exit condition without a usable price: skipped")
        elif cond != ("below" if side == "long" else "above"):
            plan["not_executed"].append(f"exit {cond} ${px:g} is not an adverse trigger for a {side}: skipped")
        elif (cond == "below") != (px < ref_price):
            plan["not_executed"].append(f"exit {cond} ${px:g} is already triggered at ${ref_price:g}: skipped")
        elif plan["stop"] and abs(px - plan["stop"]) < 0.005:
            continue   # same level as the stop: the stop order covers it
        else:
            plan["exits"].append({"condition": cond, "price": px})
    return plan


def build(text, rating, ref_price, held=None, extract=gemini_extract):
    """extract -> verify -> validate in one go; extraction failure degrades to a rating-only plan, never raises."""
    try:
        raw = extract(text, rating, held)
    except Exception as ex:
        p = validate(None, text, rating, ref_price, held)
        p["not_executed"] = [f"plan extraction failed ({type(ex).__name__}): rating only"]
        return p
    return validate(raw, text, rating, ref_price, held)


# ---------- message line ----------
def plan_line(sym, plan):
    """'NBIS · Overweight 0.5x · stop $110 · +0.5x if holds > $118.94 · note: watch CapEx'"""
    if not plan:
        return None
    parts = [sym, f"{plan['rating']} {plan['initial_x']:g}x" if plan.get("base") else f"{plan['rating']}"]
    if plan.get("stop"):
        parts.append(f"stop ${plan['stop']:g}")
    if plan.get("tp"):
        parts.append(f"target ${plan['tp']:g}")
    for s in plan.get("scale_in", []):
        c = {"above": f"if holds > ${s['price']:g}", "below": f"if holds < ${s['price']:g}", "near": f"near ${s['price']:g}"}[s["condition"]]
        parts.append(f"+{s['size_x']:g}x {c}")
    for e in plan.get("exits", []):
        parts.append(f"exit {'<' if e['condition'] == 'below' else '>'} ${e['price']:g}")
    if plan.get("notes"):
        n = plan["notes"][0]
        parts.append("note: " + (n if len(n) <= 50 else n[:47].rsplit(" ", 1)[0] + "…"))
    return " · ".join(parts)
