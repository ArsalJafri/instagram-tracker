from __future__ import annotations

import pytest
import requests

from instagram_tracker.sources import StorySourceError
from instagram_tracker.sources.igexport import IGExportStorySource


def test_parses_fixture_into_stories(igexport_payload):
    stories = IGExportStorySource.parse(igexport_payload, "zero2sudo")

    assert len(stories) == 9
    assert all(story.username == "zero2sudo" for story in stories)
    assert stories == sorted(stories, key=lambda s: s.posted_at)


def test_extracts_the_job_link_from_the_fixture(igexport_payload):
    stories = IGExportStorySource.parse(igexport_payload, "zero2sudo")
    with_links = [story for story in stories if story.links]

    assert len(with_links) == 1
    story = with_links[0]
    assert story.story_id == "3960059145617903970"
    assert story.links[0].canonical_url == (
        "https://sonypicturesjobs.com/job/-/-/22978/98897894576"
    )
    assert story.links[0].original_url.startswith("https://l.instagram.com/?u=")


def test_posted_at_comes_from_taken_at(igexport_payload):
    stories = IGExportStorySource.parse(igexport_payload, "zero2sudo")
    assert stories[0].posted_at.isoformat() == "2026-08-09T17:17:36+00:00"


def test_missing_items_is_an_error():
    with pytest.raises(StorySourceError):
        IGExportStorySource.parse({"data": {}}, "zero2sudo")


def test_malformed_items_are_skipped_not_fatal():
    payload = {"data": {"items": [{"no_id": True}, "junk", {"id": 7, "taken_at": 1700000000}]}}
    stories = IGExportStorySource.parse(payload, "zero2sudo")
    assert [story.story_id for story in stories] == ["7"]


# -- transient failures ---------------------------------------------------
#
# Added 2026-09-03, after igexport returned 502 on 10 of 12 requests while still
# serving a complete payload on the other two. One attempt per poll made a flaky API
# look like a dead one; the poller's backoff then spent each 17% chance 8 minutes apart.


class SequenceSession:
    """Replays a scripted sequence of outcomes, one per request."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SequenceResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} Server Error", response=response)


def source(outcomes):
    waits = []
    src = IGExportStorySource(
        session=SequenceSession(outcomes), sleep=lambda s: waits.append(s)
    )
    return src, waits


def test_a_502_is_retried_within_one_poll(igexport_payload):
    """The measured failure mode: transient 502s, then a complete payload."""
    src, waits = source(
        [http_error(502), http_error(502), SequenceResponse(igexport_payload)]
    )

    stories = src.fetch_stories("zero2sudo")

    assert stories, "the recovered payload should still parse into Stories"
    assert src.session.calls == 3
    assert waits == [2.0, 4.0]


def test_retries_are_bounded_and_then_it_gives_up():
    src, waits = source([http_error(502)] * 4)

    with pytest.raises(StorySourceError, match="IGExport request failed"):
        src.fetch_stories("zero2sudo")

    assert src.session.calls == 4, "MAX_ATTEMPTS, not an unbounded loop"
    assert waits == [2.0, 4.0, 8.0]


def test_a_4xx_is_not_retried():
    """A bad username never comes good, and repeating it only adds load."""
    src, waits = source([http_error(404)])

    with pytest.raises(StorySourceError):
        src.fetch_stories("nosuchuser")

    assert src.session.calls == 1
    assert waits == []


def test_a_timeout_is_retried(igexport_payload):
    """No response means no status to read, so it counts as transient."""
    src, waits = source(
        [requests.Timeout("timed out"), SequenceResponse(igexport_payload)]
    )

    assert src.fetch_stories("zero2sudo")
    assert src.session.calls == 2


def test_a_first_attempt_success_does_not_sleep(igexport_payload):
    src, waits = source([SequenceResponse(igexport_payload)])

    assert src.fetch_stories("zero2sudo")
    assert src.session.calls == 1
    assert waits == [], "the healthy path must not pay for the retry logic"
