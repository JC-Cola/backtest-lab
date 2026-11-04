"""Live research feed: posts what is being tested right now to a Supabase function (rpc live_put), which the live
page reads. Needs BACKTEST_LIVE_URL, BACKTEST_LIVE_KEY and BACKTEST_LIVE_TOKEN; a no-op without them. Never raises.

    put(run={"run_id", "state": running|finished|failed, "note"}, ideas=[{id, run_id, account, source, rule, stage,
        status, why, detail}, ...])"""
from __future__ import annotations

import json
import os
import urllib.request

VARS = ("BACKTEST_LIVE_URL", "BACKTEST_LIVE_KEY", "BACKTEST_LIVE_TOKEN")
TIMEOUT = 5


def enabled() -> bool:
    return all(os.environ.get(v) for v in VARS)


def put(run: dict | None = None, ideas=()) -> bool:
    """True when the row batch was accepted; False when disabled or failed (one log line, no secrets)."""
    if not enabled():
        return False
    url, key, token = (os.environ[v] for v in VARS)
    try:
        body = json.dumps({"p_token": token, "p_run": run, "p_ideas": list(ideas)}, default=str).encode()
        req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/rpc/live_put", data=body, method="POST", headers={
            "apikey": key, "Content-Type": "application/json", "User-Agent": "backtest-lab-live/1"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            r.read()
        return True
    except Exception as e:
        print(f"live: put failed ({type(e).__name__}{getattr(e, 'code', '')})")
        return False
