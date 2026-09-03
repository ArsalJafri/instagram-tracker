"""Quiet-hours polling schedule."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from instagram_tracker.health import HealthState
from instagram_tracker.poller import Poller, QuietHours


# -- quiet hours ----------------------------------------------------------
#
# Added 2026-09-03. Polling ran at a flat 60s around the clock. Across 96 Stories in two
# samples a month apart — 59 on 2026-08-09..11 and 37 on 2026-09-02..03 — not one was
# posted between 23:00 and 06:00 Pacific, so roughly a quarter of every day's requests
# were spent on hours the account has never used.


def at(hour, tz="America/Los_Angeles", minute=0):
    return datetime(2026, 9, 3, hour, minute, tzinfo=ZoneInfo(tz))


def quiet(start=23, end=6, interval=600, tz="America/Los_Angeles"):
    return QuietHours(tz, start, end, interval)


@pytest.mark.parametrize("hour", [23, 0, 1, 3, 5])
def test_hours_inside_the_overnight_window_are_quiet(hour):
    assert quiet().contains(at(hour)) is True


@pytest.mark.parametrize("hour", [6, 9, 13, 18, 22])
def test_hours_outside_it_are_not(hour):
    assert quiet().contains(at(hour)) is False


def test_the_window_wraps_past_midnight():
    """23:00-06:00 is a wrap, not an empty range — the naive `start <= h < end` fails."""
    window = quiet()
    assert window.contains(at(23)) and window.contains(at(2))
    assert not window.contains(at(12))


def test_a_non_wrapping_window_still_works():
    window = quiet(start=1, end=5)
    assert window.contains(at(3)) is True
    assert window.contains(at(23)) is False


def test_the_quiet_interval_is_used_overnight():
    poller = Poller(None, 60, quiet_hours=quiet())

    assert poller._base_interval(at(3)) == 600
    assert poller._base_interval(at(13)) == 60


def test_backoff_still_multiplies_the_quiet_interval():
    """A failing provider at 03:00 must back off from 600s, not from 60s."""
    poller = Poller(None, 60, quiet_hours=quiet())
    poller._failures = 3

    assert poller._base_interval(at(3)) == 600
    assert poller._base_interval(at(13)) == 60


def test_the_window_is_wall_clock_so_it_follows_daylight_saving():
    """The account posts on Pacific wall time; a fixed UTC offset would drift an hour."""
    window = quiet()
    summer = datetime(2026, 7, 1, 3, tzinfo=ZoneInfo("America/Los_Angeles"))
    winter = datetime(2026, 12, 1, 3, tzinfo=ZoneInfo("America/Los_Angeles"))

    assert window.contains(summer) and window.contains(winter)


def test_an_unknown_timezone_fails_open():
    """Missing tzdata must not silently make the tracker quieter than intended."""
    window = QuietHours("Not/AZone", 23, 6, 600)

    assert window.enabled is False
    assert window.contains(at(3)) is False
    assert Poller(None, 60, quiet_hours=window)._base_interval(at(3)) == 60


def test_an_empty_window_disables_the_feature():
    window = quiet(start=6, end=6)

    assert window.enabled is False
    assert Poller(None, 60, quiet_hours=window)._base_interval(at(3)) == 60


def test_no_quiet_hours_at_all_is_the_old_behaviour():
    assert Poller(None, 60)._base_interval(at(3)) == 60


# -- the health check must not call a quiet poller stalled -------------------
#
# Added 2026-09-03, same day and same change that caused it. `stale_after_seconds` was
# max(poll_interval * 5, 300) = 300s while the quiet interval was 600s, so between
# overnight polls the endpoint reported "stalled" and answered 503 — every night, while
# working exactly as designed, to whatever pinger was watching.


def health_after_poll(expected_interval, seconds_ago):
    state = HealthState(stale_after_seconds=300)
    state.set_expected_interval(expected_interval)
    state.record_poll(0)
    state.last_poll_at = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return state.snapshot()


def test_a_quiet_interval_gap_is_not_stalled():
    """800s between polls is the schedule working, not a fault."""
    assert health_after_poll(800, seconds_ago=790)["status"] == "ok"


def test_daytime_sensitivity_is_not_lost():
    """A 60s poller silent for 400s is genuinely stalled and must still say so."""
    assert health_after_poll(60, seconds_ago=400)["status"] == "stalled"


def test_a_quiet_poller_silent_for_five_intervals_is_stalled():
    assert health_after_poll(800, seconds_ago=4100)["status"] == "stalled"


def test_the_reported_threshold_reflects_the_active_interval():
    assert health_after_poll(800, seconds_ago=10)["stale_after_seconds"] == 4000
    assert health_after_poll(60, seconds_ago=10)["stale_after_seconds"] == 300


def test_the_poller_reports_its_interval_to_health():
    """The wiring, not just the arithmetic."""
    state = HealthState(stale_after_seconds=300)

    class Stub:
        def run_once(self):
            return 0

    poller = Poller(Stub(), 60, health=state, quiet_hours=quiet(interval=800))
    poller.tick()

    # Deterministic whatever hour the suite runs at: whichever interval applies now,
    # health must have been told about that one.
    expected = max(300, poller._base_interval() * 5)
    assert state.snapshot()["stale_after_seconds"] == expected
