"""Job retrieval: fetch a posting and pull title, company and location out of it.

Three sources are tried in order of reliability:

1. JSON-LD ``JobPosting`` — the structured format most ATS platforms emit.
2. OpenGraph / ``<title>`` metadata.
3. The URL slug, for postings rendered entirely client side (Workday and friends
   return an empty shell to a plain HTTP fetch, and browser automation is a non-goal).
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

# Slug segments that are path scaffolding rather than part of a role name.
_SLUG_NOISE = {
    "job", "jobs", "career", "careers", "posting", "postings", "opening",
    "openings", "details", "detail", "apply", "en", "en-us", "us", "position",
    "positions", "role", "roles", "req",
}


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
    def __init__(self, timeout: int = 20, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()

    def fetch(self, url: str) -> JobDetails:
        html = self._get_html(url)
        if html is None:
            return slug_details(url)

        details = parse_html(html, url)
        if details.title:
            return details

        log.info("No title found in HTML for %s; falling back to the URL slug", url)
        return slug_details(url)

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
    if not title:
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


def slug_details(url: str) -> JobDetails:
    """Derive a best-effort title from the URL path when the page yields nothing."""
    title = slug_title(url)
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
    return " ".join(words[-1].split())


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
