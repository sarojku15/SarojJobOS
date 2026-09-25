#!/usr/bin/env python3

"""
Additive schema for full resume/profile-version traceability (sections
4-6 of the resume-management request):

  - candidate_search_profile.source_resume_id: which resumes.resume_id
    (if any) produced THIS profile version -- e.g. resume_extracted
    versions carry the resume that was just uploaded; a manual edit
    carries forward whichever resume the profile it was edited FROM
    traced back to; version 1 (a brand new candidate, no resume yet)
    is NULL. Never guessed -- only ever set by the exact caller that
    knows the real lineage (see api/profile_store.py).

  - saved_searches.profile_version: optionally PINS a saved search to
    one specific historical candidate_search_profile.version, instead
    of always dynamically using whichever profile is currently active
    (NULL = today's existing default behavior, completely unchanged).
    This is what lets an OLD search keep representing an OLD
    resume/profile even after the candidate uploads a newer one, and a
    NEW search created afterward can explicitly use the newer one.

  - candidate_job_matches.profile_version: which profile version was
    ACTUALLY used to compute THIS match's score -- recorded at
    match-creation/re-match time, independent of whatever the search's
    own current pin setting is later changed to. Full per-match
    traceability, complementing the already-existing resume_id column
    (migrate_v7_candidate_resume_variant.py).

Purely additive: no existing column, table, or constraint is touched.
A caller that never reads these three new columns sees no behavior
change whatsoever -- every saved search's profile_version stays NULL
(today's existing "always use current active profile" behavior)
unless a search explicitly pins one going forward.

Same safety posture as every other migrate_v*.py in this project:
explicit --db, defaults to the local dev DB, never production.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"

_COLUMN_ADDITIONS = [
    ("candidate_search_profile", "source_resume_id", "TEXT"),
    ("saved_searches", "profile_version", "INTEGER"),
    ("candidate_job_matches", "profile_version", "INTEGER"),
]


def migrate(db_path):
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)

    for table, column, col_type in _COLUMN_ADDITIONS:
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if table_exists is None:
            continue
        existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    conn.commit()
    conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Add resume/profile-version traceability columns")
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print("Done. Columns ensured: candidate_search_profile.source_resume_id, saved_searches.profile_version, candidate_job_matches.profile_version")


if __name__ == "__main__":
    main()
