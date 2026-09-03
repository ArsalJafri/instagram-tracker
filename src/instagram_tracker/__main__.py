"""Entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import Config, deprecated_settings_in_use
from .db import Database, is_postgres_target
from .health import HealthState, serve_in_background
from .heartbeat import Heartbeat
from .notifier import malformed_mentions
from .pipeline import build_pipeline
from .poller import Poller, QuietHours


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="instagram-tracker")
    parser.add_argument("--once", action="store_true", help="run a single poll and exit")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    config = Config.from_env()
    for legacy, current in deprecated_settings_in_use():
        logging.warning(
            "%s was renamed to %s; the old name still works but will be removed. "
            "Rename it in the environment to silence this.",
            legacy,
            current,
        )

    if not config.discord_webhook_url:
        logging.warning(
            "DISCORD_NEW_GRAD_WEBHOOK_URL is not set; relevant jobs will be logged only"
        )
    if not config.discord_internship_webhook_url:
        logging.warning(
            "DISCORD_INTERNSHIP_WEBHOOK_URL is not set; internships will go to the main channel"
        )

    for name, value in (
        ("DISCORD_NEW_GRAD_MENTIONS", config.discord_mentions),
        ("DISCORD_INTERNSHIP_MENTIONS", config.discord_internship_mentions),
    ):
        bare = malformed_mentions(value)
        if bare:
            logging.warning(
                "%s contains bare id(s) %s — Discord renders these as plain text and "
                "pings nobody. Wrap them: <@&ID> for a role, <@ID> for a user.",
                name,
                ", ".join(bare),
            )

    if not config.heartbeat_url:
        logging.warning("HEARTBEAT_URL is not set; a stopped tracker will fail silently")

    target = config.database_target
    logging.info("State: %s", "PostgreSQL" if is_postgres_target(target) else f"SQLite {target}")

    with Database(target) as db:
        # Tolerate a few missed polls before reporting unhealthy, with a floor so a
        # short interval does not make the check hair-trigger.
        health = HealthState(
            stale_after_seconds=max(config.poll_interval_seconds * 5, 300),
            channels={
                "new_grad": bool(config.discord_webhook_url),
                "internship": bool(config.discord_internship_webhook_url),
                "review": bool(config.discord_unknown_webhook_url),
            },
        )
        pipeline = build_pipeline(config, db, health=health)
        poller = Poller(
            pipeline,
            config.poll_interval_seconds,
            heartbeat=Heartbeat(config.heartbeat_url),
            health=health,
            quiet_hours=QuietHours(
                config.poll_timezone,
                config.quiet_hour_start,
                config.quiet_hour_end,
                config.quiet_poll_interval_seconds,
            ),
        )
        if args.once:
            poller.tick()
            return 0

        # PORT is set by the host when the process must answer HTTP to stay alive.
        # Locally it is absent and no socket is opened.
        port = os.getenv("PORT")
        if port:
            serve_in_background(health, int(port))
        try:
            poller.run_forever()
        except KeyboardInterrupt:
            logging.info("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
