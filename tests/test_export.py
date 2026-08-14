"""Joining the three corpus tables back into one record per posting."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_corpus import build_rows  # noqa: E402

from instagram_tracker.db import Database  # noqa: E402


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "corpus.db") as database:
        yield database


def test_an_observation_with_no_run_still_exports(db):
    db.record_observation("https://x/1", "s1", "Software Engineer", "text", "json-ld")

    rows = build_rows(db)
    assert len(rows) == 1
    assert rows[0]["runs"] == []
    assert rows[0]["role_label"] is None


def test_every_run_is_carried_so_versions_can_be_compared(db):
    observation_id = db.record_observation(
        "https://x/1", "s1", "Software Engineer Intern", "text", "json-ld"
    )
    db.record_classification_run(
        observation_id, "scorer-1", "other", 0.2, "unknown", 0.1, "review", "scorer"
    )
    db.record_classification_run(
        observation_id, "scorer-2", "software", 0.9, "intern", 0.9,
        "internship", "scorer", evidence=["software:swe"],
    )

    row = build_rows(db)[0]
    assert [run["classifier_version"] for run in row["runs"]] == ["scorer-1", "scorer-2"]
    assert row["runs"][1]["evidence"] == ["software:swe"]
    # The whole point of the split: one frozen input, two verdicts to compare.
    assert row["runs"][0]["destination"] != row["runs"][1]["destination"]


def test_the_newest_correction_is_the_label(db):
    observation_id = db.record_observation("https://x/1", "s1", "Analyst", "text", "slug")
    db.record_correction(observation_id, "data", "intern")
    db.record_correction(observation_id, "software", "intern")

    row = build_rows(db)[0]
    assert row["role_label"] == "software"
    assert row["employment_label"] == "intern"


def test_rows_carry_what_a_model_would_train_on(db):
    observation_id = db.record_observation(
        "https://x/1", "s1", "Software Engineer Intern", "java python", "json-ld",
        company="Acme", declared_employment_type="INTERN",
    )
    db.record_correction(observation_id, "software", "intern")

    row = build_rows(db)[0]
    assert row["title"] == "Software Engineer Intern"
    assert row["raw_text"] == "java python"
    assert row["fetch_source"] == "json-ld"
    assert row["declared_employment_type"] == "INTERN"
    assert row["role_label"] == "software"


def test_an_empty_corpus_exports_nothing(db):
    assert build_rows(db) == []
