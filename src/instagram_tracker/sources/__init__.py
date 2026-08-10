"""Story source adapters."""

from __future__ import annotations

from .base import StorySource, StorySourceError
from .composite import CompositeStorySource
from .igexport import IGExportStorySource
from .instagram_bio import DEFAULT_MIN_INTERVAL_SECONDS, InstagramBioSource

_PROVIDERS = {
    "igexport": lambda **kwargs: IGExportStorySource(),
    "instagram_bio": lambda bio_interval_seconds=DEFAULT_MIN_INTERVAL_SECONDS, **kwargs: (
        InstagramBioSource(min_interval_seconds=bio_interval_seconds)
    ),
}


def build_story_source(
    provider: str,
    bio_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
) -> StorySource:
    """Build one source, or a composite when STORY_PROVIDER lists several.

    Comma-separated providers run together on every poll, so a single provider going
    down no longer stops detection.
    """
    names = [name.strip().lower() for name in provider.split(",") if name.strip()]
    if not names:
        raise ValueError("STORY_PROVIDER is empty")

    sources = []
    for name in names:
        try:
            factory = _PROVIDERS[name]
        except KeyError:
            known = ", ".join(sorted(_PROVIDERS))
            raise ValueError(
                f"Unknown STORY_PROVIDER {name!r}; known providers: {known}"
            ) from None
        sources.append(factory(bio_interval_seconds=bio_interval_seconds))

    return sources[0] if len(sources) == 1 else CompositeStorySource(sources)


__all__ = [
    "StorySource",
    "StorySourceError",
    "CompositeStorySource",
    "IGExportStorySource",
    "InstagramBioSource",
    "build_story_source",
]
