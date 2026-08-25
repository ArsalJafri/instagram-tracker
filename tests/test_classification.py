"""Two-axis classification: role and employment scored independently."""

from __future__ import annotations

import pytest

from instagram_tracker.classification import (
    CLASSIFIER_VERSION,
    classify_job,
    normalize,
)
from instagram_tracker.jobs import JobDetails
from instagram_tracker.models import (
    ClassificationSource,
    Destination,
    EmploymentClass,
    InputQuality,
    RoleClass,
)


def details(title, text="", employment_type=None, source="json-ld"):
    return JobDetails(
        title=title,
        company=None,
        location=None,
        employment_type=employment_type,
        text=text or (title or ""),
        source=source,
    )


# -- the failure that prompted the redesign ------------------------------


def test_word_order_no_longer_decides_the_verdict():
    # SpaceX wrote "Engineer, Software". The phrase list held "software engineer" and so
    # reported "no software signal" about a title containing both words.
    result = classify_job(
        details("Job Application for New Graduate Engineer, Software - '26/'27 Grads"),
        "https://spacex.com/careers/1",
    )

    assert result.role is RoleClass.SOFTWARE
    assert result.employment is EmploymentClass.FULL_TIME
    assert result.destination is Destination.FULL_TIME


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineer",
        "Engineer, Software",
        "Software Development Engineer",
        "Software Developer",
    ],
)
def test_the_same_two_words_score_the_same_in_any_order(title):
    assert classify_job(details(title), "https://x/1").role is RoleClass.SOFTWARE


# -- the axes are independent --------------------------------------------


def test_a_software_role_with_no_level_is_not_confused_with_a_non_software_role():
    # The old exclusive-or could not tell these apart: both were "one rule matched".
    software_no_level = classify_job(details("Software Engineer"), "https://x/1")
    intern_not_software = classify_job(details("Marketing Intern"), "https://x/2")

    assert software_no_level.role is RoleClass.SOFTWARE
    assert software_no_level.employment is EmploymentClass.UNKNOWN

    assert intern_not_software.role is RoleClass.OTHER
    assert intern_not_software.employment is EmploymentClass.INTERN

    # Both still go to review, but now for reasons that can be told apart.
    assert software_no_level.destination is Destination.REVIEW
    assert intern_not_software.destination is Destination.REVIEW


# -- weak signals combining ----------------------------------------------


def test_an_ambiguous_title_is_carried_by_its_description():
    # "Technology Summer Analyst" says nothing on its own. No phrase list could reach it.
    description = (
        "Java Python distributed systems application development Computer Science "
        "currently pursuing a bachelor's degree return to school after the internship "
        "10-week summer program APIs databases Git"
    )
    result = classify_job(details("Technology Summer Analyst", description), "https://x/1")

    assert result.role is RoleClass.SOFTWARE
    assert result.employment is EmploymentClass.INTERN
    assert result.destination is Destination.INTERNSHIP


def test_a_bare_ambiguous_title_stays_in_review():
    # The same title with nothing behind it must not be guessed at.
    result = classify_job(details("Technology Summer Analyst"), "https://x/1")

    assert result.role is RoleClass.OTHER
    assert result.destination is Destination.REVIEW


# -- "engineer" alone means nothing --------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Mechanical Engineer Intern",
        "Civil Engineering Intern - Summer 2027",
        "Chemical Engineer, New Grad",
        "Electrical Engineering Intern",
        "Biomedical Engineer Intern",
    ],
)
def test_other_disciplines_never_reach_a_software_channel(title):
    result = classify_job(details(title), "https://x/1")

    assert result.role is not RoleClass.SOFTWARE
    assert result.destination is Destination.REVIEW


# -- seniority ------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Senior Software Engineer (Full-Time)",
        "Staff Software Engineer, New Grad",
        "Principal Software Engineer - Entry Level",
        "Engineering Manager - Full Time",
    ],
)
def test_seniority_defeats_the_full_time_rule(title):
    assert classify_job(details(title), "https://x/1").destination is Destination.REVIEW


def test_seniority_words_do_not_defeat_an_internship():
    # "Product Manager Intern" trips `manager` while plainly being an internship. It goes
    # to review for being product rather than software, not for the seniority word.
    result = classify_job(details("Product Manager Intern"), "https://x/1")

    assert result.employment is EmploymentClass.INTERN
    assert result.role is RoleClass.PRODUCT


# -- the other role classes ----------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Data Scientist Intern", RoleClass.DATA),
        ("Quantitative Developer Intern", RoleClass.QUANT),
        ("IT Support Intern", RoleClass.IT),
        ("Product Manager Intern", RoleClass.PRODUCT),
        ("Machine Learning Engineer Intern", RoleClass.SOFTWARE),
        ("Data Engineer, New Grad", RoleClass.SOFTWARE),
    ],
)
def test_role_classes(title, expected):
    assert classify_job(details(title), "https://x/1").role is expected


# -- rules ----------------------------------------------------------------


def test_a_structured_employment_type_short_circuits_the_scorer():
    result = classify_job(
        details("Software Engineer", employment_type="INTERN"), "https://x/1"
    )

    assert result.employment is EmploymentClass.INTERN
    assert result.source is ClassificationSource.RULE
    assert result.rule == "structured-employment-intern"
    assert result.destination is Destination.INTERNSHIP


