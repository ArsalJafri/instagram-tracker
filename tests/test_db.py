from __future__ import annotations

from datetime import datetime, timezone

import pytest

from instagram_tracker.db import Database


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "test.db") as database:
        yield database


def test_story_dedup(db):
    assert db.has_any_stories() is False
    assert db.is_story_processed("1") is False

    db.mark_story_processed("1", "zero2sudo", datetime.now(timezone.utc))

    assert db.is_story_processed("1") is True
    assert db.has_any_stories() is True


def test_marking_the_same_story_twice_is_harmless(db):
    db.mark_story_processed("1", "zero2sudo", None)
    db.mark_story_processed("1", "zero2sudo", None)
    assert db.is_story_processed("1") is True


def test_link_dedup_is_by_canonical_url(db):
    assert db.is_link_known("https://example.com/a") is False
    db.record_link("https://example.com/a", "https://l.instagram.com/?u=x", "1")
    assert db.is_link_known("https://example.com/a") is True

    # A second Story linking to the same posting must not re-register it.
    db.record_link("https://example.com/a", "https://l.instagram.com/?u=y", "2")
    row = db.conn.execute(
        "SELECT story_id FROM discovered_links WHERE canonical_url = ?",
        ("https://example.com/a",),
    ).fetchone()
    assert row["story_id"] == "1"


def test_notification_dedup(db):
    assert db.is_notified("https://example.com/a") is False
    db.record_notification("https://example.com/a", "1")
    db.record_notification("https://example.com/a", "2")
    assert db.is_notified("https://example.com/a") is True

    count = db.conn.execute("SELECT COUNT(*) AS n FROM notifications").fetchone()["n"]
    assert count == 1


def test_job_record_is_upserted(db):
    db.record_job("https://example.com/a", "SWE", "Acme", "Seattle", "relevant", "why")
    db.record_job("https://example.com/a", "SWE II", "Acme", "Seattle", "not_relevant", "why not")

    rows = db.conn.execute("SELECT * FROM jobs").fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "SWE II"
    assert rows[0]["classification"] == "not_relevant"


def test_database_creates_its_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "job_monitor.db"
    with Database(path):
        pass
    assert path.exists()


# -- connection resilience ----------------------------------------------


def test_a_dropped_connection_is_transparently_reconnected(tmp_path):
    """A remote Postgres will drop; without this the tracker runs on doing nothing."""
    from instagram_tracker.db import Database

    with Database(tmp_path / "drop.db") as db:
        db.record_link("https://example.com/a", "https://example.com/a", "s1")

        db.conn.close()  # simulate the connection dying underneath us

        assert db.is_link_known("https://example.com/a") is True
        db.record_link("https://example.com/b", "https://example.com/b", "s2")
        assert db.is_link_known("https://example.com/b") is True


def test_a_real_sql_error_is_not_swallowed_by_the_retry(tmp_path):
    import pytest

    from instagram_tracker.db import Database

    with Database(tmp_path / "err.db") as db:
        with pytest.raises(Exception):
            db._execute("SELECT * FROM table_that_does_not_exist")
