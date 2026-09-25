#!/usr/bin/env python3

"""
Phase 9 additive schema: "saved searches" -- one candidate, many
independent, named sets of search criteria (job target, locations,
experience/salary/work-model/employment-type preferences, sources,
minimum score, freshness). Distinct from candidate_search_profile
(the single "what this candidate IS" profile version used by the
existing search_submission.py) and distinct from search_runs (a single
execution). A saved search's target_roles/target_locations/work_models
are passed as explicit overrides into the existing, unmodified
search_submission.submit_search() via the target_roles_override /
target_locations_override / work_models_override parameters added in
this same phase -- no second query-planning or submission
implementation.

Accepts an explicit --db path (defaults to the Phase 9 local
development database, data/applications/jobos_dev.db) and NEVER
defaults to the production database -- production is only ever touched
if --db explicitly names it, which no Phase 9 code path does.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _create_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_searches (
            saved_search_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            name TEXT NOT NULL,
            target_roles_json TEXT NOT NULL,
            target_locations_json TEXT NOT NULL,
            work_models_json TEXT,
            employment_type TEXT,
            minimum_experience_years REAL,
            maximum_experience_years REAL,
            salary_expectation_min REAL,
            salary_expectation_max REAL,
            salary_currency TEXT,
            minimum_match_score INTEGER,
            max_job_age_days INTEGER,
            sources_json TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_search_runs (
            saved_search_id TEXT NOT NULL,
            search_run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(saved_search_id, search_run_id),
            FOREIGN KEY(saved_search_id) REFERENCES saved_searches(saved_search_id),
            FOREIGN KEY(search_run_id) REFERENCES search_runs(search_run_id)
        )
        """
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
    parser = argparse.ArgumentParser(description="Add Phase 9 saved_searches tables")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DEV_DB),
        help=f"Database path (default: {DEFAULT_DEV_DB}, never production)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print("Done. Tables ensured: saved_searches, saved_search_runs")


if __name__ == "__main__":
    main()
