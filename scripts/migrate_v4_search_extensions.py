#!/usr/bin/env python3

"""
Additive columns on saved_searches (Phase 10): `skills_json` (a
per-search skill list used only for query construction -- see
../.claude/skills/job-discovery-engine/references/search-query-strategy.md
-- never a scoring override; scoring still reads only the candidate's
own profile skills, unchanged) and `schedule_json` (stored schedule
metadata -- frequency/enabled -- for a FUTURE scheduler to read;
storing it here does NOT activate anything, launchd is untouched).

Same safety posture as migrate_v3_saved_searches.py: explicit --db,
defaults to the local dev DB, never production.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"

NEW_COLUMNS = [
    ("skills_json", "TEXT"),
    ("schedule_json", "TEXT"),
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
    parser = argparse.ArgumentParser(description="Add Phase 10 saved_searches columns (skills_json, schedule_json)")
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print("Done. Columns ensured on saved_searches: skills_json, schedule_json")


if __name__ == "__main__":
    main()
