#!/usr/bin/env python3

"""
Additive schema: candidate_job_search_matches -- the real fix for the
same-job/multiple-search collision this project's own audit found.

Root cause (documented dependency investigation, see this migration's
companion report data/reports/same_job_multi_search_schema_decision_
2026-09-25.md): candidate_job_matches has PRIMARY KEY (candidate_id,
job_id) and conflates two genuinely different concerns into one row:

  (a) candidate+job-scoped facts that are correctly singular per pair
      -- candidate_status (a human's ONE application/shortlist
      decision about ONE job; MULTIPLE statuses for the same
      candidate+job would be nonsensical). This table's own PRIMARY
      KEY is exactly right for this and is UNCHANGED here.

  (b) search-run-scoped SCORING OUTPUT -- fit_score, priority,
      experience_eligibility, resume_id/resume_variant/profile_version
      -- which a DIFFERENT search (potentially pinned to a DIFFERENT
      profile_version, see migrate_v8) can legitimately compute
      DIFFERENTLY for the SAME job (proven directly in
      test_resume_profile_traceability.py). Because (b) shared (a)'s
      single-row-per-(candidate,job) keying, a later search's write
      silently overwrote an earlier search's own scoring snapshot,
      including which run "owns" that job for results-scoping
      purposes (api/results_store.py's _job_ids_for_search()) -- an
      earlier search could lose a job from its OWN results view the
      moment a different search for the same candidate matched it.

This table holds ONLY (b): one row per (search_run_id, job_id), never
overwritten by a different run. candidate_job_matches is NOT
restructured and keeps holding the "latest snapshot across all of a
candidate's activity" (used by the dashboard and the full Excel
report, which intentionally want candidate-wide latest state, not one
search's own view) -- this is purely additive, a second table, not a
migration of the first.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _create_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_job_search_matches (
            search_run_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            fit_score INTEGER,
            priority TEXT,
            experience_eligibility TEXT,
            location_match TEXT,
            skill_match_json TEXT,
            matched_skills_json TEXT,
            missing_skills_json TEXT,
            resume_id TEXT,
            resume_variant TEXT,
            profile_version INTEGER,
            created_at TEXT NOT NULL,
            PRIMARY KEY(search_run_id, job_id),
            FOREIGN KEY(search_run_id) REFERENCES search_runs(search_run_id),
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_job_search_matches_candidate_job "
        "ON candidate_job_search_matches(candidate_id, job_id)"
    )


def migrate(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    _create_tables(conn)

    conn.commit()
    conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Add candidate_job_search_matches (per-search-run scoring snapshot) table")
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print("Done. Table ensured: candidate_job_search_matches")


if __name__ == "__main__":
    main()
