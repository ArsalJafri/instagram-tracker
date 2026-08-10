"""Story source backed by Instagram's public profile endpoint.

    GET https://www.instagram.com/api/v1/users/web_profile_info/?username=<username>

This is the request a logged-out browser makes, and it needs no session: profile
metadata is public even though Story *content* is not. The account's `bio_links` carry
the job link it is currently promoting, so this is a second, independent path to the
same postings that owes nothing to a third party.

Two limits are deliberate and documented in the spec:

* **It is a partial signal.** The bio holds a curated current link rather than everything
  that goes out on Stories, so it supplements IGExport instead of replacing it. Overlap
  is harmless — deduplication is on `canonical_url`.
* **It is throttled hard.** Polling Instagram directly from a residential IP at the Story
  cadence invites a throttle or a soft block, so this source refuses to fetch more often
  than `min_interval_seconds` and returns nothing in between.

Bio links have no publish timestamp, so the synthesised Stories carry discovery time and
a `bio:` prefixed id. Filter that prefix out of `processed_stories` before measuring
detection latency, or it will read as zero.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone

import requests

from ..links import canonicalize
from ..models import Link, Story
from .base import StorySource, StorySourceError

log = logging.getLogger(__name__)

ENDPOINT = "https://www.instagram.com/api/v1/users/web_profile_info/"
# The public web client's app id, sent by ordinary logged-out browsers.
WEB_APP_ID = "936619743392459"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

STORY_ID_PREFIX = "bio:"
DEFAULT_MIN_INTERVAL_SECONDS = 600


class InstagramBioSource(StorySource):
    def __init__(
        self,
        min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
        timeout: int = 20,
        session: requests.Session | None = None,
        clock=time.monotonic,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.timeout = timeout
        self.session = session or requests.Session()
        self._clock = clock
        self._last_attempt: float | None = None

    def fetch_stories(self, username: str) -> list[Story]:
        if not self._due():
            return []

        # Recorded before the request so a failing endpoint is not retried every tick.
        self._last_attempt = self._clock()

        try:
            response = self.session.get(
                ENDPOINT,
                params={"username": username},
                headers={
                    "User-Agent": USER_AGENT,
                    "X-IG-App-ID": WEB_APP_ID,
                    "Accept": "*/*",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise StorySourceError(f"Instagram profile request failed: {exc}") from exc
        except ValueError as exc:
            raise StorySourceError(f"Instagram profile returned invalid JSON: {exc}") from exc

        return self.parse(payload, username)

    def _due(self) -> bool:
        if self._last_attempt is None:
            return True
        return (self._clock() - self._last_attempt) >= self.min_interval_seconds

    @staticmethod
    def parse(payload: dict, username: str, now: datetime | None = None) -> list[Story]:
        """Turn a profile payload into one synthetic Story per distinct bio link."""
        if not isinstance(payload, dict):
            raise StorySourceError("Instagram profile payload was not an object")

        user = (payload.get("data") or {}).get("user")
        if not isinstance(user, dict):
            raise StorySourceError("Instagram profile payload had no data.user")

        discovered_at = now or datetime.now(timezone.utc)
        owner = user.get("username") or username

        stories: list[Story] = []
        seen: set[str] = set()
        for raw in _candidate_urls(user):
            canonical = canonicalize(raw)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            stories.append(
                Story(
                    story_id=_synthetic_id(canonical),
                    username=owner,
                    posted_at=discovered_at,
                    links=[Link(original_url=raw, canonical_url=canonical)],
                )
            )

        log.debug("Instagram bio exposed %d distinct link(s) for @%s", len(stories), owner)
        return stories


def _candidate_urls(user: dict) -> list[str]:
    """Bio links first, then external_url, which is usually a duplicate of the first."""
    urls: list[str] = []
    for entry in user.get("bio_links") or []:
        if not isinstance(entry, dict):
            continue
        # `url` is already unwrapped; `lynx_url` is the l.instagram.com wrapper.
        raw = entry.get("url") or entry.get("lynx_url")
        if isinstance(raw, str) and raw.strip():
            urls.append(raw.strip())

    external = user.get("external_url")
    if isinstance(external, str) and external.strip():
        urls.append(external.strip())
    return urls


def _synthetic_id(canonical_url: str) -> str:
    digest = hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()[:16]
    return f"{STORY_ID_PREFIX}{digest}"
