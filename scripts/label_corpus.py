#!/usr/bin/env python3
"""Label stored postings from the terminal.

Discord webhooks are send-only — they cannot read messages or reactions — so corrections
cannot be collected in the review channel without a real bot. At roughly 150 postings a
month this is cheaper, and it keeps the labels in the same database as the observations.

    python scripts/label_corpus.py                  # unlabelled, review destinations first
    python scripts/label_corpus.py --disagreements  # only where the classifier may be wrong
    python scripts/label_corpus.py --limit 20

At each posting: enter a role and an employment label, blank to accept the prediction,
`s` to skip, `q` to stop. Nothing is overwritten — corrections are appended.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from instagram_tracker.db import Database  # noqa: E402
from instagram_tracker.models import EmploymentClass, RoleClass  # noqa: E402

ROLES = [role.value for role in RoleClass]
EMPLOYMENTS = [employment.value for employment in EmploymentClass]


def _prompt(label: str, options: list[str], default: str | None) -> str | None:
    """Accept a full value or any unambiguous prefix; blank keeps the prediction."""
    while True:
        raw = input(f"  {label} {options} [{default}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("s", "q"):
            return raw
        matches = [option for option in options if option.startswith(raw)]
        if len(matches) == 1:
            return matches[0]
        print(f"  ? '{raw}' matched {matches or 'nothing'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument(
        "--disagreements",
        action="store_true",
        help="only postings sent to review, where a miss is most likely",
    )
    args = parser.parse_args()

    load_dotenv()
    target = os.getenv("DATABASE_URL") or os.getenv("DATABASE_PATH", "./data/job_monitor.db")

    with Database(target) as db:
        latest_run = {run["observation_id"]: run for run in db.all_classification_runs()}
        already_labelled = {c["observation_id"] for c in db.all_corrections()}

        pending = [
            observation
            for observation in db.all_observations()
            if observation["id"] not in already_labelled
        ]
        if args.disagreements:
            pending = [
                observation
                for observation in pending
                if (latest_run.get(observation["id"]) or {}).get("destination") == "review"
            ]

        if not pending:
            print("Nothing left to label.")
            return 0

        print(f"{len(pending)} unlabelled; showing up to {args.limit}. "
              "Blank accepts the prediction, 's' skips, 'q' quits.\n")

        labelled = 0
        for observation in pending[: args.limit]:
            run = latest_run.get(observation["id"], {})
            print(f"[{observation['id']}] {observation['title'] or '(no title)'}")
            print(f"  {observation['canonical_url']}")
            print(f"  source={observation['fetch_source']} "
                  f"predicted role={run.get('role')} ({run.get('role_confidence')}) "
                  f"employment={run.get('employment')} ({run.get('employment_confidence')}) "
                  f"-> {run.get('destination')}")

            role = _prompt("role", ROLES, run.get("role"))
            if role == "q":
                break
            if role == "s":
                print()
                continue

            employment = _prompt("employment", EMPLOYMENTS, run.get("employment"))
            if employment == "q":
                break
            if employment == "s":
                print()
                continue

            db.record_correction(observation["id"], role, employment)
            labelled += 1
            print(f"  saved: {role} / {employment}\n")

        print(f"Labelled {labelled} posting(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
