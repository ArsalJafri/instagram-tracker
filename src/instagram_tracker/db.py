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

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

POSTGRES_SCHEMES = ("postgres://", "postgresql://")

# The corpus tables. Deliberately separate from `jobs`, which updates in place and so
# cannot answer "what did the classifier think at the time".
#
# `job_observations` is immutable: the posting exactly as it was read. `raw_text` is
# stored unnormalized on purpose — normalization rules will change, and a corpus holding
# only the normalized form is silently invalidated by every one of those changes.
#
# Many runs may point at one observation. That is the point: a new classifier version can
# be replayed over the frozen corpus and compared against the old one on identical input.
_OBSERVATIONS = """
    CREATE TABLE IF NOT EXISTS job_observations (
        id                       {pk},
        canonical_url            TEXT NOT NULL,
        story_id                 TEXT,
        title                    TEXT,
        raw_text                 TEXT,
        fetch_source             TEXT,
        company                  TEXT,
        location                 TEXT,
        declared_employment_type TEXT,
        observed_at              TEXT NOT NULL
    )
"""

_RUNS = """
    CREATE TABLE IF NOT EXISTS classification_runs (
        id                    {pk},
        observation_id        {fk} NOT NULL,
        classifier_version    TEXT NOT NULL,
        role                  TEXT NOT NULL,
        role_confidence       {real} NOT NULL,
        employment            TEXT NOT NULL,
        employment_confidence {real} NOT NULL,
        destination           TEXT NOT NULL,
        classification_source TEXT NOT NULL,
        input_quality         TEXT,
        rule                  TEXT,
        evidence              TEXT,
        created_at            TEXT NOT NULL
    )
"""

_CORRECTIONS = """
    CREATE TABLE IF NOT EXISTS corrections (
        id              {pk},
        observation_id  {fk} NOT NULL,
        role_label      TEXT,
        employment_label TEXT,
        note            TEXT,
        corrected_at    TEXT NOT NULL
    )
"""

