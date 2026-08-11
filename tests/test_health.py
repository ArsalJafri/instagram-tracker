from __future__ import annotations

import json
import urllib.request

from instagram_tracker.health import HealthState, serve_in_background
from instagram_tracker.poller import Poller
from instagram_tracker.sources.base import StorySourceError


class FakePipeline:
    def __init__(self, sent: int = 0, exc: Exception | None = None):
        self.sent = sent
        self.exc = exc

    def run_once(self) -> int:
        if self.exc:
            raise self.exc
        return self.sent


def test_a_fresh_state_reports_no_poll_yet():
    snapshot = HealthState().snapshot()

    assert snapshot["status"] == "ok"
    assert snapshot["polls"] == 0
    assert snapshot["last_poll_at"] is None
    assert snapshot["seconds_since_last_poll"] is None


def test_the_poller_records_successful_polls():
    health = HealthState()
    poller = Poller(FakePipeline(sent=2), 60, health=health)

    poller.tick()
    poller.tick()
    snapshot = health.snapshot()

    assert snapshot["polls"] == 2
    assert snapshot["notifications_sent"] == 4
    assert snapshot["last_poll_at"] is not None
    assert snapshot["last_error"] is None


def test_a_source_failure_is_visible_without_advancing_the_poll_count():
    # A process that is up but no longer polling is the failure worth catching, so a
    # failed tick must not look like a successful one.
    health = HealthState()
    poller = Poller(FakePipeline(exc=StorySourceError("down")), 60, health=health)

    poller.tick()
    snapshot = health.snapshot()

    assert snapshot["polls"] == 0
    assert snapshot["last_poll_at"] is None
    assert "down" in snapshot["last_error"]


def test_a_later_success_clears_the_error():
    health = HealthState()
    Poller(FakePipeline(exc=StorySourceError("down")), 60, health=health).tick()
    Poller(FakePipeline(sent=0), 60, health=health).tick()

    assert health.snapshot()["last_error"] is None


def test_the_endpoint_serves_the_snapshot_as_json():
    health = HealthState()
    health.record_poll(1)
    server = serve_in_background(health, 0)  # port 0 -> OS picks a free one
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert response.status == 200
            payload = json.loads(response.read())
    finally:
        server.shutdown()

    assert payload["status"] == "ok"
    assert payload["polls"] == 1
    assert payload["notifications_sent"] == 1


def test_the_poller_works_without_a_health_state():
    assert Poller(FakePipeline(sent=3), 60).tick() == 3
