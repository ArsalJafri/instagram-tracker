"""Story source backed by the igexport.com public endpoint.

    GET https://igexport.com/api/ig-stories/?username=<username>

Responses nest the Stories under ``data.items``; each item carries ``id``,
``taken_at`` and an optional ``story_link_stickers`` array.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from ..links import extract_links
from ..models import Story
from .base import StorySource, StorySourceError

log = logging.getLogger(__name__)

ENDPOINT = "https://igexport.com/api/ig-stories/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class IGExportStorySource(StorySource):
    def __init__(self, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()

    def fetch_stories(self, username: str) -> list[Story]:
        try:
            response = self.session.get(
                ENDPOINT,
                params={"username": username},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise StorySourceError(f"IGExport request failed: {exc}") from exc
        except ValueError as exc:
            raise StorySourceError(f"IGExport returned invalid JSON: {exc}") from exc

        return self.parse(payload, username)

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
