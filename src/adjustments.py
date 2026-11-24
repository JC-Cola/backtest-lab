"""Manual P&L corrections for Alpaca paper bookkeeping errors (config/adjustments.json).

Each entry {account, date, symbol, usd, why}: `date` is the NY trading date the correction belongs to. It adds `usd`
to that day's P&L and to every balance/equity value on or after that date (cumulative). Account keys are Alpaca
account keys ('dts' = the qlib account).
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "config" / "adjustments.json"


def load(path=None):
    try:
        data = json.loads(Path(path or PATH).read_text())
    except (OSError, ValueError):
        return []
    return [a for a in data if isinstance(a, dict)] if isinstance(data, list) else []


def for_account(acct, path=None):
    return [a for a in load(path) if a.get("account") == acct]


def cum_on(acct, date_iso, path=None):
    """Sum of corrections dated on or before date_iso (YYYY-MM-DD)."""
    return round(sum(a.get("usd", 0) for a in for_account(acct, path) if str(a.get("date")) <= str(date_iso)), 2)


def on_day(acct, date_iso, path=None):
    """Sum of corrections dated exactly date_iso."""
    return round(sum(a.get("usd", 0) for a in for_account(acct, path) if str(a.get("date")) == str(date_iso)), 2)
