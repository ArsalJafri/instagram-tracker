"""Relevance, expressed in the terms the notifier consumes.

The signal lists and the entry-level/software rules that used to live here were replaced
on 2026-08-14 by two-axis scoring in `classification.py`. `classify` remains as the seam
the pipeline and the accumulated regression tests speak to; it delegates, so there is one
implementation of the rules rather than two that can drift apart.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .classification import classify_job, to_job
from .jobs import JobDetails
from .models import Job

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


def classify(details: JobDetails, url: str) -> Job:
    """Evaluate a fetched posting and project the result onto the notifier's model."""
    return to_job(classify_job(details, url), details, url)
