#!/usr/bin/env python3

"""
Additive schema: search_run_sources -- one row per (search_run_id,
source) pair, persisting exactly what a single search_worker.py
process_queue_item() call already computes in-memory (via
source_registry.discover_from_sources()'s run_report list of
SourceRunState objects) but previously discarded once that call
returned. Before this, only coarse, run-wide aggregates survived in
search_runs (e.g. `blocked_queries` as a COUNT, never WHICH sources) --
there was no way to tell "this source was searched and returned zero"
from "this source was never searched" once the worker process exited.

Backward compatible / non-destructive: purely additive (one new
table), no existing column or row in search_runs or any other table is
touched. search_runs itself is completely unmodified -- a caller that
never reads search_run_sources sees no behavior change whatsoever.

`source` here is the REGISTRY key (e.g. "NAUKRI", "LINKEDIN_SEARCH") --
the same identifier source_registry.ADAPTERS/discover_from_sources()
already use, so a row here maps 1:1 onto one SourceRunState. The
human-facing BOARD name (e.g. "LINKEDIN") is derived on read via
source_registry.board_name_for_source() -- never duplicated as a
second stored column that could drift out of sync.

Accepts an explicit --db path (defaults to the Phase 9 local
development database, data/applications/jobos_dev.db) and NEVER
defaults to the production database -- same safety posture as every
other migrate_v*.py in this project.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _create_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_run_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_run_id TEXT NOT NULL,
            source TEXT NOT NULL,
            attempted INTEGER NOT NULL DEFAULT 0,
            started_at TEXT,
            completed_at TEXT,
            duration_ms INTEGER,
            reachable INTEGER,
            succeeded INTEGER,
            raw_count INTEGER NOT NULL DEFAULT 0,
            eligible_count INTEGER NOT NULL DEFAULT 0,
            displayed_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'NOT_ATTEMPTED',
            error_type TEXT,
            error_message TEXT,
            details_json TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(search_run_id, source),
            FOREIGN KEY(search_run_id) REFERENCES search_runs(search_run_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_run_sources_run "
        "ON search_run_sources(search_run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_run_sources_source "
        "ON search_run_sources(source)"
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
    parser = argparse.ArgumentParser(description="Add search_run_sources (per-source execution audit) table")
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
    print("Done. Table ensured: search_run_sources")


if __name__ == "__main__":
    main()
