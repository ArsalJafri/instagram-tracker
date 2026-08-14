"""Discord webhook notifications."""

from __future__ import annotations

import logging
import re

import requests

from .classifier import is_known_ats
from .models import Classification, Job, RoleType

log = logging.getLogger(__name__)

EMBED_COLOR = 0x2ECC71
INTERNSHIP_EMBED_COLOR = 0x3498DB
UNKNOWN_EMBED_COLOR = 0xE67E22

HEADLINE = {
    RoleType.NEW_GRAD: "New entry-level software role",
    RoleType.INTERNSHIP: "New software internship",
}
UNKNOWN_HEADLINE = "Could not read this posting — check it manually"
NEAR_MISS_HEADLINE = "Near match — matched one rule but not the other"
NEAR_MISS_EMBED_COLOR = 0xF1C40F
OTHER_HEADLINE = "Other link — did not match the rules"
OTHER_EMBED_COLOR = 0x95A5A6


class DiscordNotifier:
    """Posts to Discord, routing internships to their own webhook when one is set.

    If no internship webhook is configured, internships fall back to the main one rather
    than being dropped — a missing channel should degrade the routing, not the alerting.

    Mentions are per role type and do *not* fall back to each other: pinging the wrong
    group is worse than pinging nobody.
    """

    def __init__(
        self,
        webhook_url: str,
        internship_webhook_url: str = "",
        mentions: str = "",
        internship_mentions: str = "",
        unknown_webhook_url: str = "",
        timeout: int = 15,
        session: requests.Session | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.internship_webhook_url = internship_webhook_url
        self.unknown_webhook_url = unknown_webhook_url
        self.mentions = mentions
        self.internship_mentions = internship_mentions
        self.timeout = timeout
        self.session = session or requests.Session()

    def mentions_for(self, job: Job) -> str:
        # Only a confirmed match is worth a ping. Everything else is for reading later.
        if job.classification is not Classification.RELEVANT:
            return ""
        if job.role_type is RoleType.INTERNSHIP:
            return self.internship_mentions
        return self.mentions

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url or self.internship_webhook_url)

    def webhook_for(self, job: Job) -> str:
        # Anything that is not a confirmed match goes to the review channel — if
        # @zero2sudo posted a link, it surfaces somewhere. Deciding which rejections were
        # "interesting enough" repeatedly guessed wrong, and the misses were invisible.
        #
        # These never fall back to a real channel: the point of a separate channel is
        # that they do not mix with confirmed matches. With none configured they stay
        # silent, as before.
        if job.classification is not Classification.RELEVANT:
            return self.unknown_webhook_url
        if job.role_type is RoleType.INTERNSHIP and self.internship_webhook_url:
            return self.internship_webhook_url
        return self.webhook_url or self.internship_webhook_url

    def notify(self, job: Job, username: str) -> bool:
        webhook = self.webhook_for(job)
        if not webhook:
            log.warning("No Discord webhook configured; skipping notification for %s", job.url)
            return False
        try:
            response = self.session.post(
                webhook,
                json=build_payload(job, username, self.mentions_for(job)),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            log.error("Discord notification failed for %s: %s", job.url, exc)
            return False
        if job.classification is Classification.RELEVANT:
            label = job.role_type.value
        elif job.classification is Classification.UNKNOWN:
            label = "unreadable"
        else:
            label = "near-miss" if job.near_miss else "other"
        log.info("Notified Discord (%s) about %s", label, job.url)
        return True


_VALID_MENTION = re.compile(r"<@[!&]?\d+>")
_SNOWFLAKE = re.compile(r"\d{15,}")


def malformed_mentions(mentions: str) -> list[str]:
    """IDs written without their wrapper, which Discord renders as plain text.

    `@1536859615256645732` sends fine, looks plausible and pings nobody — the failure is
    entirely silent, so it is worth reporting at startup. Valid mentions are removed
    first; any Discord id left over was never going to notify anyone.
    """
    return _SNOWFLAKE.findall(_VALID_MENTION.sub(" ", mentions))


def allowed_mentions(mentions: str) -> dict:
    """Whitelist exactly what this message may ping.

    Discord pings nothing that `allowed_mentions` does not permit, so setting it
    explicitly means a stray character in configuration cannot mass-notify a server.
    With no mentions configured the message is barred from pinging anything at all.
    """
    if not mentions.strip():
        return {"parse": []}

    parse = ["users", "roles"]
    lowered = mentions.lower()
    # Only unlock the broadcast pings when the configuration actually asks for them.
    if "@everyone" in lowered or "@here" in lowered:
        parse.append("everyone")
    return {"parse": parse}


def build_payload(job: Job, username: str, mentions: str = "") -> dict:
    fields = []
    if job.company:
        fields.append({"name": "Company", "value": job.company, "inline": True})
    if job.location:
        fields.append({"name": "Location", "value": job.location, "inline": True})
    if is_known_ats(job.url):
        fields.append({"name": "Source", "value": "Known ATS", "inline": True})

    relevant = job.classification is Classification.RELEVANT
    unreadable = job.classification is Classification.UNKNOWN
    internship = job.role_type is RoleType.INTERNSHIP

    if not relevant:
        # Why it did not match is the useful part when triaging a review-channel link.
        fields.append({"name": "Why", "value": job.reason[:1024] or "unknown", "inline": False})

    title = job.title or "Job posting"
    if relevant:
        color = INTERNSHIP_EMBED_COLOR if internship else EMBED_COLOR
    elif unreadable:
        title, color = "Unreadable job posting", UNKNOWN_EMBED_COLOR
    elif job.near_miss:
        color = NEAR_MISS_EMBED_COLOR
    else:
        color = OTHER_EMBED_COLOR

    embed = {
        "title": title[:256],
        "url": job.url,
        "color": color,
        "description": job.url,
        "footer": {"text": f"From @{username} on Instagram"},
    }
    if fields:
        embed["fields"] = fields

    # Mentions must live in `content`. Inside an embed they render as blue text and
    # notify nobody, which would look correct and silently ping no one.
    if relevant:
        content = HEADLINE[job.role_type]
    elif unreadable:
        content = UNKNOWN_HEADLINE
    elif job.near_miss:
        content = NEAR_MISS_HEADLINE
    else:
        content = OTHER_HEADLINE
    if mentions.strip():
        content = f"{mentions.strip()} {content}"

    return {
        "content": content,
        "embeds": [embed],
        "allowed_mentions": allowed_mentions(mentions),
    }
