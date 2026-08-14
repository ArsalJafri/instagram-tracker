"""A minimal health endpoint, so the tracker can deploy where only web services run.

Render's free tier has no background workers — only web services, which must bind a port
and which spin down after 15 minutes without traffic. This server is what makes the
poller deployable there, and what an external pinger (UptimeRobot) hits to keep it awake.

Deliberately stdlib: the tracker needs a socket that answers 200, not a web framework.

The response doubles as a status page, so a ping also tells you whether polling is
actually happening rather than merely whether the process is alive.
"""

from __future__ import annotations

import collections
import json
import logging
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger(__name__)


DEFAULT_STALE_AFTER_SECONDS = 300


class HealthState:
    """Shared between the poller and the HTTP thread.

    A process that is up but has stopped polling is the failure worth catching, so this
    goes unhealthy when no poll has succeeded for `stale_after_seconds`. That turns an
    ordinary uptime check into a real liveness check: an external pinger watching only
    the status code still notices a stalled poller, with no second service to configure.

    Before the first poll the clock runs from startup, so a cold deploy has a grace
    period rather than failing its host's health check on the first request.
    """

    def __init__(
        self,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        channels: dict | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.stale_after_seconds = stale_after_seconds
        # Which destinations are configured, as booleans — never the webhook URLs.
        # "Is the review channel even set up?" took four rounds of guessing to answer.
        self.channels = channels or {}
        # The last handful of link decisions, so "why didn't X send?" is one request
        # rather than a reconstruction from Instagram's feed and the git log.
        self.recent: collections.deque = collections.deque(maxlen=15)
        self.started_at = datetime.now(timezone.utc)
        self.last_poll_at: datetime | None = None
        self.last_error: str | None = None
        self.polls = 0
        self.notifications = 0
        # Corpus capture is best effort and swallows its own errors, so without a counter
        # a broken write is invisible — the exact failure mode this project keeps hitting.
        # The Postgres insert path differs from SQLite's, so it needs to be observable.
        self.corpus_recorded = 0
        self.corpus_failures = 0

    def record_poll(self, sent: int) -> None:
        with self._lock:
            self.last_poll_at = datetime.now(timezone.utc)
            self.last_error = None
            self.polls += 1
            self.notifications += sent

    def record_decision(self, url: str, title: str | None, verdict: str, sent_to: str) -> None:
        with self._lock:
            self.recent.appendleft(
                {
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "title": title,
                    "verdict": verdict,
                    "sent_to": sent_to,
                    "url": url,
                }
            )

    def record_corpus(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.corpus_recorded += 1
            else:
                self.corpus_failures += 1

    def record_error(self, message: str) -> None:
        with self._lock:
            self.last_error = message

    def snapshot(self) -> dict:
        with self._lock:
            now = datetime.now(timezone.utc)
            since = (now - self.last_poll_at).total_seconds() if self.last_poll_at else None
            # Before the first poll, measure from startup so a cold start is not stalled.
            reference = self.last_poll_at or self.started_at
            stalled = (now - reference).total_seconds() > self.stale_after_seconds
            return {
                "status": "stalled" if stalled else "ok",
                "uptime_seconds": round((now - self.started_at).total_seconds()),
                "polls": self.polls,
                "notifications_sent": self.notifications,
                "last_poll_at": self.last_poll_at.isoformat() if self.last_poll_at else None,
                "seconds_since_last_poll": round(since) if since is not None else None,
                "stale_after_seconds": self.stale_after_seconds,
                "last_error": self.last_error,
                "channels": self.channels,
                "corpus": {
                    "recorded": self.corpus_recorded,
                    "failed": self.corpus_failures,
                },
                "recent": list(self.recent),
            }

    def is_healthy(self) -> bool:
        return self.snapshot()["status"] == "ok"


def _handler_for(state: HealthState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            self._respond(with_body=True)

        def do_HEAD(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            """Answer HEAD as well as GET.

            BaseHTTPRequestHandler replies 501 to any method without a handler, and
            uptime monitors send HEAD by default because it avoids transferring a body.
            Without this the endpoint looks permanently down to the very thing watching
            it — which is exactly what happened once deployed.
            """
            self._respond(with_body=False)

        def _respond(self, with_body: bool) -> None:
            snapshot = state.snapshot()
            body = json.dumps(snapshot, indent=2).encode()
            # 503 when polling has stalled, so an uptime check that only reads the
            # status code still catches a process that is alive but doing nothing.
            self.send_response(200 if snapshot["status"] == "ok" else 503)
            self.send_header("Content-Type", "application/json")
            # Sent for HEAD too: the headers must match what GET would return.
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if with_body:
                self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            """Silence per-request logging; a pinger every few minutes would flood it."""

    return Handler


def serve_in_background(state: HealthState, port: int) -> HTTPServer:
    """Start the health server on a daemon thread and return it."""
    server = HTTPServer(("0.0.0.0", port), _handler_for(state))
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    log.info("Health endpoint listening on port %d", port)
    return server
