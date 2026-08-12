"""Unreadable postings get surfaced for review instead of vanishing.

Written after a real IBM internship link was missed: careers.ibm.com answers a plain
fetch with HTTP 202 and an empty body, so nothing about the role is recoverable.
"""

from __future__ import annotations

from instagram_tracker.models import Classification, Job, RoleType
from instagram_tracker.notifier import DiscordNotifier, build_payload

URL = "https://careers.ibm.com/en_US/careers/JobDetail?jobId=128497"


def unreadable():
    return Job(
        title=None,
        company=None,
        location=None,
        classification=Classification.UNKNOWN,
        url=URL,
        reason="no title could be extracted from the page or URL",
    )


def relevant(role_type=RoleType.NEW_GRAD):
    return Job(
        title="Software Engineer, New Grad",
        company="Acme",
        location="Seattle",
        classification=Classification.RELEVANT,
        url="https://boards.greenhouse.io/acme/jobs/1",
        role_type=role_type,
    )


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return FakeResponse()


class FakeResponse:
    def raise_for_status(self):
        return None


def notifier(session, unknown="https://review.test"):
    return DiscordNotifier(
        "https://main.test",
        "https://intern.test",
        unknown_webhook_url=unknown,
        mentions="<@&123>",
        internship_mentions="<@&456>",
        session=session,
    )


def test_an_unreadable_posting_goes_to_the_review_channel():
    session = FakeSession()
    assert notifier(session).notify(unreadable(), "zero2sudo") is True
    assert session.calls[0][0] == "https://review.test"


def test_it_never_falls_back_to_a_real_channel():
    # The point of a separate channel is that unreadable links do not mix with matches.
    session = FakeSession()
    assert notifier(session, unknown="").notify(unreadable(), "zero2sudo") is False
    assert session.calls == []


def test_relevant_jobs_still_route_normally():
    session = FakeSession()
    n = notifier(session)

    n.notify(relevant(RoleType.NEW_GRAD), "z")
    n.notify(relevant(RoleType.INTERNSHIP), "z")

    assert [call[0] for call in session.calls] == ["https://main.test", "https://intern.test"]


def test_the_review_alert_is_visually_distinct():
    payload = build_payload(unreadable(), "zero2sudo")
    embed = payload["embeds"][0]

    assert payload["content"] == "Could not read this posting — check it manually"
    assert embed["title"] == "Unreadable job posting"
    assert embed["color"] != build_payload(relevant(), "zero2sudo")["embeds"][0]["color"]


def test_the_review_alert_explains_why():
    embed = build_payload(unreadable(), "zero2sudo")["embeds"][0]
    why = {f["name"]: f["value"] for f in embed["fields"]}["Why"]
    assert "no title" in why


def test_review_alerts_never_ping_anyone():
    # These are for triage, not urgency; a mention on every unreadable link would train
    # you to ignore the ping that matters.
    session = FakeSession()
    notifier(session).notify(unreadable(), "zero2sudo")
    payload = session.calls[0][1]

    assert payload["content"].startswith("Could not read")
    assert payload["allowed_mentions"] == {"parse": []}


# -- near misses ---------------------------------------------------------


def near_miss_job():
    return Job(
        title="business analyst intern summer",
        company=None,
        location=None,
        classification=Classification.NOT_RELEVANT,
        url="https://capitalonecareers.com/job/mclean/business-analyst-intern-summer-2027",
        reason="no software or computer-science signal",
        near_miss=True,
    )


def test_a_near_miss_goes_to_the_review_channel():
    session = FakeSession()
    assert notifier(session).notify(near_miss_job(), "zero2sudo") is True
    assert session.calls[0][0] == "https://review.test"


def test_a_near_miss_is_labelled_and_explained():
    payload = build_payload(near_miss_job(), "zero2sudo")
    embed = payload["embeds"][0]

    assert payload["content"] == "Near match — matched one rule but not the other"
    assert embed["title"] == "business analyst intern summer"
    why = {f["name"]: f["value"] for f in embed["fields"]}["Why"]
    assert "no software" in why


def test_a_near_miss_never_pings_anyone():
    session = FakeSession()
    notifier(session).notify(near_miss_job(), "zero2sudo")
    assert session.calls[0][1]["allowed_mentions"] == {"parse": []}


def test_near_misses_stay_silent_without_a_review_channel():
    session = FakeSession()
    assert notifier(session, unknown="").notify(near_miss_job(), "zero2sudo") is False
    assert session.calls == []
