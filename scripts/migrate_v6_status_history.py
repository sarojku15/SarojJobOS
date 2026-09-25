#!/usr/bin/env python3

"""
Additive schema: candidate_job_status_history -- one row per
candidate_status transition on a candidate_job_matches row (shortlist,
approve, mark-applied, etc). Before this, candidate_job_matches only
ever carried its OWN current candidate_status plus a single overwritten
updated_at, so "when did this move from SHORTLISTED to APPROVED" was
not reconstructable once the next transition happened.

Backward compatible / non-destructive: purely additive (one new
table), no existing column or row in candidate_job_matches or any
other table is touched. application_lifecycle.py is the only writer,
appending one row per transition; nothing else changes about how
candidate_status itself is read or written.

Same safety posture as every other migrate_v*.py in this project:
explicit --db, defaults to the local dev DB, never production.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _create_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_job_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_job_status_history_match "
        "ON candidate_job_status_history(candidate_id, job_id)"
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
    parser = argparse.ArgumentParser(description="Add candidate_job_status_history (application status transition audit) table")
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
    print("Done. Table ensured: candidate_job_status_history")


if __name__ == "__main__":
    main()