_OBSERVATION_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_observations_url ON job_observations (canonical_url)
"""

# Stored text is capped so a pathological page cannot bloat a row. The fetcher already
# truncates at 4000; this is the backstop.
MAX_RAW_TEXT = 8000

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
    _OBSERVATIONS.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT"),
    _RUNS.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT", fk="INTEGER", real="REAL"),
    _CORRECTIONS.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT", fk="INTEGER"),
    _OBSERVATION_INDEX,
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
    _OBSERVATIONS.format(pk="BIGSERIAL PRIMARY KEY"),
    _RUNS.format(pk="BIGSERIAL PRIMARY KEY", fk="BIGINT", real="DOUBLE PRECISION"),
    _CORRECTIONS.format(pk="BIGSERIAL PRIMARY KEY", fk="BIGINT"),
    _OBSERVATION_INDEX,
]


def is_postgres_target(target: object) -> bool:
    return str(target).startswith(POSTGRES_SCHEMES)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, target: Path | str) -> None:
        self.target = target
        self.is_postgres = is_postgres_target(target)
        self.conn = self._connect()

        for statement in _POSTGRES_SCHEMA if self.is_postgres else _SQLITE_SCHEMA:
            self._execute(statement)
        self._migrate()
        self.conn.commit()

    # -- connection ------------------------------------------------------

    def _connect(self):
        if self.is_postgres:
            # Imported lazily so a local SQLite run never needs the driver installed.
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(str(self.target), row_factory=dict_row)

        path = Path(self.target)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    @property
    def _connection_errors(self) -> tuple[type[BaseException], ...]:
        if self.is_postgres:
            import psycopg

            return (psycopg.OperationalError, psycopg.InterfaceError)
        return (sqlite3.OperationalError, sqlite3.ProgrammingError)

    def _reconnect(self) -> None:
        try:
            self.conn.close()
        except Exception:  # already broken; nothing useful to do
            pass
        self.conn = self._connect()

    # -- plumbing --------------------------------------------------------

    def _sql(self, sql: str) -> str:
        """Convert the shared `?` placeholders to psycopg's `%s`.

        Safe as a blind replace only because no statement here contains a literal
        `?` or `%`. Keep it that way.
        """
        return sql.replace("?", "%s") if self.is_postgres else sql

    def _execute(self, sql: str, params: tuple = ()):
        """Run a statement, reconnecting once if the connection has died.

        A local SQLite file never drops, but a hosted Postgres will — on restart, idle
        timeout or any network blip. Without this the connection stays broken forever:
        the poller survives, logs an error every tick, and silently stops persisting or
        notifying anything, which looks exactly like a quiet week from the account.
        """
        try:
            return self._run(sql, params)
        except self._connection_errors as exc:
            log.warning("Database connection failed (%s); reconnecting", exc)
            self._reconnect()
            # A second failure is a real error and is allowed to propagate.
            return self._run(sql, params)

    def _run(self, sql: str, params: tuple = ()):
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

    def known_story_ids(self, story_ids: list[str]) -> set[str]:
        """Which of these Stories have already been processed, in one round trip.

        The obvious loop asks once per Story per poll. Against a local SQLite file that
        was a function call; against a hosted Postgres it is a network round trip, and
        with ~32 live Stories polled every minute it came to roughly 46,000 queries a
        day. This asks once.
        """
        if not story_ids:
            return set()

        placeholders = ", ".join("?" for _ in story_ids)
        rows = self._execute(
            f"SELECT story_id FROM processed_stories WHERE story_id IN ({placeholders})",
            tuple(story_ids),
        )
        return {row["story_id"] for row in rows}

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

    # -- corpus ----------------------------------------------------------

    def record_observation(
        self,
        canonical_url: str,
        story_id: str | None,
        title: str | None,
        raw_text: str,
        fetch_source: str,
        company: str | None = None,
        location: str | None = None,
        declared_employment_type: str | None = None,
    ) -> int:
        """Store a posting exactly as it was read. Never updated afterwards."""
        params = (
            canonical_url,
            story_id,
            title,
            (raw_text or "")[:MAX_RAW_TEXT],
            fetch_source,
            company,
            location,
            declared_employment_type,
            _now(),
        )
        sql = (
            "INSERT INTO job_observations (canonical_url, story_id, title, raw_text, "
            "fetch_source, company, location, declared_employment_type, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        observation_id = self._insert_returning_id(sql, params)
        self.conn.commit()
        return observation_id

    def record_classification_run(
        self,
        observation_id: int,
        classifier_version: str,
        role: str,
        role_confidence: float,
        employment: str,
        employment_confidence: float,
        destination: str,
        classification_source: str,
        input_quality: str | None = None,
        rule: str | None = None,
        evidence: list[str] | None = None,
    ) -> int:
        """Append one classifier verdict. Several may point at one observation."""
        sql = (
            "INSERT INTO classification_runs (observation_id, classifier_version, role, "
            "role_confidence, employment, employment_confidence, destination, "
            "classification_source, input_quality, rule, evidence, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        run_id = self._insert_returning_id(
            sql,
            (
                observation_id,
                classifier_version,
                role,
                float(role_confidence),
                employment,
                float(employment_confidence),
                destination,
                classification_source,
                input_quality,
                rule,
                json.dumps(evidence or []),
                _now(),
            ),
        )
        self.conn.commit()
        return run_id

    def record_correction(
        self,
        observation_id: int,
        role_label: str | None,
        employment_label: str | None,
        note: str | None = None,
    ) -> None:
        """Append a human label. The expensive, irreplaceable part of the corpus."""
        self._execute(
            "INSERT INTO corrections (observation_id, role_label, employment_label, "
            "note, corrected_at) VALUES (?, ?, ?, ?, ?)",
            (observation_id, role_label, employment_label, note, _now()),
        )
        self.conn.commit()

    def _insert_returning_id(self, sql: str, params: tuple) -> int:
        """The one place the two engines genuinely disagree about inserts."""
        if self.is_postgres:
            row = self._execute(f"{sql} RETURNING id", params).fetchone()
            return int(row["id"])
        return int(self._execute(sql, params).lastrowid)

    def all_observations(self) -> list[dict]:
        return [dict(row) for row in self._execute(
            "SELECT * FROM job_observations ORDER BY id"
        )]

    def all_classification_runs(self) -> list[dict]:
        return [dict(row) for row in self._execute(
            "SELECT * FROM classification_runs ORDER BY id"
        )]

    def all_corrections(self) -> list[dict]:
        return [dict(row) for row in self._execute(
            "SELECT * FROM corrections ORDER BY id"
        )]

    # -- health ----------------------------------------------------------

    def counts(self) -> dict[str, int]:
        """Row counts per table, for the health endpoint."""
        tables = (
            "processed_stories",
            "discovered_links",
            "jobs",
            "notifications",
            "job_observations",
            "classification_runs",
            "corrections",
        )
        result = {}
        for table in tables:
            row = self._fetchone(f"SELECT COUNT(*) AS n FROM {table}")
            result[table] = int(row["n"]) if row else 0
        return result
