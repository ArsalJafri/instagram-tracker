"""Dead-man's switch pings for an external monitor (healthchecks.io or similar).

A tracker that dies quietly is indistinguishable from an account that simply stopped
posting: both look like an empty Discord channel. The alarm therefore has to come from
the absence of pings rather than the absence of notifications.

Ping failures are logged and swallowed. Monitoring the monitor must never be able to
stop the monitor.
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


class Heartbeat:
    def __init__(
        self,
        url: str,
        timeout: int = 10,
        session: requests.Session | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def success(self) -> bool:
        """Report a completed poll, resetting the external timer."""
        return self._ping(self.url)

    def failure(self) -> bool:
        """Report a failed poll, so repeated errors alert before the timer expires."""
        return self._ping(self.url.rstrip("/") + "/fail")

    def _ping(self, url: str) -> bool:
        if not self.enabled:
            return False
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Heartbeat ping failed: %s", exc)
            return False
        return True
