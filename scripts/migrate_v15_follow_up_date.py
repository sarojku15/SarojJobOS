#!/usr/bin/env python3

"""
Additive schema: candidate_job_matches.follow_up_date -- Phase 6
(follow_up_date gap audit).

Root cause this fixes: `follow_up_date` already existed as a field in
config/application_schema.json's LEGACY, single-candidate job-record
shape (the global `jobs` table), but no API route in the current
multi-candidate system ever read or wrote it -- and writing to
`jobs.follow_up_date` directly would have been a real candidate-
isolation bug anyway, since `jobs` is a GLOBAL table shared across
every candidate (the same job row can be matched by multiple
candidates -- see candidate_job_matches' own existing candidate_status
column for the established, correct pattern: candidate-specific state
lives on candidate_job_matches, keyed by (candidate_id, job_id), never
on the shared `jobs` row).

This adds `follow_up_date` (nullable ISO date string) to
candidate_job_matches instead, alongside the existing candidate_status
column, so it is correctly scoped per (candidate_id, job_id) from the
start.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _column_exists(conn, table, column):
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def migrate(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    if not _column_exists(conn, "candidate_job_matches", "follow_up_date"):
        conn.execute("ALTER TABLE candidate_job_matches ADD COLUMN follow_up_date TEXT")

    conn.commit()
    conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Add candidate_job_matches.follow_up_date column")
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print("Done. Column ensured: candidate_job_matches.follow_up_date")


if __name__ == "__main__":
    main()
