#!/usr/bin/env python3

"""
Additive schema: tailored_resumes -- Phase 1 (resume tailoring).

One row per generated tailored resume. Never overwrites `resumes` (the
original, uploaded PDF) or an earlier tailoring -- a re-generate for
the same (candidate, base_resume, job) inserts a NEW row with the next
`tailoring_version`, exactly like candidate_search_profile's own
version-never-overwritten convention.

`content_json` holds the actual tailored resume content (identity,
summary, ordered skills, ordered/filtered employment bullets) -- a
structured representation, not a new PDF binary (no PDF-generation
library exists in this project's dependencies; the export path renders
this to plain text/HTML instead of writing a new PDF -- see
scripts/resume_tailoring.py's own docstring for that explicit,
deliberate scope decision).

`factual_safety_status` / `factual_safety_notes_json` record the
automated validation scripts/resume_tailoring.py runs BEFORE persisting
any tailored resume (every word used must already appear in the
candidate's own confirmed profile/base resume) -- PASS or REJECTED,
never silently skipped.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _create_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tailored_resumes (
            tailored_resume_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            base_resume_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            tailoring_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            content_json TEXT NOT NULL,
            changed_sections_json TEXT,
            unchanged_sections_json TEXT,
            keywords_added_json TEXT,
            keywords_emphasized_json TEXT,
            matched_skills_json TEXT,
            missing_skills_json TEXT,
            factual_safety_status TEXT NOT NULL,
            factual_safety_notes_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id),
            FOREIGN KEY(base_resume_id) REFERENCES resumes(resume_id),
            UNIQUE(candidate_id, base_resume_id, job_id, tailoring_version)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tailored_resumes_candidate_job "
        "ON tailored_resumes(candidate_id, job_id)"
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
    parser = argparse.ArgumentParser(description="Add tailored_resumes table")
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print("Done. Table ensured: tailored_resumes")


if __name__ == "__main__":
    main()
