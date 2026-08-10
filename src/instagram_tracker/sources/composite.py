"""Fans one poll out across several providers.

The point is redundancy: one provider going down must not stop the others, so a partial
failure degrades rather than raising. Only a total failure propagates, because that is
the case where the poller should back off and the heartbeat should report trouble.

Sources may legitimately return the same link. Deduplication is on `canonical_url` in the
pipeline, so overlap costs an extra fetch at worst and never a duplicate notification.
"""

from __future__ import annotations

import logging

from ..models import Story
from .base import StorySource, StorySourceError

log = logging.getLogger(__name__)


class CompositeStorySource(StorySource):
    def __init__(self, sources: list[StorySource]) -> None:
        if not sources:
            raise ValueError("CompositeStorySource needs at least one source")
        self.sources = list(sources)

    def fetch_stories(self, username: str) -> list[Story]:
        stories: list[Story] = []
        failures: list[str] = []

        for source in self.sources:
            name = type(source).__name__
            try:
                stories.extend(source.fetch_stories(username))
            except StorySourceError as exc:
                failures.append(f"{name}: {exc}")
                log.warning("Source %s failed, continuing with the others: %s", name, exc)

        if len(failures) == len(self.sources):
            raise StorySourceError("every story source failed — " + "; ".join(failures))

        stories.sort(key=lambda story: story.posted_at)
        return stories
