"""Polling loop with backoff on provider failures."""

from __future__ import annotations

import logging
import time

from .heartbeat import Heartbeat
from .pipeline import Pipeline
from .sources.base import StorySourceError

log = logging.getLogger(__name__)

MAX_BACKOFF_MULTIPLIER = 8


class Poller:
    def __init__(
        self,
        pipeline: Pipeline,
        interval_seconds: int,
        heartbeat: Heartbeat | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.interval_seconds = interval_seconds
        self.heartbeat = heartbeat
        self._failures = 0

    def run_forever(self, sleep=time.sleep) -> None:
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
            return 0
        except Exception:  # a bad payload should not end the run
            self._failures += 1
            log.exception("Unexpected error during poll (failure %d)", self._failures)
            self._beat(ok=False)
            return 0

        self._failures = 0
        self._beat(ok=True)
        return sent

    def _beat(self, ok: bool) -> None:
        if self.heartbeat is None:
            return
        self.heartbeat.success() if ok else self.heartbeat.failure()

    def _next_delay(self) -> int:
        multiplier = min(2**self._failures, MAX_BACKOFF_MULTIPLIER)
        return self.interval_seconds * multiplier
