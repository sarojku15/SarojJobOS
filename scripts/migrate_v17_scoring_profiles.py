#!/usr/bin/env python3

"""
Additive schema for Custom Job Scoring Profiles.

scoring_profiles is a NEW table -- candidate-owned (same candidate_id
scoping convention as saved_searches, resumes, company_research, etc.;
this is the existing browser-localStorage-identity ownership model,
never a new/stronger authentication claim -- see docs/SECURITY.md's
"Credential ownership" and multi-user-isolation sections for what that
does and doesn't protect against). Holds the weighted-criteria model
scripts/scoring_profiles.py evaluates:

  scoring_profile_id   -- "sp_<hex>"
  candidate_id         -- FK to candidates
  name                 -- display name, e.g. "Senior SRE / Platform"
  description           -- free text, candidate-owned
  weights_json          -- {"technical_skills": 30, ...} as entered
                            (NOT pre-normalized -- scoring_profiles.py
                            normalizes at evaluation time, every time,
                            so edits are always reflected correctly)
  mandatory_gate_enabled -- 0/1
  min_shortlist_score    -- 0..100
  is_default             -- 0/1 (at most one TRUE per candidate; this
                             migration does not enforce that with a
                             DB constraint -- the API layer does, the
                             same convention candidate_search_profile's
                             own is_active flag already uses)
  version                -- starts at 1, incremented on every edit
                             (never mutated in place) so a job match
                             that stored {profile_id, profile_version}
                             at scoring time remains interpretable
                             even after the profile is later edited
  created_at / updated_at

saved_searches gains ONE new nullable column, scoring_profile_id --
NULL (the default for every existing row) means "legacy scoring,
exactly as before, using this search's own existing minimum_match_score
column" -- zero behavior change for any search that predates this
migration or that a user never explicitly points at a custom profile.

candidate_job_matches and candidate_job_search_matches each gain two
new nullable columns, scoring_profile_id and scoring_profile_version,
so a job match that WAS scored with a custom profile stays traceable
to exactly which profile/version produced it -- never overwriting the
existing fit_score/priority columns' meaning, which continue to mean
"the score that was actually used to rank this match," legacy or
custom.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _column_exists(conn, table, column):
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _table_exists(conn, table):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _create_scoring_profiles_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scoring_profiles (
            scoring_profile_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            weights_json TEXT NOT NULL,
            mandatory_gate_enabled INTEGER NOT NULL DEFAULT 1,
            min_shortlist_score REAL NOT NULL DEFAULT 70,
            is_default INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scoring_profiles_candidate "
        "ON scoring_profiles(candidate_id)"
    )


def _add_saved_searches_column(conn):
    if not _table_exists(conn, "saved_searches"):
        return
    if not _column_exists(conn, "saved_searches", "scoring_profile_id"):
        conn.execute("ALTER TABLE saved_searches ADD COLUMN scoring_profile_id TEXT")


def _add_match_traceability_columns(conn):
    for table in ("candidate_job_matches", "candidate_job_search_matches"):
        if not _table_exists(conn, table):
            continue
        for column in ("scoring_profile_id", "scoring_profile_version"):
            if not _column_exists(conn, table, column):
                ddl_type = "TEXT" if column == "scoring_profile_id" else "INTEGER"
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def migrate(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    _create_scoring_profiles_table(conn)
    _add_saved_searches_column(conn)
    _add_match_traceability_columns(conn)

    conn.commit()
    conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Add scoring_profiles table + saved_searches.scoring_profile_id + "
        "candidate_job_matches/candidate_job_search_matches traceability columns"
    )
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print(
        "Done. Ensured: scoring_profiles table; saved_searches.scoring_profile_id; "
        "candidate_job_matches/candidate_job_search_matches.{scoring_profile_id,scoring_profile_version}"
    )


if __name__ == "__main__":
    main()
