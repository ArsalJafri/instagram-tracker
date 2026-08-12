"""Relevance rules.

A role qualifies when it is software-related and is either entry-level/new-grad or an
internship. Internships were originally rejected outright; they are now a first-class
outcome, tagged with a `RoleType` so they can be routed to their own Discord channel.

Employment type is rarely stated in extractable metadata, so full-time is treated as
satisfied unless something negates it (contract, part-time). Seniority negatives such as
"senior" or "manager" disqualify on the entry-level rule instead.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .jobs import JobDetails
from .models import Classification, Job, RoleType

# The spec's signal lists, split by the rule each one decides.
# Entry-level / new-grad signals.
LEVEL_SIGNALS = [
    "new grad",
    "new graduate",
    "entry level",
    "entry-level",
    "early career",
    "associate",
    # Numbered junior titles, added 2026-08-11 after "Software Engineer I/II" was found
    # rejected in the accuracy audit. Deliberately scoped to follow a role word: a bare
    # "i" or "1" would match almost anything.
    "engineer i",
    "engineer ii",
    "engineer 1",
    "engineer 2",
    "developer i",
    "developer ii",
    "developer 1",
    "developer 2",
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

# Internship signals. These satisfy the level rule on their own — an internship is
# entry-level by definition and rarely also says "new grad".
INTERNSHIP_SIGNALS = [
    "intern",
    "internship",
    "co-op",
    "coop",
]

# Seniority negatives — these defeat the entry-level rule.
SENIORITY_NEGATIVES = [
    "senior",
    "staff",
    "principal",
    "manager",
    "director",
]

# Employment-type negatives — these defeat the full-time rule. "internship" and "intern"
# used to live here and now qualify instead.
#
# The second group is schema.org's own `employmentType` vocabulary. Its spellings differ
# from prose: sites publish CONTRACTOR and PART_TIME, which the first group never matched
# ("or" is not an allowed inflection, and a hyphen is not an underscore). Without these,
# an employer's explicit declaration of a contract role was read as no declaration.
EMPLOYMENT_NEGATIVES = [
    "contract",
    "part-time",
    "contractor",
    "part_time",
    "temporary",
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


# A bounded set of inflections, so "software engineer" matches "software engineering"
# and "intern" matches "interns" and "internship". Deliberately not open-ended: allowing
# any trailing letters would make "intern" match "internal" and "international", turning
# an Internal Tools role into an internship alert.
INFLECTIONS = r"(?:s|es|ing|ed|ship)?"


def _matches(haystack: str, phrase: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(phrase)}{INFLECTIONS}(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def _found(haystack: str, phrases: list[str]) -> list[str]:
    return [phrase for phrase in phrases if _matches(haystack, phrase)]


def classify(details: JobDetails, url: str) -> Job:
    """Evaluate a fetched posting against the relevance rules."""
    if not details.title:
        # Nothing was recoverable from the page or the URL, so there is no basis to
        # judge the role either way. Recorded as unknown rather than rejected.
        return Job(
            title=None,
            company=details.company,
            location=details.location,
            classification=Classification.UNKNOWN,
            url=url,
            reason="no title could be extracted from the page or URL",
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
    # Read from the title only, for the same reason negatives are: descriptions routinely
    # mention unrelated internship programmes, which would mislabel full-time roles.
    internship_hits = _found(title, INTERNSHIP_SIGNALS) or _found(
        employment, INTERNSHIP_SIGNALS
    )

    field_hits = _found(body, FIELD_SIGNALS)
    level_hits = _found(body, LEVEL_SIGNALS)

    # Every signal is computed before any rule fires, so a rejection can still report
    # what *did* match. A posting satisfying exactly one of the two rules is a near
    # miss: a genuine early-career role that is not software, or a software role with no
    # level stated. Matching neither is a newsletter or an event, and stays silent.
    has_level = bool(level_hits or internship_hits)
    has_field = bool(field_hits)
    near_miss = has_level != has_field

    # A seniority word settles it only when nothing else claims the role is junior.
    # "Senior Software Engineer" is a clean reject and not worth eyeballing. But
    # "Product Manager Intern" trips `manager` while plainly being an internship, so the
    # seniority hit there is a false positive from a role name, not a statement of level.
    if seniority_hits and not has_level:
        near_miss = False

    if employment_hits:
        return _reject(details, url, f"not full-time: {', '.join(employment_hits)}", near_miss)
    if seniority_hits:
        return _reject(details, url, f"not entry-level: {', '.join(seniority_hits)}", near_miss)
    if not has_field:
        return _reject(details, url, "no software or computer-science signal", near_miss)
    if not has_level:
        return _reject(details, url, "no entry-level, new-grad or internship signal", near_miss)

    if internship_hits:
        role_type = RoleType.INTERNSHIP
        qualifier = f"internship ({', '.join(internship_hits)})"
    else:
        role_type = RoleType.NEW_GRAD
        qualifier = f"entry-level ({', '.join(level_hits)})"

    reason = f"{qualifier} + software ({', '.join(field_hits)})"
    if details.source == "slug":
        reason += "; matched from URL slug only"
    return Job(
        title=details.title,
        company=details.company,
        location=details.location,
        classification=Classification.RELEVANT,
        url=url,
        reason=reason,
        role_type=role_type,
    )


def _reject(details: JobDetails, url: str, reason: str, near_miss: bool = False) -> Job:
    return Job(
        title=details.title,
        company=details.company,
        location=details.location,
        classification=Classification.NOT_RELEVANT,
        url=url,
        reason=reason,
        near_miss=near_miss,
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-")).lower()
