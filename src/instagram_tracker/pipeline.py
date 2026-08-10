"""Wires the pipeline together: Stories in, Discord notifications out.

Startup behaviour follows the spec: with PROCESS_EXISTING_STORIES_ON_STARTUP=false the
Stories that are already live the first time the database is used get recorded as seen
but never notified, so a fresh install does not replay the past 24 hours. Later runs
find a populated database and process normally.
"""

from __future__ import annotations

import logging

from .classifier import classify
from .config import Config
from .db import Database
from .jobs import JobFetcher
from .models import Classification, Link, Story
from .notifier import DiscordNotifier
from .sources.base import StorySource

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self,
        config: Config,
        db: Database,
        source: StorySource,
        fetcher: JobFetcher,
        notifier: DiscordNotifier,
    ) -> None:
        self.config = config
        self.db = db
        self.source = source
        self.fetcher = fetcher
        self.notifier = notifier

    def run_once(self) -> int:
        """Poll once. Returns the number of notifications sent."""
        stories = self.source.fetch_stories(self.config.instagram_username)
        log.info("Fetched %d active Stories for @%s", len(stories), self.config.instagram_username)

        seeding = self._should_seed()
        if seeding:
            log.info(
                "First run: recording %d existing Stories without notifying "
                "(PROCESS_EXISTING_STORIES_ON_STARTUP=false)",
                len(stories),
            )

        sent = 0
        for story in stories:
            if self.db.is_story_processed(story.story_id):
                continue
            if seeding:
                self.db.mark_story_processed(
                    story.story_id, story.username, story.posted_at, notified=False
                )
                continue
            sent += self._process_story(story)
        return sent

    def _should_seed(self) -> bool:
        return not self.config.process_existing_stories_on_startup and not self.db.has_any_stories()

    def _process_story(self, story: Story) -> int:
        log.info("Processing Story %s (%d links)", story.story_id, len(story.links))
        sent = 0
        for link in story.links:
            if self._process_link(story, link):
                sent += 1
        self.db.mark_story_processed(
            story.story_id, story.username, story.posted_at, notified=bool(sent)
        )
        return sent

    def _process_link(self, story: Story, link: Link) -> bool:
        if self.db.is_link_known(link.canonical_url):
            log.info("Skipping already seen link %s", link.canonical_url)
            return False
        self.db.record_link(link.canonical_url, link.original_url, story.story_id)

        details = self.fetcher.fetch(link.canonical_url)
        job = classify(details, link.canonical_url)
        self.db.record_job(
            link.canonical_url,
            job.title,
            job.company,
            job.location,
            job.classification.value,
            job.reason,
        )
        log.info(
            "%s -> %s (%s): %s",
            link.canonical_url,
            job.classification.value,
            job.title or "unknown title",
            job.reason,
        )

        if job.classification is not Classification.RELEVANT:
            return False
        if self.db.is_notified(link.canonical_url):
            return False
        if not self.notifier.notify(job, story.username):
            return False
        self.db.record_notification(link.canonical_url, story.story_id)
        return True


def build_pipeline(config: Config, db: Database) -> Pipeline:
    from .sources import build_story_source

    return Pipeline(
        config=config,
        db=db,
        source=build_story_source(config.story_provider),
        fetcher=JobFetcher(),
        notifier=DiscordNotifier(config.discord_webhook_url),
    )
