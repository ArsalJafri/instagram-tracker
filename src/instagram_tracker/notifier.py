"""Discord webhook notifications."""

from __future__ import annotations

import logging

import requests

from .classifier import is_known_ats
from .models import Job

log = logging.getLogger(__name__)

EMBED_COLOR = 0x2ECC71


class DiscordNotifier:
    def __init__(
        self,
        webhook_url: str,
        timeout: int = 15,
        session: requests.Session | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def notify(self, job: Job, username: str) -> bool:
        if not self.enabled:
            log.warning("DISCORD_WEBHOOK_URL is not set; skipping notification for %s", job.url)
            return False
        try:
            response = self.session.post(
                self.webhook_url,
                json=build_payload(job, username),
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            log.error("Discord notification failed for %s: %s", job.url, exc)
            return False
        log.info("Notified Discord about %s", job.url)
        return True


def build_payload(job: Job, username: str) -> dict:
    fields = []
    if job.company:
        fields.append({"name": "Company", "value": job.company, "inline": True})
    if job.location:
        fields.append({"name": "Location", "value": job.location, "inline": True})
    if is_known_ats(job.url):
        fields.append({"name": "Source", "value": "Known ATS", "inline": True})

    embed = {
        "title": (job.title or "Job posting")[:256],
        "url": job.url,
        "color": EMBED_COLOR,
        "description": job.url,
        "footer": {"text": f"From @{username} on Instagram"},
    }
    if fields:
        embed["fields"] = fields

    return {"content": "New entry-level software role", "embeds": [embed]}
