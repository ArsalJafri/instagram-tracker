"""The Story source contract.

IGExport is an implementation detail. Everything downstream depends only on this
interface, so replacing the provider means writing one new adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Story


class StorySourceError(RuntimeError):
    """Raised when a provider cannot be reached or returns something unusable."""


class StorySource(ABC):
    @abstractmethod
    def fetch_stories(self, username: str) -> list[Story]:
        """Return the account's currently active Stories, oldest first."""
