"""Persistence for stories, links, jobs and notifications.

Deduplication rests on two unique keys: ``processed_stories.story_id`` stops a Story
being processed twice, ``discovered_links.canonical_url`` stops a job being notified
twice even when it appears in several Stories.

Two backends share one interface, chosen by what you pass in:

* a filesystem path -> SQLite, used for local runs and the whole test suite
* a ``postgres://`` URL -> PostgreSQL, used on hosts with an ephemeral filesystem

The split exists because Render's free tier cannot attach a persistent disk. An empty
database is not merely a loss of history: startup seeding would mark every live Story as
seen-but-not-notified, silently swallowing any job posted just before the restart.

The SQL is almost entirely shared. `ON CONFLICT` is standard in both engines, so only
placeholders, a few column types and the migration probe differ.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

POSTGRES_SCHEMES = ("postgres://", "postgresql://")

_SQLITE_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS processed_stories (
        story_id   TEXT PRIMARY KEY,
        username   TEXT NOT NULL,
        posted_at  TEXT,
        seen_at    TEXT NOT NULL,
        notified   INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discovered_links (
        canonical_url TEXT PRIMARY KEY,
        original_url  TEXT NOT NULL,
        story_id      TEXT NOT NULL,
        discovered_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jobs (
        canonical_url  TEXT PRIMARY KEY,
        title          TEXT,
        company        TEXT,
        location       TEXT,
        classification TEXT NOT NULL,
        reason         TEXT,
        evaluated_at   TEXT NOT NULL,
        role_type      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_url TEXT NOT NULL UNIQUE,
        story_id      TEXT,
        sent_at       TEXT NOT NULL
    )
    """,
]

_POSTGRES_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS processed_stories (
        story_id   TEXT PRIMARY KEY,
        username   TEXT NOT NULL,
        posted_at  TEXT,
        seen_at    TEXT NOT NULL,
        notified   BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS discovered_links (
        canonical_url TEXT PRIMARY KEY,
        original_url  TEXT NOT NULL,
        story_id      TEXT NOT NULL,
        discovered_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jobs (
        canonical_url  TEXT PRIMARY KEY,
        title          TEXT,
        company        TEXT,
        location       TEXT,
        classification TEXT NOT NULL,
        reason         TEXT,
        evaluated_at   TEXT NOT NULL,
        role_type      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id            BIGSERIAL PRIMARY KEY,
        canonical_url TEXT NOT NULL UNIQUE,
        story_id      TEXT,
        sent_at       TEXT NOT NULL
    )
    """,
]


def is_postgres_target(target: object) -> bool:
    return str(target).startswith(POSTGRES_SCHEMES)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, target: Path | str) -> None:
        self.is_postgres = is_postgres_target(target)

        if self.is_postgres:
            # Imported lazily so a local SQLite run never needs the driver installed.
            import psycopg
            from psycopg.rows import dict_row

            self.conn = psycopg.connect(str(target), row_factory=dict_row)
        else:
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path)
            self.conn.row_factory = sqlite3.Row

        for statement in _POSTGRES_SCHEMA if self.is_postgres else _SQLITE_SCHEMA:
            self._execute(statement)
        self._migrate()
        self.conn.commit()

    # -- plumbing --------------------------------------------------------

    def _sql(self, sql: str) -> str:
        """Convert the shared `?` placeholders to psycopg's `%s`.

        Safe as a blind replace only because no statement here contains a literal
        `?` or `%`. Keep it that way.
        """
        return sql.replace("?", "%s") if self.is_postgres else sql

    def _execute(self, sql: str, params: tuple = ()):
        cursor = self.conn.cursor()
        cursor.execute(self._sql(sql), params)
        return cursor

    def _fetchone(self, sql: str, params: tuple = ()):
        return self._execute(sql, params).fetchone()

    def _migrate(self) -> None:
        """Bring an existing database up to the current schema.

        CREATE TABLE IF NOT EXISTS leaves already-created tables untouched, so columns
        added after a database exists have to be applied explicitly. Rows written before
        a column existed keep NULL, which readers treat as the historical default.
        """
        if self.is_postgres:
            self._execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS role_type TEXT")
            return

        # SQLite has no ADD COLUMN IF NOT EXISTS, so the column is probed first.
        columns = {row["name"] for row in self._execute("PRAGMA table_info(jobs)")}
        if "role_type" not in columns:
            self._execute("ALTER TABLE jobs ADD COLUMN role_type TEXT")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- stories ---------------------------------------------------------

    def is_story_processed(self, story_id: str) -> bool:
        return self._fetchone(
            "SELECT 1 AS hit FROM processed_stories WHERE story_id = ?", (story_id,)
        ) is not None

    def mark_story_processed(
        self,
        story_id: str,
        username: str,
        posted_at: datetime | None,
        notified: bool = False,
    ) -> None:
        self._execute(
            "INSERT INTO processed_stories "
            "(story_id, username, posted_at, seen_at, notified) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (story_id) DO NOTHING",
            (
                story_id,
                username,
                posted_at.isoformat() if posted_at else None,
                _now(),
                bool(notified),
            ),
        )
        self.conn.commit()

    def has_any_stories(self) -> bool:
        return self._fetchone("SELECT 1 AS hit FROM processed_stories LIMIT 1") is not None

    # -- links -----------------------------------------------------------

    def is_link_known(self, canonical_url: str) -> bool:
        return self._fetchone(
            "SELECT 1 AS hit FROM discovered_links WHERE canonical_url = ?", (canonical_url,)
        ) is not None

    def record_link(self, canonical_url: str, original_url: str, story_id: str) -> None:
        self._execute(
            "INSERT INTO discovered_links "
            "(canonical_url, original_url, story_id, discovered_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (canonical_url) DO NOTHING",
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
        role_type: str | None = None,
    ) -> None:
        self._execute(
            "INSERT INTO jobs (canonical_url, title, company, location, "
            "classification, reason, evaluated_at, role_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (canonical_url) DO UPDATE SET "
            "title = EXCLUDED.title, company = EXCLUDED.company, "
            "location = EXCLUDED.location, classification = EXCLUDED.classification, "
            "reason = EXCLUDED.reason, evaluated_at = EXCLUDED.evaluated_at, "
            "role_type = EXCLUDED.role_type",
            (
                canonical_url,
                title,
                company,
                location,
                classification,
                reason,
                _now(),
                role_type,
            ),
        )
        self.conn.commit()

    # -- notifications ---------------------------------------------------

    def is_notified(self, canonical_url: str) -> bool:
        return self._fetchone(
            "SELECT 1 AS hit FROM notifications WHERE canonical_url = ?", (canonical_url,)
        ) is not None

    def record_notification(self, canonical_url: str, story_id: str | None) -> None:
        self._execute(
            "INSERT INTO notifications (canonical_url, story_id, sent_at) "
            "VALUES (?, ?, ?) ON CONFLICT (canonical_url) DO NOTHING",
            (canonical_url, story_id, _now()),
        )
        self.conn.commit()

    # -- health ----------------------------------------------------------

    def counts(self) -> dict[str, int]:
        """Row counts per table, for the health endpoint."""
        tables = ("processed_stories", "discovered_links", "jobs", "notifications")
        result = {}
        for table in tables:
            row = self._fetchone(f"SELECT COUNT(*) AS n FROM {table}")
            result[table] = int(row["n"]) if row else 0
        return result
