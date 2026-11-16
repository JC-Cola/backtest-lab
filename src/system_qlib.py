"""qlib: the live paper system on the dedicated Alpaca paper account (ALPACA_DTS_* keys; it replaced day-trader-skill there).

    python system_qlib.py run [--dry-run] [--force] [--no-telegram] [--no-update]
    python system_qlib.py train                 # (re)fit and cache the model only

What it does, once per night after the close (22:00 UTC weekdays; a no-op on holidays and when already done for the day):
  1. updates data/qlib (Yahoo daily bars, adjusted) through today's close and refuses to trade on stale data
  2. loads the newest model in data/qlib/models/, refitting it first when it is from an earlier calendar month
     (Alpha158 + LightGBM on the trailing 8 years, validation = the last 6 months; see qlib_model.py, QLIB_PLAN_15.md)
  3. scores every symbol for the day, then picks the portfolio with qlib's TopkDropoutStrategy rule (topk 50, n_drop 5):
     sell the 5 worst-scored holdings, buy the best-scored non-holdings to refill to 50
  4. equal weight (1/50 of the account's equity, never above 4% per name), whole shares, long-only, no leverage;
     diff against the account's positions; skip diffs under $100; DAY market orders queued for the next open, sells before buys
  5. one Telegram line, state in agent_memory/system_qlib.json (same snapshot shape as the other systems + "picks")
Orders go only to the Alpaca PAPER endpoint on an account number starting with "PA" (guard below). Re-running the same night is
a no-op (state + deterministic client order ids).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "agent_memory" / "system_qlib.json"
NY = ZoneInfo("America/New_York")
LABEL = "qlib"
PAPER_HOST = "paper-api.alpaca.markets"
CFG = {"topk": 50, "n_drop": 5, "cap_pct": 0.04, "min_diff_usd": 100.0, "train_years": 8, "valid_months": 6,
       "min_fresh": 0.90,            # share of the universe that must have today's bar
       "ready_after": (16, 10),      # New York time after which the daily bar is final
       "keep_rebalances": 30, "start_equity": 25000.0}


class Refused(RuntimeError):
    pass


# ---------------------------------------------------------------- pure portfolio logic (tested with fakes)

def topk_dropout(scores: dict, held, topk=50, n_drop=5) -> list:
    """qlib's TopkDropoutStrategy (method_sell="bottom", method_buy="top", hold_thresh=1) as a target set.

    Holdings missing from `scores` rank last. Sell the n_drop worst of (holdings + the best non-holdings that could replace them),
    then buy the best non-holdings until topk names are held."""
    held = [s for s in dict.fromkeys(held)]
    key = lambda s: (-(scores.get(s, float("-inf"))), s)
    last = sorted(held, key=key)
    pool = sorted((s for s in scores if s not in set(held)), key=key)
    today = pool[:max(0, n_drop + topk - len(last))]
    comb = sorted(last + today, key=key)
    sell = [s for s in reversed(comb) if s in set(last)][:n_drop]
    buy = today[:len(sell) + topk - len(last)]
    keep = [s for s in last if s not in set(sell)]
    return keep + buy


def affordable(prices: dict, equity: float, cap_pct: float) -> set:
    """Symbols we can hold with at least one whole share under the per-name cap."""
    return {s for s, p in prices.items() if p and p > 0 and p <= cap_pct * equity}


def target_shares(targets, prices: dict, equity: float, topk=50, cap_pct=0.04) -> dict:
    """Whole shares per name: weight min(1/topk, cap) of equity, floored; a name that floors to 0 but fits the cap gets 1 share.
    The total never exceeds equity (no leverage): extra 1-share bumps are dropped, dearest first, if it would."""
    w = min(1.0 / topk, cap_pct)
    out, bumps = {}, []
    for s in targets:
        px = prices.get(s)
        if not px or px <= 0:
            continue
        q = int(math.floor(w * equity / px + 1e-9))
        if q == 0 and px <= cap_pct * equity:
            q = 1
            bumps.append(s)
        if q > 0:
            out[s] = q
    over = sum(q * prices[s] for s, q in out.items()) - equity
    for s in sorted(bumps, key=lambda x: -prices[x]):
        if over <= 0:
            break
        over -= out.pop(s) * prices[s]
    return out


def diff_orders(target: dict, held: dict, prices: dict, min_usd=100.0) -> list:
    """Orders that move `held` (symbol -> qty) to `target`. Sells first (largest first), then buys. A position that leaves
    the portfolio is sold in full whatever its size; every other change under `min_usd` is skipped."""
    sells, buys = [], []
    for s in sorted(set(target) | set(held)):
        want, have = int(target.get(s, 0)), int(held.get(s, 0))
        d = want - have
        px = prices.get(s)
        if d == 0 or not px:
            continue
        usd = abs(d) * px
        if d < 0 and want == 0:
            sells.append({"symbol": s, "side": "sell", "qty": -d, "usd": round(usd, 2), "why": "exit"})
        elif usd >= min_usd:
            (sells if d < 0 else buys).append({"symbol": s, "side": "sell" if d < 0 else "buy", "qty": abs(d),
                                               "usd": round(usd, 2), "why": "new" if have == 0 else "resize"})
    sells.sort(key=lambda o: -o["usd"])
    buys.sort(key=lambda o: -o["usd"])
    return sells + buys


def telegram_line(orders, holdings: int, limit=14) -> str:
    sym = {"buy": "+", "sell": "−"}
    toks = [sym[o["side"]] + o["symbol"] for o in orders]
    if not toks:
        return "%s · no changes · %d holdings" % (LABEL, holdings)
    more = len(toks) - limit
    body = " ".join(toks[:limit]) + (" … (+%d more)" % more if more > 0 else "")
    return "%s · rebalance: %s · %d holdings" % (LABEL, body, holdings)


def client_order_id(data_date: str, side: str, sym: str) -> str:
    return "qlib-%s-%s-%s" % (data_date, side, sym)


# ---------------------------------------------------------------- the paper account

def assert_paper(client):
    """Refuse anything that is not the Alpaca PAPER endpoint on a paper account (number starts with PA)."""
    raw = getattr(client, "_base_url", "")
    base = str(getattr(raw, "value", raw))          # alpaca-py stores a BaseURL enum; its .value is the URL
    if PAPER_HOST not in base:
        raise Refused("refusing to run: the trading client is not the Alpaca PAPER endpoint (%r)" % base)
    acct = client.get_account()
    num = str(getattr(acct, "account_number", "") or "")
    if not num.startswith("PA"):
        raise Refused("refusing to run: the account number does not look like a paper account")
    return acct


def make_client():
    key, secret = os.environ.get("ALPACA_DTS_API_KEY"), os.environ.get("ALPACA_DTS_SECRET_KEY")
    if not (key and secret):
        raise Refused("ALPACA_DTS_API_KEY / ALPACA_DTS_SECRET_KEY not set")
    from alpaca.trading.client import TradingClient
    return TradingClient(key, secret, paper=True)    # paper only: never paper=False


def held_qty(client) -> dict:
    return {p.symbol: int(float(p.qty)) for p in client.get_all_positions() if float(p.qty) > 0}


def cancel_stale(client) -> int:
    """Cancel our own unfilled orders from earlier nights (DAY orders expire anyway; this also clears the accepted queue)."""
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest
    n = 0
    for o in client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)):
        if str(getattr(o, "client_order_id", "") or "").startswith("qlib-"):
            try:
                client.cancel_order_by_id(o.id)
                n += 1
            except Exception:  # noqa: BLE001 - filled or cancelled meanwhile
                pass
    return n


def submit(client, data_date: str, orders: list) -> list:
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
    sent = []
    for o in orders:
        req = MarketOrderRequest(symbol=o["symbol"], qty=int(o["qty"]), side=OrderSide.BUY if o["side"] == "buy" else OrderSide.SELL,
                                 time_in_force=TimeInForce.DAY, client_order_id=client_order_id(data_date, o["side"], o["symbol"]))
        try:
            r = client.submit_order(req)
            sent.append({**o, "id": str(r.id), "status": str(getattr(r.status, "value", r.status))})
        except Exception as e:  # noqa: BLE001 - one rejected order must not stop the rest
            sent.append({**o, "id": None, "status": "error", "error": "%s: %s" % (type(e).__name__, str(e)[:140])})
    return sent


def snapshot_into(state: dict, client, now_utc: dt.datetime) -> None:
    """Equity, cash, last_equity, positions, orders, calendar, history, balance_history straight from the account."""
    import tempfile
    import alpaca_paper
    with tempfile.TemporaryDirectory() as d:
        snap = alpaca_paper.snapshot(client, decisions={}, path=Path(d) / "snap.json", behind={})
    for k in ("equity", "cash", "last_equity", "positions", "orders", "calendar", "history", "balance_history", "start_equity"):
        if k in snap:
            state[k] = snap[k]
    state["generated"] = now_utc.replace(microsecond=0).isoformat() + "+00:00"
    state["source"] = "alpaca-paper"


# ---------------------------------------------------------------- state

def new_state() -> dict:
    return {"system": LABEL, "label": LABEL, "mode": "paper", "start_equity": CFG["start_equity"], "equity": CFG["start_equity"],
            "cash": CFG["start_equity"], "last_equity": CFG["start_equity"], "positions": [], "orders": [], "picks": [],
            "rebalances": [], "model": {}, "data": {}}


def load(path=STATE_PATH) -> dict:
    p = Path(path)
    if p.exists():
        try:
            base = new_state()
            base.update(json.loads(p.read_text()))
            return base
        except (ValueError, OSError):
            pass
    return new_state()


def save(state: dict, path=STATE_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=1) + "\n")
    os.replace(tmp, p)


# ---------------------------------------------------------------- data + model (real pipeline; tests inject a scorer)

def real_scorer(day: dt.date, update=True) -> dict:
    """Update data, load/refit the model, score `day`. Returns {"scores": {sym: score}, "prices": {sym: close}, "meta": {...}}."""
    import pandas as pd
    import qlib_data as D
    import qlib_model as M
    if update:
        failed = D.update_csv()
        info = D.write_bin()
        info["download_failed"] = failed
    else:
        info = D.coverage()
    D.init_qlib()
    cov = D.coverage()
    spy = pd.read_csv(D.CSV_DIR / "SPY.csv", index_col=0, parse_dates=True)
    last_spy = spy.index.max().date()
    if last_spy != day:
        raise Refused("data is stale: SPY's last bar is %s, expected %s" % (last_spy, day))
    if cov["symbols_on_last_day"] < CFG["min_fresh"] * cov["symbols"]:
        raise Refused("only %d of %d symbols have a bar for %s" % (cov["symbols_on_last_day"], cov["symbols"], day))
    bundle, retrained = latest_or_refit(day)
    frame = M.raw_frame(pd.Timestamp(day) - pd.Timedelta(days=170), day, with_label=False)
    d0 = M.days_of(frame)
    today = frame[d0 == pd.Timestamp(day)]
    sc = bundle.predict(today)
    scores = {i: float(v) for (_, i), v in sc.items()}
    prices = {}
    for sym in scores:
        f = D.CSV_DIR / ("%s.csv" % sym)
        if f.exists():
            row = pd.read_csv(f, index_col=0, parse_dates=True)
            if len(row) and row.index.max().date() == day:
                prices[sym] = float(row["close"].iloc[-1])
    return {"scores": {s: v for s, v in scores.items() if s in prices}, "prices": prices,
            "meta": {"model": bundle.meta, "retrained": retrained, "data": cov}}


def model_files():
    import qlib_data as D
    return sorted(D.MODEL_DIR.glob("model_*.pkl"))


def latest_or_refit(day: dt.date):
    """(Bundle, retrained). A model is refitted when none exists or the newest one was fitted in an earlier month."""
    import pandas as pd
    import qlib_model as M
    files = model_files()
    if files:
        b = M.Bundle.load(files[-1])
        if b.meta.get("fitted_on", "")[:7] == day.isoformat()[:7]:
            return b, False
    return train_model(day), True


def train_model(day: dt.date):
    import pandas as pd
    import qlib_data as D
    import qlib_model as M
    D.init_qlib()
    start = pd.Timestamp(day) - pd.DateOffset(years=CFG["train_years"])
    frame = M.raw_frame(start, day)
    vstart = pd.Timestamp(day) - pd.DateOffset(months=CFG["valid_months"])
    tend = vstart - pd.Timedelta(days=3)
    vend = pd.Timestamp(day) - pd.Timedelta(days=4)       # labels look 2 sessions ahead: keep them inside the data
    b = M.fit(frame, (start, tend), (vstart, vend), meta={"fitted_on": day.isoformat()})
    b.save(D.MODEL_DIR / ("model_%s.pkl" % day.isoformat()))
    for old in model_files()[:-3]:
        old.unlink()
    return b


# ---------------------------------------------------------------- one night

def now_ny(now_utc: dt.datetime) -> dt.datetime:
    return now_utc.replace(tzinfo=dt.timezone.utc).astimezone(NY).replace(tzinfo=None)


def is_session(client, day: dt.date) -> bool:
    try:
        from alpaca.trading.requests import GetCalendarRequest
        return any(str(getattr(c, "date", ""))[:10] == day.isoformat() for c in client.get_calendar(GetCalendarRequest(start=day, end=day)))
    except Exception:  # noqa: BLE001 - calendar down: fall back to weekdays
        return day.weekday() < 5


def run_once(state: dict, client, now_utc: dt.datetime, scorer=None, notify=None, path=STATE_PATH, dry_run=False, force=False,
             update=True) -> dict:
    acct = assert_paper(client)
    et = now_ny(now_utc)
    day = et.date()
    if not is_session(client, day):
        return {"status": "closed"}
    if (et.hour, et.minute) < CFG["ready_after"]:
        return {"status": "too early: today's bar is not final before %02d:%02d New York" % CFG["ready_after"]}
    done = {r["data_date"] for r in state.get("rebalances", [])}
    if day.isoformat() in done and not force:
        if not dry_run:
            snapshot_into(state, client, now_utc)
            save(state, path)
        return {"status": "already done", "data_date": day.isoformat()}
    scorer = scorer or (lambda d: real_scorer(d, update=update))
    res = scorer(day)
    scores, prices = res["scores"], res["prices"]
    equity = float(acct.equity)
    held = held_qty(client)
    for s in held:                        # a holding with no fresh price/score stays priced at its account mark
        prices.setdefault(s, None)
    pos_px = {p.symbol: float(p.current_price) for p in client.get_all_positions()}
    for s, px in pos_px.items():
        if not prices.get(s):
            prices[s] = px
    ok = affordable(prices, equity, CFG["cap_pct"])
    cand = {s: v for s, v in scores.items() if s in ok}
    targets = topk_dropout(cand, [s for s in held if s in cand] if held else [], CFG["topk"], CFG["n_drop"])
    # names held that cannot be scored or afforded any more are sold: they are not in `targets`
    tgt = target_shares(targets, prices, equity, CFG["topk"], CFG["cap_pct"])
    orders = diff_orders(tgt, held, prices, CFG["min_diff_usd"])
    ranked = sorted(cand, key=lambda s: (-cand[s], s))
    rank = {s: i + 1 for i, s in enumerate(ranked)}
    picks = [{"symbol": s, "score": round(cand[s], 5), "rank": rank[s]} for s in sorted(tgt, key=lambda x: rank.get(x, 10**6))]
    holdings = len(tgt)
    msg = telegram_line(orders, holdings)
    out = {"status": "dry-run" if dry_run else "ok", "data_date": day.isoformat(), "orders": orders, "holdings": holdings, "message": msg}
    if dry_run:
        return out
    cancelled = cancel_stale(client)
    sent = submit(client, day.isoformat(), orders)
    state["picks"] = picks
    state["model"] = res.get("meta", {}).get("model", state.get("model", {}))
    state["data"] = res.get("meta", {}).get("data", state.get("data", {}))
    state["rebalances"] = (state.get("rebalances", []) + [{"data_date": day.isoformat(), "submitted_utc": now_utc.replace(microsecond=0).isoformat(),
                                                           "equity": round(equity, 2), "holdings": holdings, "cancelled": cancelled,
                                                           "orders": sent}])[-CFG["keep_rebalances"]:]
    snapshot_into(state, client, now_utc)
    save(state, path)
    if notify:
        notify(msg + ("" if all(o["status"] != "error" for o in sent) else " · %d order errors" % sum(o["status"] == "error" for o in sent)))
    out["sent"] = sent
    return out


def telegram_notify(text: str) -> bool:
    import telegram
    return telegram.send(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="qlib paper system")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true", help="compute and print the orders; submit nothing, save nothing")
    r.add_argument("--force", action="store_true")
    r.add_argument("--no-telegram", action="store_true")
    r.add_argument("--no-update", action="store_true", help="use the data already in data/qlib")
    r.add_argument("--now", help="UTC time, e.g. 2026-11-13T22:00 (testing)")
    sub.add_parser("train")
    a = ap.parse_args(argv)
    if a.cmd == "train":
        import qlib_data as D
        D.update_csv()
        D.write_bin()
        b = train_model(dt.date.today())
        print(json.dumps(b.meta))
        return 0
    now = dt.datetime.fromisoformat(a.now) if a.now else dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    state = load()
    out = run_once(state, make_client(), now, notify=None if (a.no_telegram or a.dry_run) else telegram_notify, dry_run=a.dry_run,
                   force=a.force, update=not a.no_update)
    print(json.dumps({k: v for k, v in out.items() if k != "sent"}, default=str))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refused as e:
        print("REFUSED: %s" % e)
        sys.exit(2)
