"""Link extraction, redirect unwrapping and URL normalization.

Instagram wraps outbound Story links as ``https://l.instagram.com/?u=<encoded>``.
The ``u`` parameter holds the real destination, so the wrapper is unwrapped locally
rather than by following the redirect. ``display_url`` is deliberately not trusted:
it is truncated for display and carries no scheme.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from .models import Link

log = logging.getLogger(__name__)

REDIRECT_HOSTS = {"l.instagram.com", "l.facebook.com", "lm.facebook.com"}

# Tracking parameters that vary per impression and would otherwise defeat dedup.
TRACKING_PARAMS = {
    "fbclid",
    "igshid",
    "igsh",
    "e",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
}


def extract_links(item: dict) -> list[Link]:
    """Pull every usable link off a raw Story item's ``story_link_stickers``."""
    stickers = item.get("story_link_stickers")
    if not isinstance(stickers, list):
        return []

    links: list[Link] = []
    seen: set[str] = set()
    for sticker in stickers:
        if not isinstance(sticker, dict):
            continue
        story_link = sticker.get("story_link")
        if not isinstance(story_link, dict):
            continue
        url = story_link.get("url")
        if not isinstance(url, str) or not url.strip():
            continue

        canonical = canonicalize(url)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        links.append(Link(original_url=url, canonical_url=canonical))
    return links


def unwrap_redirect(url: str) -> str:
    """Resolve an Instagram/Facebook link wrapper to its destination URL."""
    seen: set[str] = set()
    current = url
    # Wrappers occasionally nest; stop as soon as one fails to unwrap.
    while current not in seen:
        seen.add(current)
        parsed = urlparse(current)
        if _bare_host(parsed.netloc) not in REDIRECT_HOSTS:
            return current
        target = parse_qs(parsed.query).get("u")
        if not target or not target[0]:
            log.debug("Redirect wrapper %s had no 'u' parameter", current)
            return current
        current = unquote(target[0])
    return current


def normalize(url: str) -> str:
    """Strip tracking noise so the same posting always yields the same key."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""

    scheme = "https" if parsed.scheme in ("http", "https") else parsed.scheme.lower()
    host = _bare_host(parsed.netloc)

    path = parsed.path.rstrip("/") or "/"

    kept = [
        (key, value)
        for key, value in _ordered_query(parsed.query)
        if key.lower() not in TRACKING_PARAMS
    ]
    query = "&".join(f"{key}={value}" if value else key for key, value in kept)

    return urlunparse((scheme, host, path, "", query, ""))


def canonicalize(url: str) -> str:
    """Unwrap then normalize; the result is the deduplication key for a link."""
    return normalize(unwrap_redirect(url.strip()))


def _bare_host(netloc: str) -> str:
    host = netloc.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _ordered_query(query: str) -> list[tuple[str, str]]:
    pairs = []
    for chunk in query.split("&"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        pairs.append((key, value))
    return pairs
