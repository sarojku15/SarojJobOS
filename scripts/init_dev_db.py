#!/usr/bin/env python3

"""
Phase 9 local-development database bootstrap.

Builds data/applications/jobos_dev.db -- a database file COMPLETELY
SEPARATE from the production data/applications/jobos.db -- using the
exact same schema-construction functions the existing test suite
already uses (init_tracker.main() for the jobs table,
migrate_v2_schema._create_new_tables()/_ensure_job_columns() for the
multi-candidate tables, migrate_v3_saved_searches.migrate() for the
Phase 9 saved_searches tables, migrate_v4_search_extensions.migrate()
for saved_searches' additive skills/schedule columns,
migrate_v5_search_run_sources.migrate() for the per-source execution
audit table, migrate_v6_status_history.migrate() for the application
status transition audit table, migrate_v7_candidate_resume_variant.
migrate() for the candidate-scoped resume_variant column). No second
schema definition is written here.

Deliberately does NOT call migrate_v2_schema.migrate() (which seeds
Saroj's real config/profile.json into candidate_id="saroj") and does
NOT call migrate_v2_schema._seed_saroj() -- the Phase 9 dev database
starts empty/generic. Existing Saroj data may optionally be used as a
manual dev/test candidate (created through the API like any other),
never assumed or hardcoded here.

Idempotent: safe to run multiple times against an already-initialized
dev database (every underlying call uses CREATE TABLE IF NOT EXISTS /
ADD COLUMN IF NOT EXISTS-equivalent guards).
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"
PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"

sys.path.insert(0, str(ROOT / "scripts"))

import init_tracker
import migrate_v2_schema
import migrate_v3_saved_searches
import migrate_v4_search_extensions
import migrate_v5_search_run_sources
import migrate_v6_status_history
import migrate_v7_candidate_resume_variant
import migrate_v8_resume_profile_traceability
import migrate_v9_candidate_job_search_matches
import migrate_v10_search_type
import migrate_v11_tailored_resumes
import migrate_v12_company_research
import migrate_v13_interview_prep
import migrate_v14_search_schedules
import migrate_v15_follow_up_date


def init_dev_db(db_path=DEV_DB):
    db_path = Path(db_path)

    if db_path.resolve() == PRODUCTION_DB.resolve():
        raise ValueError(
            "Refusing to initialize the dev database at the production "
            f"DB path ({PRODUCTION_DB}) -- this function must never be "
            "pointed at production."
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)

    original_data_dir = init_tracker.DATA_DIR
    original_db_path = init_tracker.DB_PATH
    try:
        init_tracker.DATA_DIR = db_path.parent
        init_tracker.DB_PATH = db_path
        init_tracker.main()
    finally:
        init_tracker.DATA_DIR = original_data_dir
        init_tracker.DB_PATH = original_db_path

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    migrate_v2_schema._create_new_tables(conn)
    migrate_v2_schema._ensure_job_columns(conn)
    conn.commit()
    conn.close()

    migrate_v3_saved_searches.migrate(db_path)
    migrate_v4_search_extensions.migrate(db_path)
    migrate_v5_search_run_sources.migrate(db_path)
    migrate_v6_status_history.migrate(db_path)
    migrate_v7_candidate_resume_variant.migrate(db_path)
    migrate_v8_resume_profile_traceability.migrate(db_path)
    migrate_v9_candidate_job_search_matches.migrate(db_path)
    migrate_v10_search_type.migrate(db_path)
    migrate_v11_tailored_resumes.migrate(db_path)
    migrate_v12_company_research.migrate(db_path)
    migrate_v13_interview_prep.migrate(db_path)
    migrate_v14_search_schedules.migrate(db_path)
    migrate_v15_follow_up_date.migrate(db_path)

    return db_path


def main():
    path = init_dev_db()
    print(f"Dev database ready: {path}")


if __name__ == "__main__":
    main()
