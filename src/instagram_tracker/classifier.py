"""Relevance rules.

A role qualifies only when all three hold:

* full-time
* entry-level or new-grad
* software or computer-science related

Employment type is rarely stated in extractable metadata, so full-time is treated as
satisfied unless something negates it (internship, contract, part-time). Seniority
negatives such as "senior" or "manager" disqualify on the entry-level rule instead.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .jobs import JobDetails
from .models import Classification, Job

# The spec's signal lists, split by the rule each one decides.
# Entry-level / new-grad signals.
LEVEL_SIGNALS = [
    "new grad",
    "new graduate",
    "entry level",
    "entry-level",
    "early career",
    "associate",
]

# Software / computer-science signals.
FIELD_SIGNALS = [
    "software engineer",
    "software developer",
    "backend engineer",
    "frontend engineer",
    "full stack engineer",
    "data engineer",
    "machine learning engineer",
]

# Seniority negatives — these defeat the entry-level rule.
SENIORITY_NEGATIVES = [
    "senior",
    "staff",
    "principal",
    "manager",
    "director",
]

# Employment-type negatives — these defeat the full-time rule.
EMPLOYMENT_NEGATIVES = [
    "internship",
    "intern",
    "contract",
    "part-time",
]

KNOWN_ATS_DOMAINS = [
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "workdayjobs.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
]


def is_known_ats(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == domain or host.endswith("." + domain) for domain in KNOWN_ATS_DOMAINS)


def _matches(haystack: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", haystack) is not None


def _found(haystack: str, phrases: list[str]) -> list[str]:
    return [phrase for phrase in phrases if _matches(haystack, phrase)]


def classify(details: JobDetails, url: str) -> Job:
    """Evaluate a fetched posting against the relevance rules."""
    if not details.title and not details.text.strip():
        return Job(
            title=None,
            company=None,
            location=None,
            classification=Classification.UNKNOWN,
            url=url,
            reason="no title or description could be extracted",
        )

    # The title carries the signal; the description adds context but also noise
    # ("we also hire interns"), so negatives are only trusted from the title.
    title = _normalize(details.title or "")
    body = _normalize(f"{details.title or ''} {details.text}")
    employment = _normalize(details.employment_type or "")

    seniority_hits = _found(title, SENIORITY_NEGATIVES)
    employment_hits = _found(title, EMPLOYMENT_NEGATIVES) or _found(
        employment, EMPLOYMENT_NEGATIVES
    )

    if employment_hits:
        return _reject(details, url, f"not full-time: {', '.join(employment_hits)}")
    if seniority_hits:
        return _reject(details, url, f"not entry-level: {', '.join(seniority_hits)}")

    field_hits = _found(body, FIELD_SIGNALS)
    if not field_hits:
        return _reject(details, url, "no software or computer-science signal")

    level_hits = _found(body, LEVEL_SIGNALS)
    if not level_hits:
        return _reject(details, url, "no entry-level or new-grad signal")

    reason = f"entry-level ({', '.join(level_hits)}) + software ({', '.join(field_hits)})"
    if details.source == "slug":
        reason += "; matched from URL slug only"
    return Job(
        title=details.title,
        company=details.company,
        location=details.location,
        classification=Classification.RELEVANT,
        url=url,
        reason=reason,
    )


def _reject(details: JobDetails, url: str, reason: str) -> Job:
    return Job(
        title=details.title,
        company=details.company,
        location=details.location,
        classification=Classification.NOT_RELEVANT,
        url=url,
        reason=reason,
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-")).lower()
