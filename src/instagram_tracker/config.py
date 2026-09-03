"""Runtime configuration, loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .jobs import DEFAULT_RENDER_PROXY

_TRUE = {"1", "true", "yes", "on"}

# Settings renamed on 2026-08-24, old name kept working.
#
# `DISCORD_WEBHOOK_URL` and `DISCORD_MENTIONS` read like generic defaults but only ever
# meant the new-grad channel, which their `INTERNSHIP` counterparts made confusing.
# `DISCORD_UNKNOWN_WEBHOOK_URL` was worse than confusing: it was named on 2026-08-11 when
# that channel only received pages the fetcher could not read, and since the 08-13
# "every link surfaces" change it also takes near misses and plain rejections. The health
# endpoint has called it `review` ever since; the setting was the last place still
# claiming otherwise.
#
# Both spellings are accepted deliberately. A hard rename would mean that deploying the
# code before updating the host's environment silently stops every alert — this project's
# signature failure, and not one worth reintroducing over a naming tidy-up.
RENAMED_SETTINGS = {
    "DISCORD_NEW_GRAD_WEBHOOK_URL": "DISCORD_WEBHOOK_URL",
    "DISCORD_NEW_GRAD_MENTIONS": "DISCORD_MENTIONS",
    "DISCORD_REVIEW_WEBHOOK_URL": "DISCORD_UNKNOWN_WEBHOOK_URL",
}


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() in _TRUE


def _setting(name: str, default: str = "") -> str:
    """Read a setting by its current name, falling back to the name it used to have."""
    if (value := os.getenv(name)) not in (None, ""):
        return value
    legacy = RENAMED_SETTINGS.get(name)
    if legacy and (value := os.getenv(legacy)) not in (None, ""):
        return value
    return default


def deprecated_settings_in_use() -> list[tuple[str, str]]:
    """Old setting names still carrying the value, as (old, new) pairs.

    Reported at startup rather than corrected silently, so the environment and the
    documentation can be brought back into agreement instead of drifting apart.
    """
    return [
        (legacy, current)
        for current, legacy in RENAMED_SETTINGS.items()
        if not os.getenv(current) and os.getenv(legacy)
    ]


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
    # Confidence a classification must reach before it routes anywhere but review. These
    # are bounded scores, not calibrated probabilities, so the values are tuned against
    # observed classifications rather than derived. Kept here, not scattered in the code.
    role_confidence_threshold: float = 0.60
    employment_confidence_threshold: float = 0.55
    # Added to both thresholds when the fetcher recovered little text. A slug posting is
    # a handful of words off a URL path; scoring it confidently is overconfidence.
    poor_input_confidence_penalty: float = 0.15
    # Quiet hours, in `poll_timezone`, polled at `quiet_poll_interval_seconds` instead of
    # the normal interval. Derived from observation, not assumption: across 96 Stories in
    # two samples a month apart (59 on 2026-08-09..11, 37 on 2026-09-02..03) not one was
    # posted between 23:00 and 06:00 Pacific. The two samples disagree about where the
    # daily peak sits, so there is deliberately no third "peak" tier — that would be
    # fitting noise. Start == end disables the quiet window.
    poll_timezone: str = "America/Los_Angeles"
    quiet_hour_start: int = 23
    quiet_hour_end: int = 6
    quiet_poll_interval_seconds: int = 600

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
            discord_webhook_url=_setting("DISCORD_NEW_GRAD_WEBHOOK_URL"),
            discord_internship_webhook_url=os.getenv("DISCORD_INTERNSHIP_WEBHOOK_URL", ""),
            discord_unknown_webhook_url=_setting("DISCORD_REVIEW_WEBHOOK_URL"),
            discord_mentions=_setting("DISCORD_NEW_GRAD_MENTIONS"),
            discord_internship_mentions=os.getenv("DISCORD_INTERNSHIP_MENTIONS", ""),
            heartbeat_url=os.getenv("HEARTBEAT_URL", ""),
            bio_poll_interval_seconds=int(os.getenv("BIO_POLL_INTERVAL_SECONDS", "3600")),
            render_proxy_url=os.getenv("RENDER_PROXY_URL", DEFAULT_RENDER_PROXY),
            role_confidence_threshold=float(
                os.getenv("ROLE_CONFIDENCE_THRESHOLD", "0.60")
            ),
            employment_confidence_threshold=float(
                os.getenv("EMPLOYMENT_CONFIDENCE_THRESHOLD", "0.55")
            ),
            poll_timezone=os.getenv("POLL_TIMEZONE", "America/Los_Angeles"),
            quiet_hour_start=int(os.getenv("QUIET_HOUR_START", "23")),
            quiet_hour_end=int(os.getenv("QUIET_HOUR_END", "6")),
            quiet_poll_interval_seconds=int(
                os.getenv("QUIET_POLL_INTERVAL_SECONDS", "600")
            ),
            poor_input_confidence_penalty=float(
                os.getenv("POOR_INPUT_CONFIDENCE_PENALTY", "0.15")
            ),
        )
