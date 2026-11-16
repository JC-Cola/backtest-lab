"""Descriptive study: what copying Pelosi's House disclosures would have done, entered at the open AFTER the filing.

    python congress_study.py            # -> results/congress_copy.json (needs network; prices: Alpaca keys if set, else yfinance)

Same engine and rules as system_congress.py (replay, 20% cap, 0.05% per side, $25,000), run over every PTR the Clerk's index
lists from 2020. Three runs: (1) copy at the open after FILING [what a follower can do], (2) the same trades at the open of the
TRADE date [what the famous return tables imply; nobody could do this], (3) a window starting 2023-03-01 to compare with the NANC ETF.
DESCRIPTIVE ONLY: one member, picked because she is famous (selection), the index only lists people still filing and tickers that
no longer trade have no prices (survivorship), most disclosed trades are options we do not copy, and amounts are ranges.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
import sys
from pathlib import Path

import congress_data as cd
import system_congress as sc

OUT = Path(__file__).resolve().parent.parent / "results" / "congress_copy.json"
FIRST_YEAR = 2020


def load_records(member="Pelosi", years=None):
    """Every disclosed transaction of `member` from the House index, plus the filings we could not (fully) read."""
    years = years or list(range(FIRST_YEAR, dt.date.today().year + 1))
    recs, bad, docs = [], [], 0
    for row in cd.index_filings([member], years):
        docs += 1
        rows, why = cd.read_filing(row)
        recs += cd.to_records(member, row, rows)
        if why:
            bad.append({"doc_id": row["doc_id"], "filed": row["filed"], "link": row["url"], "reason": why})
    return recs, bad, docs


def _dd(curve):
    peak, worst = 0.0, 0.0
    for _, e in curve:
        peak = max(peak, e)
        worst = min(worst, e / peak - 1)
    return round(100 * worst, 2)


def _by_year(curve, start_eq):
    out, prev = {}, start_eq
    for y in sorted({d[:4] for d, _ in curve}):
        last = [e for d, e in curve if d[:4] == y][-1]
        out[y] = round(100 * (last / prev - 1), 2)
        prev = last
    return out


def bench(bars, sym, first_day, last_day):
    b = bars.get(sym, {})
    days = sorted(d for d in b if first_day <= d <= last_day)
    if not days:
        return None
    curve = [(d, b[d]["c"]) for d in days]
    ret = b[days[-1]]["c"] / b[days[0]]["o"] - 1
    years = max((dt.date.fromisoformat(days[-1]) - dt.date.fromisoformat(days[0])).days / 365.25, 1e-9)
    return {"symbol": sym, "from": days[0], "to": days[-1], "total_return_pct": round(100 * ret, 2),
            "cagr_pct": round(100 * ((1 + ret) ** (1 / years) - 1), 2), "max_drawdown_pct": _dd(curve),
            "by_year_pct": _by_year([(d, c) for d, c in curve], b[days[0]]["o"])}


def run_sim(records, bars, cfg, label, at_trade_date=False):
    recs = [dict(r) for r in records]
    for r in recs:
        if r["kind"] in ("buy", "sell_full", "sell_partial"):
            r["status"] = "pending"
        for k in ("exec_date", "fill", "qty", "weight", "backfilled"):
            r.pop(k, None)
        if at_trade_date and r.get("traded"):          # enter at the OPEN of the trade date itself (impossible in real life)
            r["filed"] = (dt.date.fromisoformat(r["traded"]) - dt.timedelta(days=1)).isoformat()
            r["first_seen"] = None
    rp = sc.replay(recs, bars, cfg)
    if not rp["curve"]:
        return {"label": label, "note": "no copyable trade"}, recs, rp
    first = rp["curve"][0][0]
    final = rp["curve"][-1][1]
    ret = final / cfg["start_equity"] - 1
    years = max((dt.date.fromisoformat(rp["curve"][-1][0]) - dt.date.fromisoformat(first)).days / 365.25, 1e-9)
    expo = [i / e for i, (_, e) in zip(rp["invested"], rp["curve"])]
    st = {k: len([r for r in recs if r["status"] == k]) for k in ("copied", "ignored", "skipped", "no_price", "pending")}
    return {"label": label, "from": first, "to": rp["curve"][-1][0], "final_equity": final,
            "total_return_pct": round(100 * ret, 2), "cagr_pct": round(100 * ((1 + ret) ** (1 / years) - 1), 2),
            "max_drawdown_pct": _dd(rp["curve"]), "avg_exposure_pct": round(100 * statistics.mean(expo), 1),
            "by_year_pct": _by_year(rp["curve"], cfg["start_equity"]), "rows_by_status": st,
            "round_trips_closed": len(rp["trades"]), "open_positions": len(rp["positions"])}, recs, rp


def lag_stats(recs):
    lags = [r["lag_days"] for r in recs if r.get("lag_days") is not None]
    if not lags:
        return {}
    return {"n": len(lags), "mean_days": round(statistics.mean(lags), 1), "median_days": statistics.median(lags),
            "max_days": max(lags), "over_45_days": len([x for x in lags if x > 45])}


def main():
    recs, bad, docs = load_records()
    kinds: dict = {}
    for r in recs:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    copyable = [r for r in recs if r["kind"] in ("buy", "sell_full", "sell_partial")]
    syms = sorted({r["ticker"] for r in copyable} | {"SPY", "NANC"})
    start = (dt.date.fromisoformat(min(r["filed"] for r in recs)) - dt.timedelta(days=10)).isoformat()
    today = dt.date.today().isoformat()
    bars = cd.cached_bars(syms, start, today)
    cfg = dict(sc.CFG)

    a, recs_a, rp_a = run_sim(recs, bars, cfg, "copy at the open after the FILING date (what a follower can do)")
    b, _, _ = run_sim(recs, bars, cfg, "same trades at the open of the TRADE date (not achievable: the filing comes later)", True)
    spy = bench(bars, "SPY", a.get("from", "2020-01-01"), today)
    # NANC window: only filings from the ETF's first full month, so both start together
    nanc_from = "2023-03-01"
    w_recs = [r for r in recs if r["filed"] >= nanc_from]
    c, _, rp_c = run_sim(w_recs, bars, cfg, "copy at the open after FILING, filings from %s" % nanc_from)
    nanc = bench(bars, "NANC", c.get("from", nanc_from), today) if "NANC" in bars else None
    spy_c = bench(bars, "SPY", c.get("from", nanc_from), today)

    equity_trades = [r for r in copyable]
    missing = sorted({r["ticker"] for r in copyable if not bars.get(r["ticker"])})
    out = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "member": "Nancy Pelosi (House; trades are mostly by her spouse, owner SP)", "source": "House Clerk PTRs via third_party/day_trader_skill/trader-pelosi",
        "chamber": "House only (the Senate site needs a person to accept an agreement; not automated)",
        "rules": {"start_equity": cfg["start_equity"], "cap_per_name": cfg["cap"], "ref_midpoint": cfg["ref_mid"], "cost_per_side": cfg["cost"],
                  "entry": "first market open strictly after the filing date", "shorting": False, "options": "recorded, never traded"},
        "filings": {"ptr_filings_since_2020": docs, "unread_or_partial": bad, "transactions_parsed": len(recs), "by_kind": kinds},
        "equity_trades_since_2020": {"total": len(copyable), "purchases": kinds.get("buy", 0), "full_sales": kinds.get("sell_full", 0),
                                      "partial_sales": kinds.get("sell_partial", 0), "tickers_without_prices": missing},
        "lag": {"all_transactions": lag_stats(recs), "equity_trades": lag_stats(equity_trades)},
        "copy_at_filing_open": a, "copy_at_trade_open_not_achievable": b, "spy_same_period": spy,
        "nanc_window": {"copy": c, "NANC": nanc, "SPY": spy_c},
        "caveats": [
            "Descriptive only; not a strategy claim and not advice.",
            "Selection: one famous member chosen because she is famous; survivorship: tickers that no longer trade have no prices and are skipped (listed above).",
            "The famous return tables measure from the TRADE date; a copier only learns of a trade at the filing (lag above), so the second run is a ceiling nobody could trade.",
            "Most of her disclosed activity is options and private assets, which are recorded but not copied, so the copied book is partial and often mostly cash (see avg_exposure_pct).",
            "Amounts are ranges; sizes here are a rule (midpoint-based, 20% cap), not what the member held. Sales of names the shadow never held are ignored.",
            "Prices are split/dividend-adjusted daily bars (yfinance when run locally, Alpaca in the nightly job).",
        ]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("filings", "equity_trades_since_2020", "lag", "copy_at_filing_open",
                                           "copy_at_trade_open_not_achievable", "spy_same_period", "nanc_window")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
