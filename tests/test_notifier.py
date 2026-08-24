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

    assert notifier.notify(JOB, "zero2sudo").sent is True
    assert session.calls[0][0] == "https://discord.test/webhook"


def test_notify_without_a_webhook_is_a_no_op():
    session = FakeSession()
    assert DiscordNotifier("", session=session).notify(JOB, "zero2sudo").sent is False
    assert session.calls == []


def test_notify_reports_failure_without_raising():
    session = FakeSession(exc=requests.ConnectionError("boom"))
    notifier = DiscordNotifier("https://discord.test/webhook", session=session)
    assert notifier.notify(JOB, "zero2sudo").sent is False


WEBHOOK = "https://discord.com/api/webhooks/1536886605732904991/LotWezffhas1o3Fs-t92lhPPWcjbeznth"
SECRET = "LotWezffhas1o3Fs-t92lhPPWcjbeznth"


class RateLimitedResponse:
    """Discord's real 429 shape: a JSON body with retry_after and a global flag."""

    status_code = 429
    headers = {"Retry-After": "1"}

    def __init__(self, retry_after: float = 0.4, is_global: bool = False):
        self._body = {
            "message": "You are being rate limited.",
            "retry_after": retry_after,
            "global": is_global,
        }

    def json(self):
        return self._body

    def raise_for_status(self):
        # requests puts the full request URL — webhook token and all — in this message.
        raise requests.HTTPError(
            f"429 Client Error: Too Many Requests for url: {WEBHOOK}", response=self
        )


class RateLimitedSession:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or RateLimitedResponse()

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return self.response


def test_a_failed_send_never_logs_the_webhook_url(caplog):
    """A webhook URL is a credential; raise_for_status embeds it in the message."""
    notifier = DiscordNotifier(WEBHOOK, session=RateLimitedSession(), sleep=lambda _: None)

    with caplog.at_level("WARNING"):
        assert notifier.notify(JOB, "zero2sudo").sent is False

    assert SECRET not in caplog.text
    assert WEBHOOK not in caplog.text
    assert "new_grad" in caplog.text


def test_a_rate_limit_is_described_by_status_delay_and_scope(caplog):
    session = RateLimitedSession(RateLimitedResponse(retry_after=0.4, is_global=True))
    notifier = DiscordNotifier(WEBHOOK, session=session, sleep=lambda _: None)

    with caplog.at_level("ERROR"):
        notifier.notify(JOB, "zero2sudo")

    assert "HTTP 429" in caplog.text
    assert "retry_after=0.4s" in caplog.text
    # The one flag that separates our own burst from a shared-IP limit.
    assert "global=True" in caplog.text


def test_a_connection_error_is_described_without_a_response():
    from instagram_tracker.notifier import describe_failure

    assert describe_failure(requests.ConnectionError("boom")) == "ConnectionError"


class FlakySession:
    """Rate limited for the first `failures` attempts, then accepts the send."""

    def __init__(self, failures: int, response=None):
        self.calls = []
        self.failures = failures
        self.response = response or RateLimitedResponse()

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if len(self.calls) <= self.failures:
            return self.response
        return FakeResponse()


class RejectedResponse:
    """A webhook that no longer exists — retrying cannot help."""

    status_code = 401
    headers: dict = {}

    def json(self):
        return {"message": "401: Unauthorized", "code": 0}

    def raise_for_status(self):
        raise requests.HTTPError("401 Client Error for url: " + WEBHOOK, response=self)


def test_a_rate_limited_send_is_retried_and_succeeds():
    session = FlakySession(failures=1)
    slept = []
    notifier = DiscordNotifier(WEBHOOK, session=session, sleep=slept.append)

    assert notifier.notify(JOB, "zero2sudo").sent is True
    assert len(session.calls) == 2
    assert slept == [0.4]  # Discord's own retry_after, not a guess


def test_retries_are_bounded():
    session = FlakySession(failures=99)
    notifier = DiscordNotifier(WEBHOOK, session=session, sleep=lambda _: None)

    assert notifier.notify(JOB, "zero2sudo").sent is False
    assert len(session.calls) == 3  # MAX_SEND_ATTEMPTS, not forever


def test_a_long_retry_after_is_not_waited_out():
    """A long delay means the limit is not ours; blocking the poller costs more."""
    session = FlakySession(failures=99, response=RateLimitedResponse(retry_after=90.0))
    slept = []
    notifier = DiscordNotifier(WEBHOOK, session=session, sleep=slept.append)

    assert notifier.notify(JOB, "zero2sudo").sent is False
    assert len(session.calls) == 1
    assert slept == []


def test_an_unauthorised_webhook_is_not_retried():
    session = FlakySession(failures=99, response=RejectedResponse())
    notifier = DiscordNotifier(WEBHOOK, session=session, sleep=lambda _: None)

    assert notifier.notify(JOB, "zero2sudo").sent is False
    assert len(session.calls) == 1
