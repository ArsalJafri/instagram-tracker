"""Job retrieval: fetch a posting and pull title, company and location out of it.

Four sources are tried, in order of reliability and cost:

1. JSON-LD ``JobPosting`` — the structured format most ATS platforms emit.
2. OpenGraph / ``<title>`` metadata.
3. The URL slug, for postings rendered entirely client side (Workday and friends
   return an empty shell to a plain HTTP fetch, and browser automation is a non-goal).
4. A rendering proxy, which executes the page's JavaScript on our behalf.

Step 4 exists because some sites cannot be read at all by a plain client.
``careers.ibm.com`` answers with an AWS WAF challenge — HTTP 202 and a script that must
run to mint a token — so there is no HTML, no JSON-LD and no usable slug. A real
internship was missed this way. Notably the page is perfectly ordinary once the
challenge passes: it serves an `og:title` that step 2 already understands, so the
problem was never parsing, only getting someone to run a browser.

It is last for a reason: it costs several seconds and a third-party call, against
milliseconds for the others. The common path never reaches it, and if the proxy fails
the result is the `unknown` it would have been anyway.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# `{url}` is replaced with the target. Set RENDER_PROXY_URL empty to disable entirely.
DEFAULT_RENDER_PROXY = "https://r.jina.ai/{url}"

# Slug segments that are path scaffolding rather than part of a role name.
_SLUG_NOISE = {
    "job", "jobs", "career", "careers", "posting", "postings", "opening",
    "openings", "details", "detail", "apply", "en", "en-us", "us", "position",
    "positions", "role", "roles", "req", "search", "jobdetail", "collect",
}

# Page titles that carry no information about the role. Client-rendered career sites
# return these from a plain fetch — "JobDetail" from IBM, "search" from Tesla, "Yello"
# from a Boeing event page.
#
# These are worse than no title at all: a title makes the classifier reject confidently
# when in truth it never saw the posting. Discarding them lets the result fall through to
# `unknown`, which is honest and can be reviewed, instead of a false "not relevant".
_UNINFORMATIVE_TITLES = {
    "home", "search", "jobs", "job", "careers", "career", "jobdetail",
    "job detail", "job search", "search jobs", "careers home", "apply",
    "sign in", "log in", "login", "page not found", "access denied", "error",
}


def is_uninformative(title: str) -> bool:
    """True when a title tells us nothing about the role.

    Single words are treated as uninformative because a real posting title is never one
    word — but a page shell, a vendor name or an opaque id frequently is.
    """
    collapsed = re.sub(r"\s+", " ", title).strip().lower()
    if not collapsed:
        return True
    if collapsed.strip(" |-–—") in _UNINFORMATIVE_TITLES:
        return True
    return len(collapsed.split()) < 2


@dataclass(frozen=True)
class JobDetails:
    """What could be recovered about a posting, plus the text used to classify it."""

    title: str | None
    company: str | None
    location: str | None
    employment_type: str | None
    text: str
    source: str


class JobFetcher:
    def __init__(
        self,
        timeout: int = 20,
        session: requests.Session | None = None,
        render_proxy_url: str = DEFAULT_RENDER_PROXY,
        render_timeout: int = 45,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.render_proxy_url = render_proxy_url
        self.render_timeout = render_timeout

    def fetch(self, url: str) -> JobDetails:
        html = self._get_html(url)
        if html is not None:
            details = parse_html(html, url)
            if details.title:
                return details
            log.info("No title found in HTML for %s; falling back to the URL slug", url)

        slug = slug_details(url)
        if slug.title:
            return slug

        # Everything cheap has failed. Only now is it worth paying for a rendered fetch.
        rendered = self._fetch_rendered(url)
        if rendered and rendered.title:
            log.info("Recovered %r for %s via the rendering proxy", rendered.title, url)
            return rendered

        # Title is None, so this classifies as unknown — the same outcome as before.
        return slug

    def _fetch_rendered(self, url: str) -> JobDetails | None:
        """Ask a rendering proxy to run the page's JavaScript and return its text.

        Never raises: this is a last resort, and its failure must leave the caller with
        the `unknown` it already had rather than breaking the poll.
        """
        if not self.render_proxy_url:
            return None
        try:
            response = self.session.get(
                self.render_proxy_url.replace("{url}", url),
                headers={"User-Agent": "instagram-tracker", "Accept": "text/plain"},
                timeout=self.render_timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Rendering proxy failed for %s: %s", url, exc)
            return None
        return parse_rendered(response.text, url)

    def _get_html(self, url: str) -> str | None:
        try:
            response = self.session.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=self.timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Could not fetch %s: %s", url, exc)
            return None
        if "html" not in response.headers.get("content-type", "").lower():
            return None
        return response.text


def parse_html(html: str, url: str) -> JobDetails:
    soup = BeautifulSoup(html, "html.parser")

    posting = _find_job_posting(soup)
    if posting:
        title = _clean(posting.get("title"))
        if title:
            return JobDetails(
                title=title,
                company=_organization_name(posting.get("hiringOrganization")),
                location=_location_name(posting.get("jobLocation")),
                employment_type=_employment_type(posting.get("employmentType")),
                text=" ".join(
                    filter(None, [title, _description_text(posting.get("description"))])
                ),
                source="json-ld",
            )

    title = _meta(soup, "og:title") or _clean(soup.title.string if soup.title else None)
    # A JSON-LD JobPosting title is the employer's own statement and is trusted as-is.
    # Page metadata is not: a client-rendered shell yields the site's chrome, not the role.
    if not title or is_uninformative(title):
        if title:
            log.info("Discarding uninformative page title %r for %s", title, url)
        return JobDetails(None, None, None, None, "", "none")

    description = _meta(soup, "og:description") or _meta_name(soup, "description") or ""
    return JobDetails(
        title=title,
        company=_meta(soup, "og:site_name"),
        location=None,
        employment_type=None,
        text=f"{title} {description}".strip(),
        source="metadata",
    )


def parse_rendered(body: str, url: str) -> JobDetails | None:
    """Pull a title and body text out of a rendering proxy's plain-text response.

    The format is a short header block followed by the page content:

        Title: Software Developer Intern 2027

        URL Source: https://...

        Markdown Content:
        ...
    """
    # The proxy reports upstream failures in-band, with a 200 and an error title such as
    # "Access Denied". Without this the error page becomes the job title and is rejected
    # as "not relevant" — a confident wrong answer instead of an honest unknown.
    if "Target URL returned error" in body:
        log.info("Rendering proxy reached %s but the site refused it", url)
        return None

    title = None
    for line in body.splitlines():
        if line.lower().startswith("title:"):
            title = _clean(line.split(":", 1)[1])
            break

    if not title or is_uninformative(title):
        return None

    marker = "Markdown Content:"
    text = body.split(marker, 1)[1] if marker in body else body
    # The description adds context for classification but the title carries the signal.
    return JobDetails(
        title=title,
        company=None,
        location=None,
        employment_type=None,
        text=f"{title} {re.sub(r'\\s+', ' ', text)[:4000]}".strip(),
        source="rendered",
    )


def slug_details(url: str) -> JobDetails:
    """Derive a best-effort title from the URL path when the page yields nothing."""
    title = slug_title(url)
    # Slugs also produce junk — an opaque event id is one "word" and means nothing.
    if title and is_uninformative(title):
        log.info("Discarding uninformative slug title %r for %s", title, url)
        title = None
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return JobDetails(
        title=title,
        company=None,
        location=None,
        employment_type=None,
        text=f"{title or ''} {host}".strip(),
        source="slug",
    )


def slug_title(url: str) -> str | None:
    segments = [unquote(part) for part in urlparse(url).path.split("/") if part]
    words: list[str] = []
    for segment in segments:
        cleaned = re.sub(r"[-_+]+", " ", segment).strip()
        if not cleaned or cleaned.lower() in _SLUG_NOISE:
            continue
        # Skip pure identifiers such as "22978" or "R-1024884".
        if not re.search(r"[A-Za-z]{3}", cleaned) or re.fullmatch(r"[A-Za-z]{1,3}[-_]?\d+", cleaned):
            continue
        words.append(cleaned)
    if not words:
        return None

    # Requisition ids ride along on the slug ("...Software-Engineer_R-12345").
    tokens = words[-1].split()
    while tokens and re.fullmatch(r"[A-Za-z]{0,3}\d+[A-Za-z]?", tokens[-1]):
        tokens.pop()
    while tokens and re.fullmatch(r"[A-Za-z]{1,2}", tokens[-1]):
        tokens.pop()
    return " ".join(tokens) or None


def _find_job_posting(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        posting = _search_job_posting(data)
        if posting:
            return posting
    return None


def _search_job_posting(node: object) -> dict | None:
    if isinstance(node, list):
        for entry in node:
            found = _search_job_posting(entry)
            if found:
                return found
        return None
    if not isinstance(node, dict):
        return None

    node_type = node.get("@type")
    types = node_type if isinstance(node_type, list) else [node_type]
    if any(isinstance(t, str) and t.lower() == "jobposting" for t in types):
        return node

    for value in node.values():
        if isinstance(value, (dict, list)):
            found = _search_job_posting(value)
            if found:
                return found
    return None


def _organization_name(value: object) -> str | None:
    if isinstance(value, dict):
        return _clean(value.get("name"))
    if isinstance(value, list) and value:
        return _organization_name(value[0])
    return _clean(value)


def _location_name(value: object) -> str | None:
    if isinstance(value, list):
        names = [_location_name(entry) for entry in value]
        joined = ", ".join(dict.fromkeys(name for name in names if name))
        return joined or None
    if not isinstance(value, dict):
        return _clean(value)

    address = value.get("address")
    if isinstance(address, dict):
        parts = [
            _clean(address.get(key))
            for key in ("addressLocality", "addressRegion", "addressCountry")
        ]
        joined = ", ".join(part for part in parts if part)
        if joined:
            return joined
    return _clean(value.get("name"))


def _employment_type(value: object) -> str | None:
    if isinstance(value, list):
        joined = " ".join(str(entry) for entry in value if entry)
        return joined or None
    return _clean(value)


def _description_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def _meta(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", attrs={"property": prop})
    return _clean(tag.get("content")) if tag else None


def _meta_name(soup: BeautifulSoup, name: str) -> str | None:
    tag = soup.find("meta", attrs={"name": name})
    return _clean(tag.get("content")) if tag else None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    collapsed = re.sub(r"\s+", " ", value).strip()
    return collapsed or None
