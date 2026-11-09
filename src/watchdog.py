"""Dead-man's switch for the cloud jobs, independent of Supabase.

All trading jobs are started by Supabase pg_cron -> GitHub API dispatch, and their failure alerts live inside the
workflows, so a dead timer / expired token / broken dispatch stops everything silently. This script runs from GitHub's
own `schedule` (watchdog.yml), reads the workflow runs through the REST API (GITHUB_TOKEN, actions: read) and sends ONE
Telegram message listing what is wrong.

    python src/watchdog.py [--dry-run]       env: GITHUB_TOKEN, GITHUB_REPOSITORY, TELEGRAM_*, ALPACA_* (calendar only)

Checks (New York time; a US trading day = Alpaca calendar, weekday when Alpaca is unreachable):
  a) 10:05-16:05: an `intraday` run was created in the last 45 min
  b) after 09:45: a `morning` run exists today
  c) after 17:15 (and not before 22:15 UTC, i.e. 30 min after the 21:45 UTC dispatch, which is 17:45 NY in summer): a `nightly` run exists today
  d) a `research` run in the last 90 min (any day and time this script runs)
  e) the last two finished intraday runs / the last two finished nightly runs are not both "failure"
Throttle: one alert per problem per 3 hours. State = agent_memory/watchdog.json, committed by the workflow only when it
changes (i.e. only after an alert), so it costs no commits on healthy days.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
STATE = Path(__file__).resolve().parent.parent / "agent_memory" / "watchdog.json"
WORKFLOWS = ("intraday", "morning", "nightly", "research")
THROTTLE = timedelta(hours=3)
INTRADAY_WINDOW = (time(10, 5), time(16, 5))
INTRADAY_MAX_AGE, RESEARCH_MAX_AGE = timedelta(minutes=45), timedelta(minutes=90)
MORNING_AFTER, NIGHTLY_AFTER = time(9, 45), time(17, 15)
NIGHTLY_DISPATCH_UTC = time(21, 45)
NIGHTLY_GRACE = timedelta(minutes=30)


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def _created(runs):
    return sorted((_t(r["created_at"]) for r in runs or []), reverse=True)


def _two_failures(runs):
    done = [r for r in sorted(runs or [], key=lambda r: r["created_at"], reverse=True) if r.get("conclusion")]
    return len(done) >= 2 and done[0]["conclusion"] == "failure" and done[1]["conclusion"] == "failure"


def problems(now_ny, runs_by_workflow, is_trading_day, state=None):
    """[(key, text)] of the problems to alert on now (throttled by `state['alerts']` = {key: ISO UTC of the last alert})."""
    now_u = now_ny.astimezone(UTC)
    today = now_ny.astimezone(NY).date()
    t = now_ny.astimezone(NY).time()
    runs = {w: _created(runs_by_workflow.get(w)) for w in WORKFLOWS}
    found = []
    if is_trading_day:
        if INTRADAY_WINDOW[0] <= t <= INTRADAY_WINDOW[1] and not any(now_u - c <= INTRADAY_MAX_AGE for c in runs["intraday"]):
            last = runs["intraday"][0].astimezone(NY).strftime("%a %H:%M") if runs["intraday"] else "never"
            found.append(("intraday_stale", f"no intraday run in the last 45 min (last: {last} NY)"))
        if t >= MORNING_AFTER and not any(c.astimezone(NY).date() == today for c in runs["morning"]):
            found.append(("morning_missing", "no morning run today (expected by 09:45 NY)"))
        nightly_due = datetime.combine(now_u.date(), NIGHTLY_DISPATCH_UTC, UTC) + NIGHTLY_GRACE
        if t >= NIGHTLY_AFTER and now_u >= nightly_due and not any(c.astimezone(NY).date() == today for c in runs["nightly"]):
            found.append(("nightly_missing", "no nightly run today (expected by 17:15 NY)"))
    if not any(now_u - c <= RESEARCH_MAX_AGE for c in runs["research"]):
        last = runs["research"][0].astimezone(NY).strftime("%a %H:%M") if runs["research"] else "never"
        found.append(("research_stale", f"no research run in the last 90 min (last: {last} NY)"))
    for w in ("intraday", "nightly"):
        if _two_failures(runs_by_workflow.get(w)):
            found.append((f"{w}_failing", f"the last two {w} runs both failed"))
    sent = (state or {}).get("alerts", {})
    return [(k, x) for k, x in found if not (k in sent and now_u - _t(sent[k]) < THROTTLE)]


def record(state, sent_keys, now_ny):
    """State with the alert time stored for every key just sent."""
    alerts = dict((state or {}).get("alerts", {}))
    for k in sent_keys:
        alerts[k] = now_ny.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"alerts": alerts}


def message(found):
    return "🐕 Watchdog: the cloud jobs look unhealthy\n" + "\n".join(f"• {x}" for _, x in found) \
        + "\nCheck Supabase pg_cron (bl-* jobs), the dispatch token and GitHub Actions."


def trading_day(day):
    """Alpaca calendar answer; a weekday counts as a trading day when Alpaca is unreachable."""
    try:
        import alpaca_keys
        from alpaca.trading.client import TradingClient
        from alpaca.trading.requests import GetCalendarRequest
        k, s = alpaca_keys.data_keys()
        cal = TradingClient(k, s, paper=True).get_calendar(GetCalendarRequest(start=day, end=day))
        return day in [c.date for c in cal]
    except Exception as e:
        print(f"calendar unavailable ({type(e).__name__}); assuming weekday = trading day", flush=True)
        return day.weekday() < 5


def fetch_runs(repo, token, workflow, per_page=30):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}.yml/runs?per_page={per_page}",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return [{"created_at": x["created_at"], "conclusion": x.get("conclusion"), "status": x.get("status")}
                for x in json.load(r)["workflow_runs"]]


def load_state(path=STATE):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main(argv=None):
    dry = "--dry-run" in (argv if argv is not None else sys.argv[1:])
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        raise SystemExit("GITHUB_TOKEN / GITHUB_REPOSITORY not set")
    now = datetime.now(NY)
    runs = {w: fetch_runs(repo, token, w) for w in WORKFLOWS}   # an API failure fails this job (visible in Actions)
    state = load_state()
    found = problems(now, runs, trading_day(now.date()), state)
    if not found:
        print("all clear" if not state.get("alerts") else "all clear (or throttled)")
        return 0
    text = message(found)
    print(text)
    if dry:
        return 0
    import telegram
    if telegram.send(text):
        STATE.write_text(json.dumps(record(state, [k for k, _ in found], now), indent=1) + "\n")
    else:
        print("telegram not sent (missing env or error); state not updated so the next run retries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
