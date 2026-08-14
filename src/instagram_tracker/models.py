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


class RoleType(str, Enum):
    """Which kind of relevant role this is; decides the Discord channel."""

    NEW_GRAD = "new_grad"
    INTERNSHIP = "internship"


class RoleClass(str, Enum):
    """What kind of work this is. Independent of how the role is employed."""

    SOFTWARE = "software"
    DATA = "data"
    QUANT = "quant"
    IT = "it"
    PRODUCT = "product"
    OTHER = "other"


class EmploymentClass(str, Enum):
    """How the role is employed. Independent of what kind of work it is."""

    INTERN = "intern"
    FULL_TIME = "full_time"
    CONTRACT = "contract"
    UNKNOWN = "unknown"


class Destination(str, Enum):
    """The Discord channel a classification routes to."""

    INTERNSHIP = "internship"
    FULL_TIME = "new_grad"
    REVIEW = "review"


class ClassificationSource(str, Enum):
    """What decided the classification, for debugging a wrong answer later."""

    RULE = "rule"
    SCORER = "scorer"


class InputQuality(str, Enum):
    """How much text the fetcher recovered. Gates how far a score is trusted."""

    RICH = "rich"
    POOR = "poor"


@dataclass(frozen=True)
class ClassificationResult:
    """The two-axis verdict. The rest of the application consumes only this."""

    role: RoleClass
    role_confidence: float
    employment: EmploymentClass
    employment_confidence: float
    destination: Destination
    source: ClassificationSource
    classifier_version: str
    input_quality: InputQuality
    # How the posting was read — json-ld, rendered, metadata, slug or none. Kept beside
    # the quality bucket because "it failed on a URL slug" and "it failed on a full
    # description" are different failures worth telling apart later.
    fetch_source: str = ""
    # Which signals fired, most significant first. Recorded so a wrong answer can be
    # explained without re-fetching the posting.
    evidence: list[str] = field(default_factory=list)
    # Set when a high-confidence rule short-circuited the scorer.
    rule: str | None = None


@dataclass(frozen=True)
class Job:
    title: str | None
    company: str | None
    location: str | None
    classification: Classification
    url: str
    reason: str = ""
    role_type: RoleType = RoleType.NEW_GRAD
    # Matched one relevance rule but not the other — a real early-career posting that is
    # not software, or a software posting with no level stated. Worth eyeballing, not
    # worth alerting on, so these go to the review channel.
    near_miss: bool = False
