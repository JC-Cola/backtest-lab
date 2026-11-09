from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import watchdog as W

NY = ZoneInfo("America/New_York")


def ny(s):
    return datetime.fromisoformat(s).replace(tzinfo=NY)


def run(when, conclusion="success"):
    return {"created_at": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "conclusion": conclusion}


def healthy(now):
    """A normal weekday state as of `now`."""
    day = now.replace(hour=0, minute=0)
    return {"intraday": [run(now - timedelta(minutes=10))],
            "morning": [run(day.replace(hour=9, minute=15))],
            "nightly": [run(day - timedelta(hours=7))],        # yesterday's
            "research": [run(now - timedelta(minutes=20))]}


def keys(found):
    return {k for k, _ in found}


def test_healthy_mid_session_is_quiet():
    now = ny("2026-11-09 12:30")
    assert W.problems(now, healthy(now), True) == []


def test_weekend_or_holiday_skips_session_checks_but_not_research():
    now = ny("2026-11-26 18:00")   # Thanksgiving
    runs = {"intraday": [], "morning": [], "nightly": [], "research": [run(now - timedelta(minutes=5))]}
    assert W.problems(now, runs, False) == []
    runs["research"] = []
    assert keys(W.problems(now, runs, False)) == {"research_stale"}


def test_early_in_the_day_nothing_is_due_yet():
    now = ny("2026-11-09 09:30")   # before the 10:05 intraday window, before 09:45 morning, before nightly
    runs = {"intraday": [], "morning": [], "nightly": [], "research": [run(now - timedelta(minutes=10))]}
    assert W.problems(now, runs, True) == []


def test_stale_intraday_inside_the_window_only():
    now = ny("2026-11-09 12:30")
    runs = healthy(now)
    runs["intraday"] = [run(now - timedelta(minutes=50))]
    assert keys(W.problems(now, runs, True)) == {"intraday_stale"}
    runs["intraday"] = [run(now - timedelta(minutes=44))]
    assert W.problems(now, runs, True) == []
    late = ny("2026-11-09 16:30")   # after the window: no intraday expectation
    r2 = healthy(late)
    r2["intraday"] = []
    assert W.problems(late, r2, True) == []


def test_missing_morning_after_0945():
    now = ny("2026-11-09 09:50")
    runs = healthy(now)
    runs["morning"] = [run(now - timedelta(days=1))]
    assert keys(W.problems(now, runs, True)) == {"morning_missing"}


def test_missing_nightly_after_1715_winter_and_summer_grace():
    now = ny("2026-11-09 17:20")   # EST: 22:20 UTC, nightly (21:45 UTC) is 35 min overdue
    runs = healthy(now)
    assert keys(W.problems(now, runs, True)) == {"nightly_missing"}
    runs["nightly"] = [run(now - timedelta(minutes=30))]
    assert W.problems(now, runs, True) == []
    summer = ny("2026-07-14 17:20")   # EDT: 21:20 UTC, the nightly is not due until 21:45 UTC + grace
    assert "nightly_missing" not in keys(W.problems(summer, healthy(summer), True))
    summer_late = ny("2026-07-14 18:20")   # 22:20 UTC
    assert "nightly_missing" in keys(W.problems(summer_late, healthy(summer_late), True))


def test_research_stale_90_min():
    now = ny("2026-11-09 12:30")
    runs = healthy(now)
    runs["research"] = [run(now - timedelta(minutes=95))]
    assert keys(W.problems(now, runs, True)) == {"research_stale"}


def test_two_failures_in_a_row():
    now = ny("2026-11-09 12:30")
    runs = healthy(now)
    runs["intraday"] = [run(now - timedelta(minutes=5), "failure"), run(now - timedelta(minutes=35), "failure")]
    assert keys(W.problems(now, runs, True)) == {"intraday_failing"}
    runs["intraday"] = [run(now - timedelta(minutes=5), "failure"), run(now - timedelta(minutes=35), "success")]
    assert W.problems(now, runs, True) == []
    runs["intraday"] = [run(now - timedelta(minutes=2), None), run(now - timedelta(minutes=5), "failure"),
                        run(now - timedelta(minutes=35), "failure")]   # a running run is skipped
    assert keys(W.problems(now, runs, True)) == {"intraday_failing"}


def test_throttle_three_hours():
    now = ny("2026-11-09 12:30")
    runs = healthy(now)
    runs["research"] = []
    found = W.problems(now, runs, True)
    state = W.record({}, [k for k, _ in found], now)
    assert W.problems(now + timedelta(hours=2, minutes=59), {**runs, "intraday": [run(now + timedelta(hours=2, minutes=50))]}, True, state) == []
    again = W.problems(now + timedelta(hours=3, minutes=1), {**runs, "intraday": [run(now + timedelta(hours=3))]}, True, state)
    assert keys(again) == {"research_stale"}
    # a different problem is not throttled by the first
    other = healthy(now)
    other["intraday"] = []
    assert keys(W.problems(now + timedelta(minutes=20), {**other, "research": [run(now)]}, True, state)) == {"intraday_stale"}


def test_message_lists_every_problem():
    m = W.message([("a", "first"), ("b", "second")])
    assert "first" in m and "second" in m
