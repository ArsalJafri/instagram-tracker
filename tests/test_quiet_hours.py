"""Quiet-hours polling schedule."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

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
