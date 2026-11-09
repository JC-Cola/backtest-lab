"""Which Alpaca credential to use for market DATA (bars, news, assets, calendar). Data needs any valid paper key, so
the long-term account's key is no longer required: the first pair that is set (and, when probed, still answers) wins.

    data_keys() -> (key, secret) or (None, None)

Order: ALPACA_INTRADAY_* -> ALPACA_DTS_* -> ALPACA_TA_* -> ALPACA_* (legacy long-term account, being retired).
The probe is one cheap GET /v2/clock per process and only a 401/403 counts as "not working"; a network error keeps the
pair (so an outage never makes the code fall through to a worse key). Secrets are never printed.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request

ORDER = [("ALPACA_INTRADAY_API_KEY", "ALPACA_INTRADAY_SECRET_KEY"),
         ("ALPACA_DTS_API_KEY", "ALPACA_DTS_SECRET_KEY"),
         ("ALPACA_TA_API_KEY", "ALPACA_TA_SECRET_KEY"),
         ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")]   # legacy long-term account
CLOCK_URL = "https://paper-api.alpaca.markets/v2/clock"
_cache: dict = {}


def pairs(env=None):
    """[(env name of the key, key, secret)] for every pair that is set, in priority order."""
    env = os.environ if env is None else env
    return [(k, env[k], env[s]) for k, s in ORDER if env.get(k) and env.get(s)]


def works(key, secret, urlopen=urllib.request.urlopen):
    """False only when Alpaca answers 401/403 (key deleted or wrong); True otherwise, including network errors."""
    req = urllib.request.Request(CLOCK_URL, headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
    try:
        with urlopen(req, timeout=10):
            return True
    except urllib.error.HTTPError as e:
        return e.code not in (401, 403)
    except Exception:
        return True


def data_keys(env=None, probe=True, urlopen=urllib.request.urlopen):
    """(key, secret) of the first working pair, else (None, None). Cached per process when `env` is the real environment."""
    real = env is None
    if real and probe and "keys" in _cache:
        return _cache["keys"]
    found = pairs(env)
    pick = (None, None)
    for _, k, s in found:
        if not probe or len(found) == 1 or works(k, s, urlopen):
            pick = (k, s)
            break
    if pick == (None, None) and found:   # nothing answered: hand back the first set pair, the caller's own error is clearer
        pick = (found[0][1], found[0][2])
    if real and probe:
        _cache["keys"] = pick
    return pick


def clear_cache():
    _cache.clear()
