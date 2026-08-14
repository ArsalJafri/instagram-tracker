"""End-to-end pipeline behaviour, driven entirely by fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from instagram_tracker.config import Config
from instagram_tracker.db import Database
from instagram_tracker.jobs import JobDetails
from instagram_tracker.models import Link, Story
from instagram_tracker.pipeline import Pipeline
from instagram_tracker.poller import Poller
from instagram_tracker.sources.base import StorySource, StorySourceError

RELEVANT_URL = "https://boards.greenhouse.io/acme/jobs/1"
BORING_URL = "https://boards.greenhouse.io/acme/jobs/2"


class FakeSource(StorySource):
    def __init__(self, stories: list[Story], error: Exception | None = None):
        self.stories = stories
        self.error = error
        self.calls = 0

    def fetch_stories(self, username: str) -> list[Story]:
        self.calls += 1
        if self.error:
            raise self.error
        return self.stories


class FakeFetcher:
    def __init__(self):
        self.fetched = []

    def fetch(self, url: str) -> JobDetails:
        self.fetched.append(url)
        if url == RELEVANT_URL:
            title = "Software Engineer, New Grad"
        else:
            title = "Senior Software Engineer"
        return JobDetails(title, "Acme", "Seattle", "FULL_TIME", title, "json-ld")


class FakeNotifier:
    def __init__(self, succeed: bool = True):
        self.sent = []
        self.succeed = succeed

    @property
    def enabled(self) -> bool:
        return True

    def notify(self, job, username) -> bool:
        self.sent.append(job.url)
        return self.succeed


def story(story_id: str, urls: list[str] = ()) -> Story:
    return Story(
        story_id=story_id,
        username="zero2sudo",
        posted_at=datetime.now(timezone.utc),
        links=[Link(original_url=url, canonical_url=url) for url in urls],
    )


def make_config(tmp_path, process_existing: bool = False) -> Config:
    return Config(
        instagram_username="zero2sudo",
        story_provider="igexport",
        poll_interval_seconds=60,
        process_existing_stories_on_startup=process_existing,
        database_path=tmp_path / "test.db",
        discord_webhook_url="https://discord.test/webhook",
    )


@pytest.fixture
def build(tmp_path):
    created = {}

    def _build(stories, process_existing=False, source_error=None, notifier=None):
        config = make_config(tmp_path, process_existing)
        db = created.get("db") or Database(config.database_path)
        created["db"] = db
        source = FakeSource(stories, source_error)
        fetcher = FakeFetcher()
        notify = notifier or FakeNotifier()
        pipeline = Pipeline(config, db, source, fetcher, notify)
        return pipeline, db, fetcher, notify

    yield _build
    if "db" in created:
        created["db"].close()


def test_existing_stories_on_first_run_are_seeded_not_notified(build):
    pipeline, db, fetcher, notifier = build([story("1", [RELEVANT_URL])])

    assert pipeline.run_once() == 0
    assert notifier.sent == []
    assert fetcher.fetched == []
    assert db.is_story_processed("1") is True


def test_stories_arriving_after_seeding_are_notified(build):
    pipeline, db, fetcher, notifier = build([story("1", [RELEVANT_URL])])
    pipeline.run_once()

    pipeline.source.stories = [story("1", [RELEVANT_URL]), story("2", [RELEVANT_URL])]
    assert pipeline.run_once() == 1
    assert notifier.sent == [RELEVANT_URL]
    assert db.is_story_processed("2") is True


def test_process_existing_on_startup_notifies_immediately(build):
    pipeline, _, _, notifier = build([story("1", [RELEVANT_URL])], process_existing=True)

    assert pipeline.run_once() == 1
    assert notifier.sent == [RELEVANT_URL]


def test_irrelevant_jobs_are_recorded_and_sent_for_review(build):
    """Every link surfaces somewhere.

    Deciding which rejections were worth showing repeatedly guessed wrong, and the
    misses were invisible — so anything that is not a confirmed match now goes to the
    review channel rather than being dropped.
    """
    pipeline, db, _, notifier = build([story("1", [BORING_URL])], process_existing=True)

    assert pipeline.run_once() == 1
    assert notifier.sent == [BORING_URL]
    row = db.conn.execute(
        "SELECT classification FROM jobs WHERE canonical_url = ?", (BORING_URL,)
    ).fetchone()
    assert row["classification"] == "not_relevant"


def test_stories_without_links_are_skipped(build):
    pipeline, db, fetcher, notifier = build([story("1")], process_existing=True)

    assert pipeline.run_once() == 0
    assert fetcher.fetched == []
    assert db.is_story_processed("1") is True


def test_a_story_is_never_processed_twice(build):
    pipeline, _, fetcher, notifier = build([story("1", [RELEVANT_URL])], process_existing=True)

    assert pipeline.run_once() == 1
    assert pipeline.run_once() == 0
    assert fetcher.fetched == [RELEVANT_URL]
    assert notifier.sent == [RELEVANT_URL]


def test_the_same_link_in_a_new_story_is_not_notified_again(build):
    pipeline, _, fetcher, notifier = build([story("1", [RELEVANT_URL])], process_existing=True)
    pipeline.run_once()

    pipeline.source.stories = [story("1", [RELEVANT_URL]), story("2", [RELEVANT_URL])]
    assert pipeline.run_once() == 0
    assert notifier.sent == [RELEVANT_URL]
    assert fetcher.fetched == [RELEVANT_URL]


def test_a_failed_discord_send_is_not_recorded_as_notified(build):
    pipeline, db, _, _ = build(
        [story("1", [RELEVANT_URL])], process_existing=True, notifier=FakeNotifier(succeed=False)
    )

    assert pipeline.run_once() == 0
    assert db.is_notified(RELEVANT_URL) is False


def test_full_run_over_the_captured_igexport_payload(build, igexport_payload):
    """The recorded zero2sudo payload: nine Stories, one of them carrying a job link."""
    from instagram_tracker.sources.igexport import IGExportStorySource

    stories = IGExportStorySource.parse(igexport_payload, "zero2sudo")
    pipeline, db, fetcher, notifier = build(stories, process_existing=True)
    pipeline.run_once()

    assert fetcher.fetched == ["https://sonypicturesjobs.com/job/-/-/22978/98897894576"]
    assert all(db.is_story_processed(s.story_id) for s in stories)
    assert pipeline.run_once() == 0


def test_poller_survives_a_source_failure(build):
    pipeline, _, _, _ = build([], source_error=StorySourceError("down"))
    poller = Poller(pipeline, interval_seconds=60)

    assert poller.tick() == 0
    assert poller._next_delay() == 120


def test_poller_backoff_resets_after_a_good_poll(build):
    pipeline, _, _, _ = build([], source_error=StorySourceError("down"))
    poller = Poller(pipeline, interval_seconds=60)
    poller.tick()
    poller.tick()
    assert poller._next_delay() == 240

    pipeline.source.error = None
    poller.tick()
    assert poller._next_delay() == 60


def test_poller_backoff_is_capped(build):
    pipeline, _, _, _ = build([], source_error=StorySourceError("down"))
    poller = Poller(pipeline, interval_seconds=60)
    for _ in range(10):
        poller.tick()
    assert poller._next_delay() == 60 * 8
