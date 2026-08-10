from __future__ import annotations

import requests

from instagram_tracker.heartbeat import Heartbeat
from instagram_tracker.poller import Poller
from instagram_tracker.sources.base import StorySourceError

URL = "https://hc.test/ping/abc123"


class FakeSession:
    def __init__(self, exc: Exception | None = None):
        self.calls = []
        self.exc = exc

    def get(self, url, timeout=None):
        self.calls.append(url)
        if self.exc:
            raise self.exc
        return FakeResponse()


class FakeResponse:
    def raise_for_status(self):
        return None


def test_success_pings_the_base_url():
    session = FakeSession()
    assert Heartbeat(URL, session=session).success() is True
    assert session.calls == [URL]


def test_failure_pings_the_fail_endpoint():
    session = FakeSession()
    assert Heartbeat(URL, session=session).failure() is True
    assert session.calls == [f"{URL}/fail"]


def test_failure_endpoint_does_not_double_the_slash():
    session = FakeSession()
    Heartbeat(f"{URL}/", session=session).failure()
    assert session.calls == [f"{URL}/fail"]


def test_without_a_url_is_a_no_op():
    session = FakeSession()
    heartbeat = Heartbeat("", session=session)

    assert heartbeat.enabled is False
    assert heartbeat.success() is False
    assert session.calls == []


def test_a_broken_heartbeat_never_raises():
    session = FakeSession(exc=requests.ConnectionError("boom"))
    assert Heartbeat(URL, session=session).success() is False


class FakePipeline:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc

    def run_once(self) -> int:
        if self.exc:
            raise self.exc
        return 1


def test_poller_beats_on_a_good_poll():
    session = FakeSession()
    poller = Poller(FakePipeline(), 60, heartbeat=Heartbeat(URL, session=session))

    assert poller.tick() == 1
    assert session.calls == [URL]


def test_poller_reports_failure_on_a_source_error():
    session = FakeSession()
    pipeline = FakePipeline(exc=StorySourceError("down"))
    poller = Poller(pipeline, 60, heartbeat=Heartbeat(URL, session=session))

    assert poller.tick() == 0
    assert session.calls == [f"{URL}/fail"]


def test_poller_reports_failure_on_an_unexpected_error():
    session = FakeSession()
    poller = Poller(FakePipeline(exc=ValueError("bad payload")), 60,
                    heartbeat=Heartbeat(URL, session=session))

    assert poller.tick() == 0
    assert session.calls == [f"{URL}/fail"]


def test_poller_without_a_heartbeat_still_ticks():
    assert Poller(FakePipeline(), 60).tick() == 1
