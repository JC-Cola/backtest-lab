"""Is the Claude proposer able to log in? A tiny state file (agent_memory/proposer_health.json, no secrets) written by
claude_cli.ask, one Telegram alert per UTC day while the login is broken, and a line for the morning briefing."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "agent_memory" / "proposer_health.json"
AUTH_CODES = (401, 403)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def scrub(text, limit: int = 200) -> str:
    """One line, no token-like strings."""
    t = re.sub(r"\s+", " ", str(text))
    t = re.sub(r"sk-[A-Za-z0-9_\-]{8,}|[A-Za-z0-9_\-]{32,}", "[redacted]", t)
    return t[:limit]


def is_auth_error(out: dict) -> bool:
    """A CLI result object that says the login was refused."""
    if not isinstance(out, dict) or not out.get("is_error"):
        return False
    if out.get("api_error_status") in AUTH_CODES:
        return True
    return bool(re.search(r"failed to authenticate|invalid bearer token|\b40[13]\b", str(out.get("result") or ""), re.I))


def read() -> dict:
    try:
        d = json.loads(Path(PATH).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d: dict):
    try:
        Path(PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(PATH).write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")
    except OSError as e:
        print(f"proposer_health: could not write ({type(e).__name__})")


def record_ok():
    d = read()
    _write({k: d[k] for k in ("last_alert_date",) if k in d} | {"status": "ok", "last_ok": _iso(_now()),
                                                                "last_error": d.get("last_error")})


def record_auth_error(text):
    """Keeps error_since from the first failure of an unbroken streak."""
    d, now = read(), _iso(_now())
    since = d.get("error_since") if d.get("status") == "auth_error" and d.get("error_since") else now
    _write(d | {"status": "auth_error", "last_error": now, "error_since": since, "error": scrub(text)})


def alert_text(d: dict) -> str:
    since = str(d.get("error_since", "?")).replace("T", " ")[:16]
    return f"⚠️ Research: Claude proposer can't log in (401) since {since} UTC — renew CLAUDE_CODE_OAUTH_TOKEN"


def alert_if_needed(send, now: datetime | None = None) -> bool:
    """send(text) -> bool (telegram.send). At most one alert per UTC day; only marked as sent when send succeeds."""
    d, today = read(), (now or _now()).date().isoformat()
    if d.get("status") != "auth_error" or d.get("last_alert_date") == today:
        return False
    if send(alert_text(d)):
        _write(d | {"last_alert_date": today})
        return True
    return False


def briefing_warning(max_age_h: float = 36, now: datetime | None = None) -> str | None:
    """Warning line for the morning briefing while the login is broken (and the failure is recent)."""
    d = read()
    if d.get("status") != "auth_error":
        return None
    try:
        age = ((now or _now()) - datetime.fromisoformat(d["last_error"])).total_seconds() / 3600
    except (KeyError, ValueError):
        return None
    if age > max_age_h:
        return None
    since = str(d.get("error_since", "?")).replace("T", " ")[:16]
    return f"Research: Claude proposer can't log in (401) since {since} UTC - renew CLAUDE_CODE_OAUTH_TOKEN"
