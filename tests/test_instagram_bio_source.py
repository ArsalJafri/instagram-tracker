from __future__ import annotations

import pytest

from instagram_tracker.sources import build_story_source
from instagram_tracker.sources.base import StorySourceError
from instagram_tracker.sources.composite import CompositeStorySource
from instagram_tracker.sources.instagram_bio import InstagramBioSource

GOOGLE_JOB = (
    "https://google.com/about/careers/applications/jobs/results/"
    "78703249065943750-software-engineer-early-career-campus"
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSession:
    def __init__(self, payload=None, exc: Exception | None = None):
        self.payload = payload
        self.exc = exc
        self.calls = 0

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        if self.exc:
            raise self.exc
        return FakeResponse(self.payload)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_parses_the_captured_profile_payload(web_profile_payload):
    stories = InstagramBioSource.parse(web_profile_payload, "zero2sudo")

    urls = [story.links[0].canonical_url for story in stories]
    assert GOOGLE_JOB in urls
    assert all(story.username == "zero2sudo" for story in stories)
    assert all(story.story_id.startswith("bio:") for story in stories)


def test_external_url_duplicating_a_bio_link_yields_one_story(web_profile_payload):
    stories = InstagramBioSource.parse(web_profile_payload, "zero2sudo")
    canonicals = [story.links[0].canonical_url for story in stories]

    # external_url repeats bio_links[0] in the real payload.
    assert len(canonicals) == len(set(canonicals))


def test_synthetic_ids_are_stable_across_polls(web_profile_payload):
    first = InstagramBioSource.parse(web_profile_payload, "zero2sudo")
    second = InstagramBioSource.parse(web_profile_payload, "zero2sudo")

    assert [s.story_id for s in first] == [s.story_id for s in second]


def test_a_new_bio_link_gets_a_new_id():
    payload = {"data": {"user": {"username": "zero2sudo", "bio_links": [
        {"url": "https://example.com/jobs/1"}]}}}
    other = {"data": {"user": {"username": "zero2sudo", "bio_links": [
        {"url": "https://example.com/jobs/2"}]}}}

    assert (InstagramBioSource.parse(payload, "zero2sudo")[0].story_id
            != InstagramBioSource.parse(other, "zero2sudo")[0].story_id)


def test_lynx_wrapper_is_unwrapped():
    wrapped = (
        "https://l.instagram.com/?u=https%3A%2F%2Fexample.com%2Fjobs%2F7"
        "%3Ffbclid%3Dabc&e=AUC123"
    )
    payload = {"data": {"user": {"username": "z", "bio_links": [{"lynx_url": wrapped}]}}}

    story = InstagramBioSource.parse(payload, "z")[0]
    assert story.links[0].canonical_url == "https://example.com/jobs/7"


def test_a_profile_without_links_yields_nothing():
    payload = {"data": {"user": {"username": "z", "bio_links": [], "external_url": None}}}
    assert InstagramBioSource.parse(payload, "z") == []


def test_a_malformed_payload_is_rejected():
    with pytest.raises(StorySourceError):
        InstagramBioSource.parse({"data": {}}, "z")


def test_the_source_throttles_between_fetches(web_profile_payload):
    clock = FakeClock()
    session = FakeSession(payload=web_profile_payload)
    source = InstagramBioSource(min_interval_seconds=600, session=session, clock=clock)

    assert source.fetch_stories("zero2sudo") != []
    assert session.calls == 1

    clock.advance(60)
    assert source.fetch_stories("zero2sudo") == []
    assert session.calls == 1, "must not hit Instagram again inside the interval"

    clock.advance(600)
    source.fetch_stories("zero2sudo")
    assert session.calls == 2


def test_a_failed_fetch_still_starts_the_throttle(web_profile_payload):
    import requests

    clock = FakeClock()
    session = FakeSession(exc=requests.ConnectionError("boom"))
    source = InstagramBioSource(min_interval_seconds=600, session=session, clock=clock)

    with pytest.raises(StorySourceError):
        source.fetch_stories("zero2sudo")

    clock.advance(60)
    assert source.fetch_stories("zero2sudo") == []
    assert session.calls == 1, "a failing endpoint must not be retried every tick"


class StubSource:
    def __init__(self, stories=None, exc=None):
        self.stories = stories or []
        self.exc = exc

    def fetch_stories(self, username):
        if self.exc:
            raise self.exc
        return self.stories


def test_composite_survives_one_source_failing(web_profile_payload):
    good = StubSource(InstagramBioSource.parse(web_profile_payload, "zero2sudo"))
    bad = StubSource(exc=StorySourceError("down"))

    assert CompositeStorySource([bad, good]).fetch_stories("zero2sudo") != []


def test_composite_raises_only_when_every_source_fails():
    sources = [StubSource(exc=StorySourceError("a")), StubSource(exc=StorySourceError("b"))]

    with pytest.raises(StorySourceError, match="every story source failed"):
        CompositeStorySource(sources).fetch_stories("zero2sudo")


def test_build_story_source_single_and_composite():
    assert isinstance(build_story_source("instagram_bio"), InstagramBioSource)
    assert isinstance(build_story_source("igexport,instagram_bio"), CompositeStorySource)


def test_build_story_source_rejects_an_unknown_provider():
    with pytest.raises(ValueError, match="Unknown STORY_PROVIDER"):
        build_story_source("igexport,nonsense")


def test_bio_interval_reaches_the_source():
    source = build_story_source("instagram_bio", bio_interval_seconds=1234)
    assert source.min_interval_seconds == 1234
