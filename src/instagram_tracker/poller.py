"""Polling loop with backoff on provider failures, and a slower overnight cadence."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .health import HealthState
from .heartbeat import Heartbeat
from .pipeline import Pipeline
from .sources.base import StorySourceError

log = logging.getLogger(__name__)

MAX_BACKOFF_MULTIPLIER = 8


class QuietHours:
    """A window of the day polled on a slower cadence.

    The window is given in a named timezone so it tracks daylight saving on its own;
    the account posts on Pacific wall-clock time, not on a fixed UTC offset.

    An unknown timezone name **fails open**: the quiet window is disabled and everything
    polls at the normal interval. A missing tzdata must not be able to make the tracker
    quieter than intended — polling too often is a wasted request, polling too rarely is
    a missed posting.
    """

    def __init__(self, timezone_name: str, start: int, end: int, interval_seconds: int):
        self.start = start
        self.end = end
        self.interval_seconds = interval_seconds
        try:
            self.tz = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            log.warning(
                "Unknown POLL_TIMEZONE %r; quiet hours disabled and every poll runs at "
                "the normal interval",
                timezone_name,
            )
            self.tz = None

    @property
    def enabled(self) -> bool:
        return self.tz is not None and self.start != self.end

    def contains(self, moment: datetime) -> bool:
        if not self.enabled:
            return False
        hour = moment.astimezone(self.tz).hour
        if self.start < self.end:
            return self.start <= hour < self.end
        # The window wraps past midnight, which is the ordinary case here (23:00-06:00).
        return hour >= self.start or hour < self.end


class Poller:
    def __init__(
        self,
        pipeline: Pipeline,
        interval_seconds: int,
        heartbeat: Heartbeat | None = None,
        health: HealthState | None = None,
        quiet_hours: QuietHours | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.interval_seconds = interval_seconds
        self.quiet_hours = quiet_hours
        self.heartbeat = heartbeat
        self.health = health
        self._failures = 0

    def run_forever(self, sleep=time.sleep) -> None:
        if self.quiet_hours is not None and self.quiet_hours.enabled:
            log.info(
                "Polling every %ds, and every %ds between %02d:00 and %02d:00 %s; "
                "press Ctrl+C to stop",
                self.interval_seconds,
                self.quiet_hours.interval_seconds,
                self.quiet_hours.start,
                self.quiet_hours.end,
                self.quiet_hours.tz,
            )
        else:
            log.info("Polling every %ds; press Ctrl+C to stop", self.interval_seconds)
        while True:
            self.tick()
            sleep(self._next_delay())

    def tick(self) -> int:
        """Run one cycle, swallowing provider errors so the loop survives them."""
        try:
            sent = self.pipeline.run_once()
        except StorySourceError as exc:
            self._failures += 1
            log.warning("Story source unavailable (failure %d): %s", self._failures, exc)
            self._beat(ok=False)
            if self.health:
                self.health.record_error(str(exc))
            return 0
        except Exception as exc:  # a bad payload should not end the run
            self._failures += 1
            log.exception("Unexpected error during poll (failure %d)", self._failures)
            self._beat(ok=False)
            if self.health:
                self.health.record_error(repr(exc))
            return 0

        self._failures = 0
        self._beat(ok=True)
        if self.health:
            self.health.record_poll(sent)
        return sent

    def _beat(self, ok: bool) -> None:
        if self.heartbeat is None:
            return
        self.heartbeat.success() if ok else self.heartbeat.failure()

    def _base_interval(self, now: datetime | None = None) -> int:
        """The interval for right now, before any failure backoff is applied."""
        if self.quiet_hours is None or not self.quiet_hours.enabled:
            return self.interval_seconds
        moment = now or datetime.now(self.quiet_hours.tz)
        if self.quiet_hours.contains(moment):
            return self.quiet_hours.interval_seconds
        return self.interval_seconds

    def _next_delay(self) -> int:
        multiplier = min(2**self._failures, MAX_BACKOFF_MULTIPLIER)
        return self._base_interval() * multiplier
