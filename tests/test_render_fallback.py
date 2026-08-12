"""The rendering-proxy fallback, added after a real internship was missed.

careers.ibm.com answers a plain fetch with an AWS WAF challenge — HTTP 202 and a script
that must run to mint a token. Once rendered it serves an ordinary og:title, so the
fallback recovers postings nothing else can read.
"""

from __future__ import annotations

import requests

from instagram_tracker.classifier import classify
from instagram_tracker.jobs import JobFetcher, parse_rendered
from instagram_tracker.models import Classification, RoleType

IBM_URL = "https://careers.ibm.com/en_US/careers/JobDetail?jobId=128497"

# Shape of a real response, trimmed.
RENDERED = """Title: Software Developer Intern 2027

URL Source: https://careers.ibm.com/en_US/careers/JobDetail?jobId=128497

Markdown Content:
At IBM Software, we transform client challenges into solutions.
"""

WAF_CHALLENGE = (
    '<!DOCTYPE html><html><head><title></title>'
    '<script>window.gokuProps = {"key":"x"};</script></head><body></body></html>'
)


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status
        self.headers = {"content-type": "text/html"}

    def raise_for_status(self):
        return None


class FakeSession:
    """Serves the WAF challenge to a direct fetch and rendered text to the proxy."""

    def __init__(self, rendered=RENDERED, proxy_exc=None):
        self.rendered = rendered
        self.proxy_exc = proxy_exc
        self.urls = []

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        self.urls.append(url)
        if url.startswith("https://r.jina.ai/"):
            if self.proxy_exc:
                raise self.proxy_exc
            return FakeResponse(self.rendered, 200)
        return FakeResponse(WAF_CHALLENGE, 202)


# -- parsing -------------------------------------------------------------


def test_a_rendered_response_yields_the_title():
    details = parse_rendered(RENDERED, IBM_URL)

    assert details.title == "Software Developer Intern 2027"
    assert details.source == "rendered"
    assert "IBM Software" in details.text


def test_a_rendered_response_without_a_title_is_rejected():
    assert parse_rendered("URL Source: https://x\n\nMarkdown Content:\nnothing", IBM_URL) is None


def test_a_useless_rendered_title_is_rejected():
    assert parse_rendered("Title: search\n\nMarkdown Content:\nx", IBM_URL) is None


# -- fetch order ---------------------------------------------------------


def test_the_proxy_recovers_a_posting_nothing_else_can_read():
    session = FakeSession()
    details = JobFetcher(session=session).fetch(IBM_URL)

    assert details.title == "Software Developer Intern 2027"
    assert details.source == "rendered"


def test_the_recovered_posting_classifies_as_an_internship():
    # The whole point: this is the posting that was missed.
    details = JobFetcher(session=FakeSession()).fetch(IBM_URL)
    job = classify(details, IBM_URL)

    assert job.classification is Classification.RELEVANT
    assert job.role_type is RoleType.INTERNSHIP


def test_a_readable_page_never_reaches_the_proxy():
    """The fast path must stay fast — the proxy costs seconds and a third-party call."""

    class ReadableSession(FakeSession):
        def get(self, url, headers=None, timeout=None, allow_redirects=None):
            self.urls.append(url)
            return FakeResponse(
                '<html><head><meta property="og:title" content="Software Engineer, New Grad">'
                "</head><body></body></html>"
            )

    session = ReadableSession()
    details = JobFetcher(session=session).fetch("https://boards.greenhouse.io/acme/jobs/1")

    assert details.title == "Software Engineer, New Grad"
    assert not any(u.startswith("https://r.jina.ai/") for u in session.urls)


def test_a_usable_slug_is_preferred_over_the_proxy():
    session = FakeSession()
    url = (
        "https://google.com/about/careers/applications/jobs/results/"
        "78703249065943750-software-engineer-early-career-campus"
    )
    details = JobFetcher(session=session).fetch(url)

    assert details.source == "slug"
    assert not any(u.startswith("https://r.jina.ai/") for u in session.urls)


# -- failure containment -------------------------------------------------


def test_a_failing_proxy_degrades_to_unknown():
    session = FakeSession(proxy_exc=requests.ConnectionError("boom"))
    details = JobFetcher(session=session).fetch(IBM_URL)

    assert details.title is None
    assert classify(details, IBM_URL).classification is Classification.UNKNOWN


def test_the_fallback_can_be_disabled():
    session = FakeSession()
    details = JobFetcher(session=session, render_proxy_url="").fetch(IBM_URL)

    assert details.title is None
    assert not any(u.startswith("https://r.jina.ai/") for u in session.urls)


def test_an_upstream_refusal_reported_by_the_proxy_is_not_a_title():
    # The proxy returns 200 with an error title when the site blocks it too. Tesla does
    # exactly this. Without the guard, "Access Denied" becomes the job title.
    body = (
        "Title: Access Denied\n\nURL Source: https://tesla.com/careers/search/job/279763\n\n"
        "Warning: Target URL returned error 403: Forbidden\n\nMarkdown Content:\n"
        "You don't have permission to access this server."
    )
    assert parse_rendered(body, "https://tesla.com/careers/search/job/279763") is None
