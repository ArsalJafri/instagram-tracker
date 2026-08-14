"""Regressions from the 2026-08-14 review-channel pile-up.

Five postings arrived in one morning and every one of them reached the review channel as
a near miss instead of its role channel. Each case here is one of those postings.
"""

from __future__ import annotations

import pytest

from instagram_tracker.classifier import classify
from instagram_tracker.jobs import JobDetails
from instagram_tracker.models import Classification, RoleType


def details(title, text="", employment_type=None, source="json-ld"):
    return JobDetails(
        title=title,
        company=None,
        location=None,
        employment_type=employment_type,
        text=text or (title or ""),
        source=source,
    )


# -- level spellings -----------------------------------------------------


def test_new_college_graduate_satisfies_the_level_rule():
    # NVIDIA's spelling. The field rule already matched on "Software Engineer", so the
    # only thing standing between this and a new-grad ping was the level phrasing.
    job = classify(
        details("Software Engineer, Deep Learning Libraries - New College Graduate 2026"),
        "https://nvidia.eightfold.ai/careers/job/893397020477",
    )

    assert job.classification is Classification.RELEVANT
    assert job.role_type is RoleType.NEW_GRAD
    assert job.near_miss is False


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer, New Grad",
        "New Graduate Software Engineer",
        "Software Engineer - New College Graduate 2026",
    ],
)
def test_every_new_grad_spelling_reaches_the_new_grad_channel(title):
    job = classify(details(title), "https://x/1")

    assert job.classification is Classification.RELEVANT
    assert job.role_type is RoleType.NEW_GRAD


# -- field spellings -----------------------------------------------------


def test_fullstack_as_one_word_satisfies_the_field_rule():
    # Composio's spelling. "full stack engineer" is a literal, so the space mattered.
    job = classify(
        details("Fullstack Engineering Internship, Product Team (Fall 2026 & Winter 2027)"),
        "https://jobs.ashbyhq.com/composio/eea3c0be",
    )

    assert job.classification is Classification.RELEVANT
    assert job.role_type is RoleType.INTERNSHIP
    assert job.near_miss is False


# -- technical roles not titled "software" -------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Forward Deployed Engineering Internship (Fall 2026 & Winter 2027)",
        "Systems Engineering Intern (New York) - Summer 2027",
        "Platform Engineer Intern - Summer 2027",
        "Site Reliability Engineering Intern",
        "DevOps Engineer Intern",
    ],
)
def test_technical_titles_without_the_word_software_qualify(title):
    job = classify(details(title), "https://x/1")

    assert job.classification is Classification.RELEVANT
    assert job.role_type is RoleType.INTERNSHIP


# -- deliberately still rejected -----------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        # Automation engineering is as often manufacturing or QA as it is software.
        "Automation Engineer Intern, (Nationwide) - Summer 2027",
        # Consulting rather than engineering; "security engineer" would not match it.
        "Intern Security Consultant 2027",
    ],
)
def test_the_two_excluded_postings_still_reach_review(title):
    job = classify(details(title), "https://x/1")

    assert job.classification is Classification.NOT_RELEVANT
    assert job.near_miss is True


def test_widening_the_field_list_does_not_admit_unrelated_engineering():
    # The risk the widening takes on: these share the "<discipline> engineer" shape but
    # are not software. They must still fail the field rule.
    for title in (
        "Mechanical Engineer Intern",
        "Civil Engineering Intern - Summer 2027",
        "Chemical Engineer, New Grad",
    ):
        assert classify(details(title), "https://x/1").classification is (
            Classification.NOT_RELEVANT
        )
