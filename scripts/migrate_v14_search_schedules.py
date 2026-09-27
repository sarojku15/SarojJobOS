#!/usr/bin/env python3

"""
Additive schema: search_schedules -- Phase 4 (scheduling).

Root cause this replaces: saved_searches.schedule_json (migrate_v4_
search_extensions.py) has been readable/writable via the API since
Phase 10, but nothing anywhere ever reads it to actually trigger a
run -- it is a dead field. This table is the real, authoritative
scheduling state; api/search_store.py's saved-search response computes
its "schedule" field from THIS table when a row exists, falling back
to the legacy schedule_json column only for a search that predates
this migration (backward-compatible, never breaks an existing read).

One row per saved search that has ever had scheduling configured
(enabled or not -- disabling keeps the row, just enabled=0, so
next_run_at/last_run_at history is never lost). scripts/scheduler.py
is the ONLY writer of next_run_at/last_run_at/last_run_status/
last_run_error; nothing else computes "due-ness" independently.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _create_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_schedules (
            saved_search_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            frequency TEXT,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            next_run_at TEXT,
            last_run_at TEXT,
            last_run_status TEXT,
            last_run_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(saved_search_id) REFERENCES saved_searches(saved_search_id),
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_schedules_enabled_next_run "
        "ON search_schedules(enabled, next_run_at)"
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
    parser = argparse.ArgumentParser(description="Add search_schedules table")
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print("Done. Table ensured: search_schedules")


if __name__ == "__main__":
    main()
