#!/usr/bin/env python3

"""
Additive schema: company_research -- Phase 2 (company research
persistence).

One row per research attempt (never overwritten -- a "Refresh
Research" call inserts a new row with the next `version`, same
never-overwrite convention as candidate_search_profile/resumes/
tailored_resumes). Associated with a candidate, a company name, and
optionally the specific job that prompted the research.

`status` is one of SUCCESS / PARTIAL / FAILED / NOT_ATTEMPTED --
scripts/company_research.py must always write a real, honest status,
never silently skip a company or fabricate a field it couldn't find.
Every externally-sourced fact (website/description/industry/etc.)
retains its own source URL in `source_urls_json` -- a field with no
corroborating source URL is never invented to fill a gap.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _create_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_research (
            company_research_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            job_id TEXT,
            company_name TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL,
            website TEXT,
            description TEXT,
            industry TEXT,
            headquarters TEXT,
            company_size TEXT,
            tech_indicators_json TEXT,
            role_context TEXT,
            recent_info_json TEXT,
            hiring_signals_json TEXT,
            source_urls_json TEXT,
            error_message TEXT,
            researched_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id),
            UNIQUE(candidate_id, company_name, version)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_company_research_candidate_company "
        "ON company_research(candidate_id, company_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_company_research_job "
        "ON company_research(job_id)"
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
    parser = argparse.ArgumentParser(description="Add company_research table")
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print("Done. Table ensured: company_research")


if __name__ == "__main__":
    main()
