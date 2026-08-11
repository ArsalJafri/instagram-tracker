"""Discord webhook notifications."""

from __future__ import annotations

import logging

import requests

from .classifier import is_known_ats
from .models import Job, RoleType

log = logging.getLogger(__name__)

EMBED_COLOR = 0x2ECC71
INTERNSHIP_EMBED_COLOR = 0x3498DB

HEADLINE = {
    RoleType.NEW_GRAD: "New entry-level software role",
    RoleType.INTERNSHIP: "New software internship",
}


class DiscordNotifier:
    """Posts to Discord, routing internships to their own webhook when one is set.

    If no internship webhook is configured, internships fall back to the main one rather
    than being dropped — a missing channel should degrade the routing, not the alerting.
    """

    def __init__(
        self,
        webhook_url: str,
        internship_webhook_url: str = "",
        timeout: int = 15,
        session: requests.Session | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.internship_webhook_url = internship_webhook_url
        self.timeout = timeout
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url or self.internship_webhook_url)

    def webhook_for(self, job: Job) -> str:
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
                json=build_payload(job, username),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            log.error("Discord notification failed for %s: %s", job.url, exc)
            return False
        log.info("Notified Discord (%s) about %s", job.role_type.value, job.url)
        return True


def build_payload(job: Job, username: str) -> dict:
    fields = []
    if job.company:
        fields.append({"name": "Company", "value": job.company, "inline": True})
    if job.location:
        fields.append({"name": "Location", "value": job.location, "inline": True})
    if is_known_ats(job.url):
        fields.append({"name": "Source", "value": "Known ATS", "inline": True})

    internship = job.role_type is RoleType.INTERNSHIP
    embed = {
        "title": (job.title or "Job posting")[:256],
        "url": job.url,
        "color": INTERNSHIP_EMBED_COLOR if internship else EMBED_COLOR,
        "description": job.url,
        "footer": {"text": f"From @{username} on Instagram"},
    }
    if fields:
        embed["fields"] = fields

    return {"content": HEADLINE[job.role_type], "embeds": [embed]}
