from __future__ import annotations

import requests

from instagram_tracker.models import Classification, Job
from instagram_tracker.notifier import DiscordNotifier, build_payload

JOB = Job(
    title="Software Engineer, New Grad",
    company="Acme Corp",
    location="Seattle, WA",
    classification=Classification.RELEVANT,
    url="https://boards.greenhouse.io/acme/jobs/1",
    reason="entry-level + software",
)


class FakeSession:
    def __init__(self, exc: Exception | None = None):
        self.calls = []
        self.exc = exc

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if self.exc:
            raise self.exc
        return FakeResponse()


class FakeResponse:
    def raise_for_status(self):
        return None


def test_payload_shape():
    payload = build_payload(JOB, "zero2sudo")
    embed = payload["embeds"][0]

    assert embed["title"] == "Software Engineer, New Grad"
    assert embed["url"] == JOB.url
    assert "zero2sudo" in embed["footer"]["text"]

    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Company"] == "Acme Corp"
    assert fields["Location"] == "Seattle, WA"
    assert fields["Source"] == "Known ATS"


def test_payload_omits_missing_fields_and_non_ats_source():
    job = Job(None, None, None, Classification.RELEVANT, "https://example.com/jobs/1")
    embed = build_payload(job, "zero2sudo")["embeds"][0]

    assert embed["title"] == "Job posting"
    assert "fields" not in embed


def test_notify_posts_to_the_webhook():
    session = FakeSession()
    notifier = DiscordNotifier("https://discord.test/webhook", session=session)

    assert notifier.notify(JOB, "zero2sudo") is True
    assert session.calls[0][0] == "https://discord.test/webhook"


def test_notify_without_a_webhook_is_a_no_op():
    session = FakeSession()
    assert DiscordNotifier("", session=session).notify(JOB, "zero2sudo") is False
    assert session.calls == []


def test_notify_reports_failure_without_raising():
    session = FakeSession(exc=requests.ConnectionError("boom"))
    notifier = DiscordNotifier("https://discord.test/webhook", session=session)
    assert notifier.notify(JOB, "zero2sudo") is False
