"""Story source backed by the igexport.com public endpoint.

    GET https://igexport.com/api/ig-stories/?username=<username>

Responses nest the Stories under ``data.items``; each item carries ``id``,
``taken_at`` and an optional ``story_link_stickers`` array.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from ..links import extract_links
from ..models import Story
from .base import StorySource, StorySourceError

log = logging.getLogger(__name__)

ENDPOINT = "https://igexport.com/api/ig-stories/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Attempts per poll, and the waits between them.
#
# Measured 2026-09-03: the endpoint returned 502 on 10 of 12 requests while still
# serving a complete payload on the other two. One attempt per poll turned an API that
# works one time in six into a source that looked dead, and the poller's backoff then
# compounded it — three consecutive failures push the retry interval to its 8-minute
# ceiling, so each 17% chance was being spent 8 minutes apart.
#
# Four attempts take one poll from ~17% to ~52%. The waits are short deliberately: this
# runs inside a poll, and Story detection latency is the thing the project exists to
# minimise. Worst case here is well inside the 60s poll interval on a 502, which comes
# back in well under a second.
MAX_ATTEMPTS = 4
RETRY_WAITS = (2.0, 4.0, 8.0)


def _worth_retrying(exc: requests.RequestException) -> bool:
    """Whether another attempt could plausibly succeed.

    Server errors and transport failures are transient. A 4xx means the request itself
    is wrong — a bad username will never come good, and repeating it is just load on an
    endpoint that is already struggling.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return True  # timeout or connection error, nothing to read a status from
    return response.status_code >= 500


class IGExportStorySource(StorySource):
    def __init__(
        self,
        timeout: int = 30,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        # Injected so tests exercise the retry path without actually waiting.
        self.sleep = sleep

    def fetch_stories(self, username: str) -> list[Story]:
        payload = self._get_payload(username)
        return self.parse(payload, username)

    def _get_payload(self, username: str) -> dict:
        """Fetch the raw payload, retrying the transient failures this API is prone to."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.session.get(
                    ENDPOINT,
                    params={"username": username},
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                if attempt == MAX_ATTEMPTS or not _worth_retrying(exc):
                    raise StorySourceError(f"IGExport request failed: {exc}") from exc
                wait = RETRY_WAITS[attempt - 1]
                log.warning(
                    "IGExport attempt %d/%d failed (%s); retrying in %.0fs",
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                    wait,
                )
                self.sleep(wait)
            except ValueError as exc:
                raise StorySourceError(f"IGExport returned invalid JSON: {exc}") from exc

        raise AssertionError("unreachable: the loop either returns or raises")

    @staticmethod
    def parse(payload: dict, username: str) -> list[Story]:
        """Turn a raw IGExport payload into Story objects."""
        if not isinstance(payload, dict):
            raise StorySourceError("IGExport payload was not an object")

        items = (payload.get("data") or {}).get("items")
        if items is None:
            raise StorySourceError("IGExport payload had no data.items")
        if not isinstance(items, list):
            raise StorySourceError("IGExport data.items was not a list")

        stories = []
        for item in items:
            story = _to_story(item, username)
            if story is not None:
                stories.append(story)
        stories.sort(key=lambda s: s.posted_at)
        return stories


def _to_story(item: object, username: str) -> Story | None:
    if not isinstance(item, dict):
        return None

    story_id = item.get("id") or item.get("pk")
    if story_id is None:
        log.warning("Skipping Story item without an id")
        return None

    owner = item.get("user") if isinstance(item.get("user"), dict) else {}
    return Story(
        story_id=str(story_id),
        username=owner.get("username") or username,
        posted_at=_parse_timestamp(item.get("taken_at")),
        links=extract_links(item),
    )


def _parse_timestamp(taken_at: object) -> datetime:
    if isinstance(taken_at, (int, float)):
        return datetime.fromtimestamp(taken_at, tz=timezone.utc)
    return datetime.now(timezone.utc)
