from __future__ import annotations

import pytest

from instagram_tracker.classifier import classify
from instagram_tracker.jobs import JobDetails
from instagram_tracker.models import Classification, Job, RoleType
from instagram_tracker.notifier import DiscordNotifier, build_payload

URL = "https://boards.greenhouse.io/acme/jobs/1"


def details(title, text="", employment_type=None, source="jsonld"):
    return JobDetails(
        title=title,
        company="Acme",
        location="Seattle, WA",
        text=text,
        employment_type=employment_type,
        source=source,
    )


# -- classification ------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineering Intern",
        "Software Engineer Intern",
        "Software Engineering Internship",
        "Software Developer Co-op",
        "Backend Engineer Intern, Summer 2027",
    ],
)
def test_internship_titles_are_relevant_and_tagged(title):
    job = classify(details(title), URL)

    assert job.classification is Classification.RELEVANT
    assert job.role_type is RoleType.INTERNSHIP


def test_software_engineering_now_matches_the_field_rule():
    # The strict boundary used to reject this: "software engineer" did not match
    # "software engineering", which silently cost new-grad matches too.
    job = classify(details("Software Engineering, New Grad"), URL)

    assert job.classification is Classification.RELEVANT
    assert job.role_type is RoleType.NEW_GRAD


def test_new_grad_roles_are_still_tagged_new_grad():
    job = classify(details("Software Engineer, Early Career, Campus"), URL)

    assert job.classification is Classification.RELEVANT
    assert job.role_type is RoleType.NEW_GRAD
    assert "employment=full_time" in job.reason


def test_internship_reason_names_the_signal():
    job = classify(details("Software Engineering Intern"), URL)
    assert "intern:intern" in job.reason


# The inflection set is deliberately bounded. Open-ended suffix matching would make
# "intern" match "internal" and "international", mislabelling full-time roles.


def test_international_role_is_new_grad_not_an_internship():
    job = classify(details("International Software Engineer, New Grad"), URL)

    assert job.classification is Classification.RELEVANT
    assert job.role_type is RoleType.NEW_GRAD


def test_internal_does_not_register_as_an_internship_signal():
    # No level signal in this title, so rejection is correct — what matters is *why*.
    # Had "intern" matched "internal", this would have been a relevant internship.
    job = classify(details("Internal Tools Software Engineer"), URL)

    assert job.classification is Classification.NOT_RELEVANT
    assert "employment=unknown" in job.reason


def test_contract_and_part_time_still_reject():
    assert classify(details("Software Engineer (Contract)"), URL).classification is (
        Classification.NOT_RELEVANT
    )
    assert classify(details("Part-Time Software Engineer"), URL).classification is (
        Classification.NOT_RELEVANT
    )


def test_seniority_still_rejects_an_internship_title():
    job = classify(details("Senior Software Engineering Intern"), URL)
    assert job.classification is Classification.NOT_RELEVANT
    assert "disqualified:senior" in job.reason


def test_employment_type_metadata_marks_an_internship():
    job = classify(details("Software Engineer", employment_type="INTERN"), URL)
    assert job.role_type is RoleType.INTERNSHIP


def test_internship_mentioned_only_in_the_description_does_not_retag():
    # Descriptions routinely mention unrelated internship programmes.
    job = classify(
        details("Software Engineer, New Grad", text="We also run a summer internship."),
        URL,
    )
    assert job.role_type is RoleType.NEW_GRAD


def test_a_plain_software_role_with_no_level_signal_is_still_rejected():
    job = classify(details("Software Engineer"), URL)
    assert job.classification is Classification.NOT_RELEVANT
    assert "employment=unknown" in job.reason


# -- routing -------------------------------------------------------------


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return FakeResponse()


class FakeResponse:
    def raise_for_status(self):
        return None


def job(role_type):
    return Job(
        title="Software Engineering Intern",
        company="Acme",
        location="Seattle, WA",
        classification=Classification.RELEVANT,
        url=URL,
        role_type=role_type,
    )


def test_internships_go_to_the_internship_webhook():
    session = FakeSession()
    notifier = DiscordNotifier("https://main.test", "https://intern.test", session=session)

    notifier.notify(job(RoleType.INTERNSHIP), "zero2sudo")
    assert session.calls[0][0] == "https://intern.test"


def test_new_grad_roles_go_to_the_main_webhook():
    session = FakeSession()
    notifier = DiscordNotifier("https://main.test", "https://intern.test", session=session)

    notifier.notify(job(RoleType.NEW_GRAD), "zero2sudo")
    assert session.calls[0][0] == "https://main.test"


def test_internships_fall_back_to_the_main_webhook_when_unconfigured():
    session = FakeSession()
    notifier = DiscordNotifier("https://main.test", "", session=session)

    assert notifier.notify(job(RoleType.INTERNSHIP), "zero2sudo").sent is True
    assert session.calls[0][0] == "https://main.test"


def test_only_an_internship_webhook_still_notifies():
    session = FakeSession()
    notifier = DiscordNotifier("", "https://intern.test", session=session)

    assert notifier.notify(job(RoleType.INTERNSHIP), "zero2sudo").sent is True
    assert session.calls[0][0] == "https://intern.test"


def test_payload_headline_distinguishes_the_two():
    assert build_payload(job(RoleType.INTERNSHIP), "z")["content"] == "New software internship"
    assert build_payload(job(RoleType.NEW_GRAD), "z")["content"] == (
        "New entry-level software role"
    )
