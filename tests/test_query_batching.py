"""Story deduplication asks the database once per poll, not once per Story.

Free when the database was a local file; ~46,000 queries a day once it moved to a hosted
Postgres, since every check became a network round trip.
"""

from __future__ import annotations

from instagram_tracker.db import Database


def test_it_returns_only_the_ids_already_processed(tmp_path):
    with Database(tmp_path / "batch.db") as db:
        db.mark_story_processed("a", "zero2sudo", None)
        db.mark_story_processed("c", "zero2sudo", None)

        assert db.known_story_ids(["a", "b", "c", "d"]) == {"a", "c"}


def test_an_empty_batch_asks_nothing(tmp_path):
    with Database(tmp_path / "empty.db") as db:
        calls = []
        original = db._execute
        db._execute = lambda sql, params=(): (calls.append(sql), original(sql, params))[1]

        assert db.known_story_ids([]) == set()
        assert calls == []


def test_a_fresh_database_knows_nothing(tmp_path):
    with Database(tmp_path / "fresh.db") as db:
        assert db.known_story_ids(["a", "b"]) == set()


def test_one_query_covers_the_whole_batch(tmp_path):
    with Database(tmp_path / "count.db") as db:
        for story_id in "abcdefghij":
            db.mark_story_processed(story_id, "zero2sudo", None)

        calls = []
        original = db._execute
        db._execute = lambda sql, params=(): (calls.append(sql), original(sql, params))[1]

        known = db.known_story_ids(list("abcdefghij") + ["new1", "new2"])

        assert known == set("abcdefghij")
        assert len(calls) == 1, "the batch must cost exactly one round trip"
