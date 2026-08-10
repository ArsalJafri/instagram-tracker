"""SQLite persistence for stories, links, jobs and notifications.

Deduplication rests on two unique keys: ``processed_stories.story_id`` stops a Story
being processed twice, ``discovered_links.canonical_url`` stops a job being notified
twice even when it appears in several Stories.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_stories (
    story_id   TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    posted_at  TEXT,
    seen_at    TEXT NOT NULL,
    notified   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS discovered_links (
    canonical_url TEXT PRIMARY KEY,
    original_url  TEXT NOT NULL,
    story_id      TEXT NOT NULL,
    discovered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    canonical_url  TEXT PRIMARY KEY,
    title          TEXT,
    company        TEXT,
    location       TEXT,
    classification TEXT NOT NULL,
    reason         TEXT,
    evaluated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_url TEXT NOT NULL UNIQUE,
    story_id      TEXT,
    sent_at       TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- stories ---------------------------------------------------------

    def is_story_processed(self, story_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM processed_stories WHERE story_id = ?", (story_id,)
        ).fetchone()
        return row is not None

    def mark_story_processed(
        self,
        story_id: str,
        username: str,
        posted_at: datetime | None,
        notified: bool = False,
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO processed_stories "
            "(story_id, username, posted_at, seen_at, notified) VALUES (?, ?, ?, ?, ?)",
            (
                story_id,
                username,
                posted_at.isoformat() if posted_at else None,
                _now(),
                int(notified),
            ),
        )
        self.conn.commit()

    def has_any_stories(self) -> bool:
        row = self.conn.execute("SELECT 1 FROM processed_stories LIMIT 1").fetchone()
        return row is not None

    # -- links -----------------------------------------------------------

    def is_link_known(self, canonical_url: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM discovered_links WHERE canonical_url = ?", (canonical_url,)
        ).fetchone()
        return row is not None

    def record_link(self, canonical_url: str, original_url: str, story_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO discovered_links "
            "(canonical_url, original_url, story_id, discovered_at) VALUES (?, ?, ?, ?)",
            (canonical_url, original_url, story_id, _now()),
        )
        self.conn.commit()

    # -- jobs ------------------------------------------------------------

    def record_job(
        self,
        canonical_url: str,
        title: str | None,
        company: str | None,
        location: str | None,
        classification: str,
        reason: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO jobs "
            "(canonical_url, title, company, location, classification, reason, evaluated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (canonical_url, title, company, location, classification, reason, _now()),
        )
        self.conn.commit()

    # -- notifications ---------------------------------------------------

    def is_notified(self, canonical_url: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM notifications WHERE canonical_url = ?", (canonical_url,)
        ).fetchone()
        return row is not None

    def record_notification(self, canonical_url: str, story_id: str | None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO notifications (canonical_url, story_id, sent_at) "
            "VALUES (?, ?, ?)",
            (canonical_url, story_id, _now()),
        )
        self.conn.commit()
