#!/usr/bin/env python3

"""
Additive column: saved_searches.search_type.

Real bug this fixes (found live on the project's own dev DB): the
/searches page and dashboard had no way to distinguish a genuine user
search from a development/verification search created during this
project's own live-testing sessions -- both showed up identically,
and the dashboard's "Jobs Found"/"Matching" aggregates were not even
scoped by search at all (see api/results_store.py's
get_dashboard_summary() fix in this same change: it read the ENTIRE
global `jobs` table, shared across every candidate/search/test run
ever, not this candidate's own activity).

search_type is one of:
  USER   -- a real, human-created search. The default for every
            search created through the normal API
            (POST /api/candidates/{id}/searches) -- a real user never
            has to think about this field at all.
  TEST   -- created by this project's own automated test suite or a
            manual live-verification session, never shown on the
            normal /searches page or counted in dashboard aggregates.
  SYSTEM -- reserved for a future internally-scheduled/automated
            search that is not a specific human's request either;
            not used by anything yet, included now so TEST/SYSTEM
            need not be conflated later.

Never inferred from a search's NAME (explicitly rejected -- brittle
title matching like `if "test" in name` would misclassify a real
user's search that happens to mention "test environment" or similar,
and would fail to catch a test search with an innocuous name). Set
EXPLICITLY by the creating code path: the real create-search API
defaults to USER via the column's own DEFAULT; this project's test
suite/live-verification scripts set TEST explicitly when they create a
saved search of their own.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"

NEW_COLUMNS = [
    ("search_type", "TEXT NOT NULL DEFAULT 'USER'"),
]


def migrate(db_path):
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)

    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='saved_searches'"
    ).fetchone()
    if table_exists is None:
        conn.close()
        return

    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(saved_searches)").fetchall()}
    for name, col_type in NEW_COLUMNS:
        if name not in existing_columns:
            conn.execute(f"ALTER TABLE saved_searches ADD COLUMN {name} {col_type}")

    conn.commit()
    conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Add saved_searches.search_type (USER/TEST/SYSTEM isolation)")
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print("Done. Column ensured on saved_searches: search_type (DEFAULT 'USER')")


if __name__ == "__main__":
    main()
