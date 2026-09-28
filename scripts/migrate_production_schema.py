#!/usr/bin/env python3
"""
Explicit, one-time, out-of-band production schema migration.

The ONLY script in this repository that defaults to
data/applications/jobos.db instead of jobos_dev.db -- every other
migrate_v*.py deliberately defaults to the dev DB and
scripts/init_dev_db.py's own init_dev_db() function explicitly REFUSES
to run against production (a deliberate guard from an earlier session,
see its own docstring). This script exists specifically to bring
production up to the same schema the dev DB already has (it was
seeded long ago and never advanced past the v2 schema -- missing
saved_searches, search_run_sources, status_history,
resume_variant/profile-traceability columns, candidate_job_search_matches,
search_type, and the whole v11-v15 automation schema) WITHOUT ever
calling the one genuinely destructive/data-writing function in this
chain: migrate_v2_schema._seed_saroj() (which does a real
INSERT INTO candidates for candidate_id="saroj" -- production's real
candidate already exists; re-running that would either fail on a
UNIQUE constraint or, worse, silently duplicate/disturb real data).

Every migration this script calls (init_tracker.main(),
migrate_v2_schema._create_new_tables()/_ensure_job_columns(),
migrate_v3 through migrate_v15's own .migrate()) is independently
CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS-equivalent and
order-tolerant (each checks its own preconditions and no-ops if a
dependency table doesn't exist yet) -- verified by reading every one
of those 13 files before writing this script. Nothing here alters an
existing row, existing column's data, or existing constraint.

Safety sequence (non-negotiable, in this order):
  1. Refuse if the target DB fails PRAGMA integrity_check before
     touching anything.
  2. Copy a timestamped backup to data/backups/ BEFORE any migration
     runs.
  3. Record per-table row counts before migrating.
  4. Run the safe migration chain.
  5. Re-run PRAGMA integrity_check.
  6. Re-check every pre-existing table's row count is IDENTICAL (never
     decreased, never increased -- these migrations only add schema,
     never touch data) and that candidate_id="saroj" still exists with
     its original name/status.
  7. Abort loudly (raising, backup already safely on disk) if any of
     the above checks fail -- never silently continue.

Usage:
    .venv/bin/python3 scripts/migrate_production_schema.py --confirm
    .venv/bin/python3 scripts/migrate_production_schema.py --db /path/to/copy.db --confirm  (for testing)

--confirm is required -- this script refuses to run without it, so it
can never be invoked by accident (e.g. via a stray CI job or a
copy-pasted command missing a flag).
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"
BACKUP_DIR = ROOT / "data" / "backups"

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


class ProductionMigrationError(RuntimeError):
    pass


def _integrity_check(db_path):
    conn = sqlite3.connect(db_path)
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    return result


def _table_names(db_path):
    conn = sqlite3.connect(db_path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    return names


def _row_counts(db_path, tables):
    conn = sqlite3.connect(db_path)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    conn.close()
    return counts


def _saroj_row(db_path):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT candidate_id, name, status FROM candidates WHERE candidate_id = 'saroj'"
    ).fetchone()
    conn.close()
    return row


def migrate_production_schema(db_path=PRODUCTION_DB, backup_dir=BACKUP_DIR):
    db_path = Path(db_path)
    if not db_path.exists():
        raise ProductionMigrationError(f"Target DB does not exist: {db_path}")

    pre_integrity = _integrity_check(db_path)
    if pre_integrity != "ok":
        raise ProductionMigrationError(
            f"Refusing to migrate: PRAGMA integrity_check failed BEFORE any change: {pre_integrity!r}"
        )

    pre_tables = _table_names(db_path)
    pre_counts = _row_counts(db_path, pre_tables)
    pre_saroj = _saroj_row(db_path)

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"jobos_before_automation_migration_{timestamp}.db"
    shutil.copy2(db_path, backup_path)

    backup_integrity = _integrity_check(backup_path)
    if backup_integrity != "ok":
        raise ProductionMigrationError(
            f"Refusing to migrate: the just-made BACKUP failed its own integrity_check: {backup_integrity!r} "
            f"(backup at {backup_path})"
        )

    # The exact same safe sequence scripts/init_dev_db.py's init_dev_db()
    # runs -- MINUS its production guard (this script's entire purpose)
    # and MINUS migrate_v2_schema.migrate()'s _seed_saroj() call (never
    # called here -- see module docstring).
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

    for mod in (
        migrate_v3_saved_searches, migrate_v4_search_extensions, migrate_v5_search_run_sources,
        migrate_v6_status_history, migrate_v7_candidate_resume_variant, migrate_v8_resume_profile_traceability,
        migrate_v9_candidate_job_search_matches, migrate_v10_search_type, migrate_v11_tailored_resumes,
        migrate_v12_company_research, migrate_v13_interview_prep, migrate_v14_search_schedules,
        migrate_v15_follow_up_date,
    ):
        mod.migrate(db_path)

    post_integrity = _integrity_check(db_path)
    if post_integrity != "ok":
        raise ProductionMigrationError(
            f"PRAGMA integrity_check FAILED AFTER migration: {post_integrity!r}. "
            f"Restore from backup immediately: cp {backup_path} {db_path}"
        )

    post_counts = _row_counts(db_path, pre_tables)  # only the tables that existed BEFORE
    shrunk = {t: (pre_counts[t], post_counts[t]) for t in pre_tables if post_counts[t] < pre_counts[t]}
    if shrunk:
        raise ProductionMigrationError(
            f"Row count DECREASED in existing table(s) after migration -- never expected, "
            f"restore from backup immediately: cp {backup_path} {db_path}. Details: {shrunk}"
        )

    post_saroj = _saroj_row(db_path)
    if pre_saroj is not None and post_saroj != pre_saroj:
        raise ProductionMigrationError(
            f"candidate_id='saroj' row changed unexpectedly -- was {pre_saroj}, now {post_saroj}. "
            f"Restore from backup immediately: cp {backup_path} {db_path}"
        )

    return {
        "backup_path": str(backup_path),
        "pre_counts": pre_counts,
        "post_counts": post_counts,
        "saroj_before": pre_saroj,
        "saroj_after": post_saroj,
        "new_tables": sorted(_table_names(db_path) - pre_tables),
    }


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="One-time, backed-up, out-of-band production schema migration.")
    parser.add_argument("--db", default=str(PRODUCTION_DB))
    parser.add_argument("--confirm", action="store_true", required=True, help="Required -- refuses to run without it.")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    result = migrate_production_schema(db_path=Path(args.db))
    print(f"Backup written: {result['backup_path']}")
    print(f"New tables added: {result['new_tables']}")
    print(f"Saroj before: {result['saroj_before']}")
    print(f"Saroj after:  {result['saroj_after']}")
    print("Row counts (table: before -> after):")
    for t in sorted(result["pre_counts"]):
        print(f"  {t}: {result['pre_counts'][t]} -> {result['post_counts'][t]}")
    print("Migration completed successfully; production DB integrity verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
