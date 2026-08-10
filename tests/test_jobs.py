from __future__ import annotations

from pathlib import Path

import pytest

from instagram_tracker.jobs import parse_html, slug_details, slug_title

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_json_ld_gives_title_company_and_location():
    details = parse_html(read("jobposting_jsonld.html"), "https://example.com/jobs/1")

    assert details.source == "json-ld"
    assert details.title == "Software Engineer, New Grad"
    assert details.company == "Acme Corp"
    assert details.location == "Seattle, WA, US"
    assert details.employment_type == "FULL_TIME"
    assert "new graduates" in details.text
    assert "<b>" not in details.text


def test_metadata_fallback_when_no_json_ld():
    details = parse_html(read("jobposting_metadata.html"), "https://example.com/jobs/2")

    assert details.source == "metadata"
    assert details.title == "Senior Software Engineer"
    assert details.company == "Acme Corp"
    assert "payments platform" in details.text


def test_spa_shell_yields_nothing_parseable():
    details = parse_html(read("spa_shell.html"), "https://example.com/jobs/3")
    assert details.title is None
    assert details.source == "none"


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://acme.wd1.myworkdayjobs.com/en-US/careers/job/New-York/"
            "Software-Engineer--New-Grad_R-12345",
            "Software Engineer New Grad",
        ),
        ("https://jobs.lever.co/acme/entry-level-software-engineer", "entry level software engineer"),
        ("https://example.com/", None),
    ],
)
def test_slug_title(url, expected):
    assert slug_title(url) == expected


def test_slug_details_includes_host_in_text():
    details = slug_details("https://jobs.lever.co/acme/new-grad-software-engineer")
    assert details.source == "slug"
    assert "jobs.lever.co" in details.text
