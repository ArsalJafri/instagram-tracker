"""Regressions from the 2026-08-11 accuracy audit of 19 rejected postings.

Each case here is a real posting the tracker got wrong, not a hypothetical.
"""

from __future__ import annotations

import pytest

from instagram_tracker.classifier import classify
from instagram_tracker.jobs import JobDetails, is_uninformative, parse_html, slug_details
from instagram_tracker.models import Classification


def details(title, text="", employment_type=None, source="json-ld"):
    return JobDetails(
        title=title,
        company=None,
        location=None,
        employment_type=employment_type,
        text=text or (title or ""),
        source=source,
    )


# -- numbered junior titles ----------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer I/II",  # the real Microsoft posting that was missed
        "Software Engineer I",
        "Software Engineer II",
        "Software Developer I",
        "Software Engineer 1",
        "Software Developer 2",
    ],
)
def test_numbered_junior_titles_are_entry_level(title):
    assert classify(details(title), "https://x/1").classification is Classification.RELEVANT


def test_a_bare_numeral_does_not_imply_entry_level():
    # The signals are scoped to follow a role word; "i" alone must never match.
    job = classify(details("Principal Architect, Team I"), "https://x/1")
    assert job.classification is Classification.NOT_RELEVANT


def test_seniority_still_beats_a_numbered_title():
    job = classify(details("Senior Software Engineer II"), "https://x/1")
    assert job.classification is Classification.NOT_RELEVANT
    assert "not entry-level" in job.reason


# -- schema.org employmentType -------------------------------------------


@pytest.mark.parametrize("employment_type", ["CONTRACTOR", "PART_TIME", "TEMPORARY"])
def test_schema_org_employment_types_reject(employment_type):
    # These are schema.org's real vocabulary. "contract" never matched "CONTRACTOR",
    # so an employer's own declaration was being ignored.
    job = classify(
        details("Software Engineer, New Grad", employment_type=employment_type),
        "https://x/1",
    )
    assert job.classification is Classification.NOT_RELEVANT
    assert "not full-time" in job.reason


def test_full_time_metadata_still_qualifies():
    job = classify(
        details("Software Engineer, New Grad", employment_type="FULL_TIME"),
        "https://x/1",
    )
    assert job.classification is Classification.RELEVANT


# -- uninformative titles ------------------------------------------------


@pytest.mark.parametrize("title", ["JobDetail", "search", "Yello", "Home", "  ", "Careers"])
def test_page_chrome_is_recognised_as_uninformative(title):
    assert is_uninformative(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer I/II",
        "Software Engineer, Early Career, Campus",
        "Next-Gen Networking",
        "The Development Programme",
    ],
)
def test_real_titles_are_kept(title):
    assert is_uninformative(title) is False


def test_a_client_rendered_shell_yields_unknown_not_a_rejection():
    # Previously "search" became the title and the posting was confidently rejected.
    # A rejection asserts the page was read; unknown admits it was not.
    html = "<html><head><title>search</title></head><body></body></html>"
    parsed = parse_html(html, "https://tesla.com/careers/search/job/279763")
    assert parsed.title is None

    resolved = slug_details("https://tesla.com/careers/search/job/279763")
    assert resolved.title is None
    assert classify(resolved, "https://tesla.com/careers/search/job/279763").classification is (
        Classification.UNKNOWN
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.ibm.com/en_US/careers/JobDetail",
        "https://boeing.recsolu.com/app/collect/event/jVEawc55UjQVvfdI1Lx3Ww",
    ],
)
def test_opaque_urls_resolve_to_unknown(url):
    assert classify(slug_details(url), url).classification is Classification.UNKNOWN


def test_a_readable_slug_still_produces_a_title():
    url = (
        "https://google.com/about/careers/applications/jobs/results/"
        "78703249065943750-software-engineer-early-career-campus"
    )
    assert classify(slug_details(url), url).classification is Classification.RELEVANT


# -- near-miss detection -------------------------------------------------


def test_early_career_roles_that_are_not_software_are_near_misses():
    for title in [
        "business analyst intern summer",
        "Product Manager Intern (Signal and Identity Product) - 2027 Summer",
        "analyst early internship program summer",
    ]:
        assert classify(details(title), "https://x/1").near_miss is True, title


def test_software_roles_with_no_stated_level_are_near_misses():
    assert classify(details("Software Engineering - Commerce"), "https://x/1").near_miss is True


def test_newsletters_and_events_are_not_near_misses():
    for title in [
        "Home | Direct Consideration Newsletter",
        "ETCH - Inside Micron & the Semiconductor Industry",
        "Future of Asset Management Virtual Series",
        "2027 Quantitative Prediction Markets Research Summer Analyst",
    ]:
        assert classify(details(title), "https://x/1").near_miss is False, title


def test_explicit_seniority_is_a_clean_reject_not_a_near_miss():
    # "Senior Software Engineer" has a software signal and no level signal, so the bare
    # rule would flag it. An explicit seniority word settles the question instead.
    assert classify(details("Senior Software Engineer"), "https://x/1").near_miss is False
    assert classify(details("Engineering Manager"), "https://x/1").near_miss is False


def test_a_relevant_job_is_never_a_near_miss():
    assert classify(details("Software Engineer, New Grad"), "https://x/1").near_miss is False
