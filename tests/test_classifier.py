from __future__ import annotations

import pytest

from instagram_tracker.classifier import classify, is_known_ats
from instagram_tracker.jobs import JobDetails, slug_details
from instagram_tracker.models import Classification

URL = "https://boards.greenhouse.io/acme/jobs/1"


def details(title, text="", employment_type=None, source="json-ld"):
    return JobDetails(
        title=title,
        company="Acme",
        location="Seattle, WA",
        employment_type=employment_type,
        text=text or title or "",
        source=source,
    )


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer, New Grad",
        "Entry-Level Backend Engineer",
        "Associate Software Developer",
        "Early Career Machine Learning Engineer",
        "New Graduate Full Stack Engineer",
    ],
)
def test_relevant_roles(title):
    assert classify(details(title), URL).classification is Classification.RELEVANT


@pytest.mark.parametrize(
    "title",
    [
        "Senior Software Engineer",           # seniority
        "Staff Software Engineer, New Grad",  # seniority beats the level signal
        "Engineering Manager",
        "Software Engineering Intern",        # not full-time
        "New Grad Software Engineer (Contract)",
        "Part-Time Software Developer, Entry Level",
        "Marketing Associate",                # no software signal
        "Software Engineer",                  # no entry-level signal
    ],
)
def test_not_relevant_roles(title):
    assert classify(details(title), URL).classification is Classification.NOT_RELEVANT


def test_full_time_is_assumed_when_nothing_negates_it():
    job = classify(details("New Grad Software Engineer", employment_type=None), URL)
    assert job.classification is Classification.RELEVANT


def test_employment_type_metadata_can_reject():
    job = classify(
        details("New Grad Software Engineer", employment_type="INTERN"),
        URL,
    )
    assert job.classification is Classification.NOT_RELEVANT
    assert "not full-time" in job.reason


def test_negatives_in_the_description_do_not_reject():
    job = classify(
        details(
            "Software Engineer, New Grad",
            text="Software Engineer, New Grad. We also run a summer internship programme.",
        ),
        URL,
    )
    assert job.classification is Classification.RELEVANT


def test_unparseable_posting_is_unknown():
    job = classify(JobDetails(None, None, None, None, "", "none"), URL)
    assert job.classification is Classification.UNKNOWN


def test_unfetchable_page_with_an_id_only_slug_is_unknown_not_rejected():
    """The live sonypicturesjobs link: no page, and a slug that is all identifiers."""
    url = "https://sonypicturesjobs.com/job/-/-/22978/98897894576"
    job = classify(slug_details(url), url)
    assert job.classification is Classification.UNKNOWN


def test_slug_only_match_is_flagged_in_the_reason():
    job = classify(details("New Grad Software Engineer", source="slug"), URL)
    assert job.classification is Classification.RELEVANT
    assert "URL slug" in job.reason


def test_job_carries_details_through():
    job = classify(details("Software Engineer, New Grad"), URL)
    assert (job.title, job.company, job.location, job.url) == (
        "Software Engineer, New Grad",
        "Acme",
        "Seattle, WA",
        URL,
    )


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://boards.greenhouse.io/acme/jobs/1", True),
        ("https://jobs.lever.co/acme/1", True),
        ("https://acme.wd1.myworkdayjobs.com/careers/job/1", True),
        ("https://www.sonypicturesjobs.com/job/1", False),
        ("https://notgreenhouse.io.evil.com/job/1", False),
    ],
)
def test_known_ats_detection(url, expected):
    assert is_known_ats(url) is expected
