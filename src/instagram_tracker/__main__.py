"""Entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import Config
from .db import Database
from .heartbeat import Heartbeat
from .pipeline import build_pipeline
from .poller import Poller


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
    if not config.discord_webhook_url:
        logging.warning("DISCORD_WEBHOOK_URL is not set; relevant jobs will be logged only")

    if not config.heartbeat_url:
        logging.warning("HEARTBEAT_URL is not set; a stopped tracker will fail silently")

    with Database(config.database_path) as db:
        pipeline = build_pipeline(config, db)
        poller = Poller(
            pipeline,
            config.poll_interval_seconds,
            heartbeat=Heartbeat(config.heartbeat_url),
        )
        if args.once:
            poller.tick()
            return 0
        try:
            poller.run_forever()
        except KeyboardInterrupt:
            logging.info("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
