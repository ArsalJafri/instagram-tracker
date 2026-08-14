#!/usr/bin/env python3
"""Export the corpus to JSONL.

The corpus lives in one free-tier Postgres with about a day of point-in-time history.
The raw text is replaceable in principle; the hand-applied labels are not, and they are
the expensive part. This script is the durability story — run it periodically and keep
the output somewhere that is not that one database.

    python scripts/export_corpus.py --out corpus.jsonl
    python scripts/export_corpus.py --labelled-only --out training.jsonl

Reads DATABASE_URL when set, otherwise DATABASE_PATH, matching the tracker itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from instagram_tracker.db import Database  # noqa: E402


def build_rows(db: Database) -> list[dict]:
    """Join the three tables into one record per observation."""
    runs: dict[int, list[dict]] = {}
    for run in db.all_classification_runs():
        runs.setdefault(run["observation_id"], []).append(run)

    corrections: dict[int, list[dict]] = {}
    for correction in db.all_corrections():
        corrections.setdefault(correction["observation_id"], []).append(correction)

    rows = []
    for observation in db.all_observations():
        observation_id = observation["id"]
        # The newest correction wins; the older ones stay in the table as history.
        labels = corrections.get(observation_id, [])
        rows.append(
            {
                "observation_id": observation_id,
                "canonical_url": observation["canonical_url"],
                "title": observation["title"],
                "raw_text": observation["raw_text"],
                "fetch_source": observation["fetch_source"],
                "company": observation["company"],
                "declared_employment_type": observation["declared_employment_type"],
                "observed_at": observation["observed_at"],
                "runs": [
                    {
                        "classifier_version": run["classifier_version"],
                        "role": run["role"],
                        "role_confidence": run["role_confidence"],
                        "employment": run["employment"],
                        "employment_confidence": run["employment_confidence"],
                        "destination": run["destination"],
                        "classification_source": run["classification_source"],
                        "rule": run["rule"],
                        "evidence": json.loads(run["evidence"] or "[]"),
                    }
                    for run in runs.get(observation_id, [])
                ],
                "role_label": labels[-1]["role_label"] if labels else None,
                "employment_label": labels[-1]["employment_label"] if labels else None,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="write here instead of stdout")
    parser.add_argument(
        "--labelled-only",
        action="store_true",
        help="only observations a human has labelled — the training set",
    )
    args = parser.parse_args()

    load_dotenv()
    target = os.getenv("DATABASE_URL") or os.getenv("DATABASE_PATH", "./data/job_monitor.db")

    with Database(target) as db:
        rows = build_rows(db)

    if args.labelled_only:
        rows = [row for row in rows if row["role_label"] or row["employment_label"]]

    lines = "\n".join(json.dumps(row) for row in rows)
    if args.out:
        args.out.write_text(lines + ("\n" if lines else ""))
        labelled = sum(1 for row in rows if row["role_label"])
        print(f"Wrote {len(rows)} observations ({labelled} labelled) to {args.out}")
    else:
        print(lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