@pytest.mark.parametrize(
    "title",
    [
        # Both observed in the new-grad channel on 2026-08-25, from employers whose
        # JSON-LD declares FULL_TIME. The second has no space before the hyphen, which
        # is why normalisation is part of what this covers.
        "AI Software Engineering Intern - Edge",
        "Single-Family Software Developer Intern- Summer 2027",
        "Software Engineer Co-op",
    ],
)
def test_an_intern_title_beats_a_full_time_declaration(title):
    """A summer internship is full-time hours, so ATSs declare FULL_TIME truthfully.

    Believing the field over the title sent every such internship to the new-grad
    channel for eleven days without failing a test.
    """
    result = classify_job(details(title, employment_type="FULL_TIME"), "https://x/1")

    assert result.employment is EmploymentClass.INTERN
    assert result.destination is Destination.INTERNSHIP
    assert result.source is ClassificationSource.SCORER
    assert result.rule is None
    assert "veto:intern-title-over-full-time" in result.evidence


def test_a_declared_intern_is_still_trusted_outright():
    """The veto is aimed at FULL_TIME only; an explicit INTERN keeps short-circuiting."""
    result = classify_job(
        details("Software Engineer Intern", employment_type="INTERN"), "https://x/1"
    )

    assert result.source is ClassificationSource.RULE
    assert result.rule == "structured-employment-intern"
    assert result.destination is Destination.INTERNSHIP


def test_a_full_time_declaration_still_wins_without_an_intern_title():
    """Nothing changes for the case the rule was written for."""
    result = classify_job(
        details("Software Engineer", employment_type="FULL_TIME"), "https://x/1"
    )

    assert result.employment is EmploymentClass.FULL_TIME
    assert result.source is ClassificationSource.RULE
    assert result.rule == "structured-employment-full-time"


def test_a_description_only_intern_signal_does_not_veto():
    """Corroboration in the body is not the employer naming the role in the title."""
    result = classify_job(
        details(
            "Software Engineer",
            text="You must be currently enrolled in an undergraduate programme.",
            employment_type="FULL_TIME",
        ),
        "https://x/1",
    )

    assert result.source is ClassificationSource.RULE
    assert result.rule == "structured-employment-full-time"


def test_schema_org_underscore_spellings_are_understood():
    result = classify_job(
        details("Software Engineer", employment_type="PART_TIME"), "https://x/1"
    )

    assert result.employment is EmploymentClass.CONTRACT
    assert result.destination is Destination.REVIEW


def test_return_to_school_is_a_rule_not_a_score():
    result = classify_job(
        details("Software Engineer", "you must return to school after the programme"),
        "https://x/1",
    )

    assert result.rule == "return-to-school"
    assert result.destination is Destination.INTERNSHIP


# -- input quality --------------------------------------------------------


def test_poor_input_must_clear_a_higher_bar():
    # Identical text, different provenance. A slug is a few words off a URL path, so the
    # same score is worth less and the borderline case falls back to review.
    text = "technology analyst java python"
    rich = classify_job(details("Technology Analyst", text, source="json-ld"), "https://x/1")
    poor = classify_job(details("Technology Analyst", text, source="slug"), "https://x/1")

    assert rich.input_quality is InputQuality.RICH
    assert poor.input_quality is InputQuality.POOR
    assert poor.role_confidence == rich.role_confidence
    # Same score, stricter gate.
    assert poor.destination is Destination.REVIEW


@pytest.mark.parametrize("source", ["json-ld", "rendered"])
def test_rich_sources(source):
    assert classify_job(details("Software Engineer", source=source), "https://x/1").input_quality is (
        InputQuality.RICH
    )


@pytest.mark.parametrize("source", ["metadata", "slug", "none"])
def test_poor_sources(source):
    assert classify_job(details("Software Engineer", source=source), "https://x/1").input_quality is (
        InputQuality.POOR
    )


# -- unreadable postings --------------------------------------------------


def test_a_posting_without_a_title_is_never_scored():
    # Nothing was read, so there is nothing to judge. Manufacturing a verdict here is how
    # a confident wrong answer replaces an honest unknown.
    result = classify_job(details(None, "", source="none"), "https://x/1")

    assert result.role is RoleClass.OTHER
    assert result.role_confidence == 0.0
    assert result.employment is EmploymentClass.UNKNOWN
    assert result.destination is Destination.REVIEW


# -- confidence -----------------------------------------------------------


def test_a_two_way_tie_is_not_confident():
    tie = classify_job(details("Data Analyst and Help Desk Technician"), "https://x/1")

    assert tie.role_confidence < 0.6
    assert tie.destination is Destination.REVIEW


def test_confidence_stays_within_bounds():
    for title in ("Software Engineer Intern", "Mechanical Engineer", "", "Analyst"):
        result = classify_job(details(title or None), "https://x/1")
        assert 0.0 <= result.role_confidence <= 1.0
        assert 0.0 <= result.employment_confidence <= 1.0


# -- explainability -------------------------------------------------------


def test_every_result_carries_its_version_and_evidence():
    result = classify_job(details("Software Engineer Intern"), "https://x/1")

    assert result.classifier_version == CLASSIFIER_VERSION
    assert "software:software+builds" in result.evidence
    assert "intern:intern" in result.evidence


# -- normalization --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Engineer, Software", "engineer software"),
        ("Full-Time", "full time"),
        ("FULL_TIME", "full time"),
        ("Co-Op", "co op"),
        ("Software   Engineer", "software engineer"),
        ("Engineer – Software", "engineer software"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_normalization_keeps_multi_word_signals_intact():
    # "return to school" must survive as a phrase; flattening must not shred it.
    assert "return to school" in normalize("You must Return-To-School afterwards")
