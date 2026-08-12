"""Runtime configuration, loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .jobs import DEFAULT_RENDER_PROXY

_TRUE = {"1", "true", "yes", "on"}


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() in _TRUE


@dataclass(frozen=True)
class Config:
    instagram_username: str
    story_provider: str
    poll_interval_seconds: int
    process_existing_stories_on_startup: bool
    database_path: Path
    # Set on hosts with an ephemeral filesystem (Render). Takes precedence over the path.
    database_url: str = ""
    discord_webhook_url: str = ""
    # Internships go to their own channel; falls back to the main webhook when unset.
    discord_internship_webhook_url: str = ""
    # Postings the fetcher could not read at all — a review channel, not an alert channel.
    discord_unknown_webhook_url: str = ""
    # Raw Discord mention text prepended to each alert, e.g. "<@&123>" for a role.
    # Independent per role type — these deliberately do not fall back to each other.
    discord_mentions: str = ""
    discord_internship_mentions: str = ""
    heartbeat_url: str = ""
    # Instagram is polled directly by the bio source; far slower than the Story cadence
    # so a residential IP does not attract a throttle. 600s did attract one.
    bio_poll_interval_seconds: int = 3600
    # Rendering proxy used only when a page cannot be read any other way. `{url}` is
    # replaced with the target; empty disables the fallback entirely.
    render_proxy_url: str = DEFAULT_RENDER_PROXY

    @property
    def database_target(self) -> str | Path:
        """Where state lives: a Postgres URL when set, otherwise the SQLite path."""
        return self.database_url or self.database_path

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        return cls(
            instagram_username=os.getenv("INSTAGRAM_USERNAME", "zero2sudo"),
            story_provider=os.getenv("STORY_PROVIDER", "igexport"),
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "60")),
            process_existing_stories_on_startup=_as_bool(
                os.getenv("PROCESS_EXISTING_STORIES_ON_STARTUP", "false")
            ),
            database_path=Path(os.getenv("DATABASE_PATH", "./data/job_monitor.db")),
            database_url=os.getenv("DATABASE_URL", ""),
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
            discord_internship_webhook_url=os.getenv("DISCORD_INTERNSHIP_WEBHOOK_URL", ""),
            discord_unknown_webhook_url=os.getenv("DISCORD_UNKNOWN_WEBHOOK_URL", ""),
            discord_mentions=os.getenv("DISCORD_MENTIONS", ""),
            discord_internship_mentions=os.getenv("DISCORD_INTERNSHIP_MENTIONS", ""),
            heartbeat_url=os.getenv("HEARTBEAT_URL", ""),
            bio_poll_interval_seconds=int(os.getenv("BIO_POLL_INTERVAL_SECONDS", "3600")),
            render_proxy_url=os.getenv("RENDER_PROXY_URL", DEFAULT_RENDER_PROXY),
        )
