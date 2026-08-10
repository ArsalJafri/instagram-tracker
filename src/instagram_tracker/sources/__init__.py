"""Story source adapters."""

from __future__ import annotations

from .base import StorySource, StorySourceError
from .igexport import IGExportStorySource

_PROVIDERS: dict[str, type[StorySource]] = {
    "igexport": IGExportStorySource,
}


def build_story_source(provider: str) -> StorySource:
    try:
        return _PROVIDERS[provider.strip().lower()]()
    except KeyError:
        known = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"Unknown STORY_PROVIDER {provider!r}; known providers: {known}") from None


__all__ = [
    "StorySource",
    "StorySourceError",
    "IGExportStorySource",
    "build_story_source",
]
