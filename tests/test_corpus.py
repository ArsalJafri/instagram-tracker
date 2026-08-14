"""The corpus tables: immutable observations, append-only runs and corrections."""

from __future__ import annotations

import json

import pytest

from instagram_tracker.db import MAX_RAW_TEXT, Database


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "corpus.db") as database:
        yield database


def observe(db, url="https://x/1", title="Software Engineer Intern", text="java python"):
    return db.record_observation(
        canonical_url=url,
        story_id="story-1",
        title=title,
        raw_text=text,
        fetch_source="json-ld",
        company="Acme",
        location="NYC",
        declared_employment_type="INTERN",
    )


def test_an_observation_round_trips(db):
    observation_id = observe(db)

    rows = db.all_observations()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == observation_id
    assert row["title"] == "Software Engineer Intern"
    assert row["raw_text"] == "java python"
    assert row["fetch_source"] == "json-ld"
    assert row["declared_employment_type"] == "INTERN"
    assert row["observed_at"]


def test_raw_text_is_capped(db):
    observe(db, text="x" * (MAX_RAW_TEXT + 5000))

    assert len(db.all_observations()[0]["raw_text"]) == MAX_RAW_TEXT


def test_the_same_url_can_be_observed_more_than_once(db):
    # Not deduplicated: a re-fetch is a new observation, and the corpus wants both.
    first = observe(db)
    second = observe(db)

    assert first != second
    assert len(db.all_observations()) == 2


def test_many_runs_may_point_at_one_observation(db):
    # The reason observations and runs are separate tables: replaying a new classifier
    # version over frozen input is how versions get compared.
    observation_id = observe(db)

    db.record_classification_run(
        observation_id, "scorer-1", "software", 0.9, "intern", 0.8,
        "internship", "scorer", input_quality="rich", evidence=["software:swe"],
    )
    db.record_classification_run(
        observation_id, "scorer-2", "software", 0.95, "intern", 0.99,
        "internship", "rule", input_quality="rich", rule="structured-employment-intern",
    )

    runs = db.all_classification_runs()
    assert len(runs) == 2
    assert {run["classifier_version"] for run in runs} == {"scorer-1", "scorer-2"}
    assert all(run["observation_id"] == observation_id for run in runs)
    assert len(db.all_observations()) == 1


def test_a_run_records_what_decided_it(db):
    observation_id = observe(db)
    db.record_classification_run(
        observation_id, "scorer-1", "software", 0.87, "intern", 0.93,
        "internship", "rule", input_quality="poor", rule="return-to-school",
        evidence=["software:software+builds", "intern:intern"],
    )

    run = db.all_classification_runs()[0]
    assert run["destination"] == "internship"
    assert run["classification_source"] == "rule"
    assert run["rule"] == "return-to-school"
    assert run["input_quality"] == "poor"
    assert json.loads(run["evidence"]) == [
        "software:software+builds",
        "intern:intern",
    ]
    assert run["role_confidence"] == pytest.approx(0.87)


def test_evidence_defaults_to_an_empty_list(db):
    observation_id = observe(db)
    db.record_classification_run(
        observation_id, "scorer-1", "other", 0.0, "unknown", 0.0, "review", "scorer"
    )

    assert json.loads(db.all_classification_runs()[0]["evidence"]) == []


def test_corrections_are_append_only(db):
    observation_id = observe(db)

    db.record_correction(observation_id, "software", "intern")
    db.record_correction(observation_id, "data", "intern", note="on reflection")

    corrections = db.all_corrections()
    assert len(corrections) == 2
    # The first label survives the second: nothing is overwritten.
    assert corrections[0]["role_label"] == "software"
    assert corrections[1]["role_label"] == "data"
    assert corrections[1]["note"] == "on reflection"


def test_counts_include_the_corpus_tables(db):
    observation_id = observe(db)
    db.record_classification_run(
        observation_id, "scorer-1", "software", 0.9, "intern", 0.9,
        "internship", "scorer",
    )
    db.record_correction(observation_id, "software", "intern")

    counts = db.counts()
    assert counts["job_observations"] == 1
    assert counts["classification_runs"] == 1
    assert counts["corrections"] == 1


def test_the_corpus_starts_empty(db):
    assert db.all_observations() == []
    assert db.all_classification_runs() == []
    assert db.all_corrections() == []
