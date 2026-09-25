#!/usr/bin/env python3

"""
Additive column: candidate_job_matches.resume_variant.

Real architecture bug this fixes: resume_variant previously existed
ONLY as a column on the global `jobs` table (see init_tracker.py). A
global per-job column cannot hold two different values for two
different candidates matched to the SAME job -- exactly the kind of
single-candidate-era leftover this project's candidate_status column
already had and was already fixed for (see search_worker.py's
_upsert_job_scored() docstring and generate_run_report.py's
build_report_rows(), which both prefer the candidate-scoped column
over the global one). This migration gives resume_variant the same
fix: a per (candidate_id, job_id) column on candidate_job_matches,
populated by scripts/resume_variant_selector.py from that candidate's
own uploaded resume(s) -- never fabricated, never shared across
candidates.

The legacy jobs.resume_variant column is left in place (not dropped --
no destructive schema change) but is no longer the authoritative
source once this column has a value for a given match; see
api/results_store.py and generate_run_report.py for the read-side
"candidate-scoped wins when set" fix applied in the same change.

Same safety posture as every other migrate_v*.py in this project:
explicit --db, defaults to the local dev DB, never production.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"

NEW_COLUMNS = [
    ("resume_variant", "TEXT"),
]


def migrate(db_path):
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)

    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='candidate_job_matches'"
    ).fetchone()
    if table_exists is None:
        conn.close()
        return

    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(candidate_job_matches)").fetchall()}
    for name, col_type in NEW_COLUMNS:
        if name not in existing_columns:
            conn.execute(f"ALTER TABLE candidate_job_matches ADD COLUMN {name} {col_type}")

    conn.commit()
    conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Add candidate_job_matches.resume_variant (candidate-scoped, fixes global-column leak)")
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print("Done. Column ensured on candidate_job_matches: resume_variant")


if __name__ == "__main__":
    main()
