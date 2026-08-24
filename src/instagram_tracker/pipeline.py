"""Wires the pipeline together: Stories in, Discord notifications out.

Startup behaviour follows the spec: with PROCESS_EXISTING_STORIES_ON_STARTUP=false the
Stories that are already live the first time the database is used get recorded as seen
but never notified, so a fresh install does not replay the past 24 hours. Later runs
find a populated database and process normally.
"""

from __future__ import annotations

import logging
import time
from typing import NamedTuple

from .classification import classify_job, to_job
from .config import Config
from .db import Database
from .jobs import JobFetcher
from .models import Classification, Link, Story
from .notifier import DiscordNotifier
from .sources.base import StorySource

log = logging.getLogger(__name__)


class LinkOutcome(NamedTuple):
    """What happened to one link.

    ``complete`` is the important half. A link is complete when it has been delivered,
    when it was already delivered, or when there is no channel that could ever take it.
    It is *incomplete* only when a send genuinely failed — and an incomplete link keeps
    its Story out of ``processed_stories``, so the next poll picks the Story up again
    and retries delivery. Without that, moving the link record alone would change
    nothing: Story-level deduplication would skip the whole Story before any link was
    reconsidered.
    """

    sent: bool
    complete: bool


class Pipeline:
    def __init__(
        self,
        config: Config,
        db: Database,
        source: StorySource,
        fetcher: JobFetcher,
        notifier: DiscordNotifier,
        health=None,
    ) -> None:
        self.config = config
        self.db = db
        self.source = source
        self.fetcher = fetcher
        self.notifier = notifier
        self.health = health
        # canonical_url -> monotonic deadline before which retrying is pointless, set
        # from Discord's own Retry-After. Held in memory rather than in the database:
        # losing it on restart costs one extra attempt, which is cheaper than a schema
        # change and self-corrects immediately.
        self._deferred: dict[str, float] = {}

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

        # Asked once for the whole batch rather than once per Story: the database is
        # remote now, so a per-Story check is a per-Story network round trip.
        known = self.db.known_story_ids([story.story_id for story in stories])

        sent = 0
        for story in stories:
            if story.story_id in known:
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
        complete = True
        for link in story.links:
            outcome = self._process_link(story, link)
            sent += int(outcome.sent)
            complete = complete and outcome.complete

        if not complete:
            # Deliberately left unrecorded so the next poll sees the Story again. Live
            # Stories expire after 24 hours, which bounds the retrying on its own.
            log.warning(
                "Story %s has undelivered links; leaving it unprocessed so they retry",
                story.story_id,
            )
            return sent

        self.db.mark_story_processed(
            story.story_id, story.username, story.posted_at, notified=bool(sent)
        )
        return sent

    def _process_link(self, story: Story, link: Link) -> LinkOutcome:
        if self.db.is_notified(link.canonical_url):
            log.info("Skipping already notified link %s", link.canonical_url)
            return LinkOutcome(sent=False, complete=True)

        # Discord asked us to wait, so wait. This check sits above the fetch on purpose:
        # a rate limit is a reason to touch nothing at all, and retrying every minute
        # was re-reading the employer's careers page as often as it was re-sending.
        if (deadline := self._deferred.get(link.canonical_url)) is not None:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                log.info(
                    "Holding %s for another %.0fs at Discord's request",
                    link.canonical_url,
                    remaining,
                )
                return LinkOutcome(sent=False, complete=False)
            del self._deferred[link.canonical_url]

        # Known but never delivered means a previous send failed, so this is a retry.
        # Fetching and classifying again is wasteful but idempotent; recording the
        # corpus again is not, and the corpus must hold one observation per posting
        # rather than one per delivery attempt.
        retrying = self.db.is_link_known(link.canonical_url)
        if retrying:
            log.info("Retrying undelivered link %s", link.canonical_url)
        else:
            self.db.record_link(link.canonical_url, link.original_url, story.story_id)

        details = self.fetcher.fetch(link.canonical_url)
        result = classify_job(
            details,
            link.canonical_url,
            role_threshold=self.config.role_confidence_threshold,
            employment_threshold=self.config.employment_confidence_threshold,
            poor_input_penalty=self.config.poor_input_confidence_penalty,
        )
        job = to_job(result, details, link.canonical_url)
        if not retrying:
            self._record_corpus(story, link, details, result)
        self.db.record_job(
            link.canonical_url,
            job.title,
            job.company,
            job.location,
            job.classification.value,
            job.reason,
            job.role_type.value if job.classification is Classification.RELEVANT else None,
        )
        log.info(
            "%s -> %s (%s): %s",
            link.canonical_url,
            result.destination.value,
            job.title or "unknown title",
            job.reason,
        )

        # Every link is offered to the notifier. A confirmed match goes to its role
        # channel; everything else goes to the review channel, so a link @zero2sudo
        # posted is never silently dropped.
        if not self.notifier.webhook_for(job):
            # Nothing was delivered and nothing can be, so this link is finished rather
            # than pending. Retrying it forever would refetch the posting every minute.
            self._record_decision(
                link.canonical_url, job, sent=False, note="none (no channel configured)"
            )
            return LinkOutcome(sent=False, complete=True)

        result = self.notifier.notify(job, story.username)
        if result.sent:
            self.db.record_notification(link.canonical_url, story.story_id)
            self._deferred.pop(link.canonical_url, None)
            note = ""
        elif result.retry_after:
            self._deferred[link.canonical_url] = time.monotonic() + result.retry_after
            note = f"rate limited (retrying in {result.retry_after:.0f}s)"
        else:
            note = "send failed (will retry)"

        self._record_decision(link.canonical_url, job, sent=result.sent, note=note)
        return LinkOutcome(sent=result.sent, complete=result.sent)

    def _record_corpus(self, story: Story, link: Link, details, result) -> None:
        """Persist the posting and the verdict for later labelling and retraining.

        Failing to store training data must never cost a notification, so this is best
        effort: the corpus is valuable, but not more valuable than the alert the user is
        waiting for.
        """
        try:
            observation_id = self.db.record_observation(
                canonical_url=link.canonical_url,
                story_id=story.story_id,
                title=details.title,
                raw_text=details.text,
                fetch_source=details.source,
                company=details.company,
                location=details.location,
                declared_employment_type=details.employment_type,
            )
            self.db.record_classification_run(
                observation_id=observation_id,
                classifier_version=result.classifier_version,
                role=result.role.value,
                role_confidence=result.role_confidence,
                employment=result.employment.value,
                employment_confidence=result.employment_confidence,
                destination=result.destination.value,
                classification_source=result.source.value,
                input_quality=result.input_quality.value,
                rule=result.rule,
                evidence=result.evidence,
            )
        except Exception:  # noqa: BLE001 - corpus capture must not break the pipeline
            log.exception("Could not record corpus entry for %s", link.canonical_url)
            if self.health:
                self.health.record_corpus(ok=False)
            return
        if self.health:
            self.health.record_corpus(ok=True)

    def _record_decision(self, url: str, job, sent: bool, note: str) -> None:
        """Remember where this link went, so the health endpoint can answer for it.

        ``note`` says why nothing was sent. It used to be hardcoded to "no channel
        configured", which reported a delivery failure as a configuration mistake and
        sent an investigation looking in the wrong place for three days.
        """
        if not self.health:
            return
        if not sent:
            destination = note
        elif job.classification is Classification.RELEVANT:
            destination = job.role_type.value
        else:
            destination = "review"
        verdict = "near_miss" if job.near_miss else job.classification.value
        self.health.record_decision(url, job.title, verdict, destination)


def build_pipeline(config: Config, db: Database, health=None) -> Pipeline:
    from .sources import build_story_source

    return Pipeline(
        config=config,
        db=db,
        source=build_story_source(
            config.story_provider,
            bio_interval_seconds=config.bio_poll_interval_seconds,
        ),
        fetcher=JobFetcher(render_proxy_url=config.render_proxy_url),
        notifier=DiscordNotifier(
            config.discord_webhook_url,
            config.discord_internship_webhook_url,
            mentions=config.discord_mentions,
            internship_mentions=config.discord_internship_mentions,
            unknown_webhook_url=config.discord_unknown_webhook_url,
        ),
        health=health,
    )
