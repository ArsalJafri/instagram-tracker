"""Domain models shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


@dataclass(frozen=True)
class Link:
    """A link found on a Story, before and after redirect unwrapping."""

    original_url: str
    canonical_url: str


@dataclass(frozen=True)
class Story:
    story_id: str
    username: str
    posted_at: datetime
    links: list[Link] = field(default_factory=list)


class Classification(str, Enum):
    """Outcome of evaluating a job against the spec's relevance rules."""

    RELEVANT = "relevant"
    NOT_RELEVANT = "not_relevant"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Job:
    title: str | None
    company: str | None
    location: str | None
    classification: Classification
    url: str
    reason: str = ""
