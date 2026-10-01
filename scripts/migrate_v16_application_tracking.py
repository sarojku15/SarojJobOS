#!/usr/bin/env python3

"""
Additive schema for the Application Tracker + Follow-Up system
(2026-09-28 implementation, following the read-only audit of the same
name).

candidate_job_matches gains four nullable columns:

  applied_at             -- UTC ISO-8601, set ONCE, the first time
                            candidate_status transitions to APPLIED
                            (application_lifecycle.py). Never
                            overwritten by a later transition.
  applied_resume_id      -- the resume actually confirmed at the
                            moment of applying (references resumes.
                            resume_id or tailored_resumes.
                            tailored_resume_id -- no FK enforced, same
                            convention as the existing resume_id
                            column, which is also unconstrained).
  applied_resume_variant -- human-readable label for the above,
                            mirroring the existing resume_variant
                            column's own convention.
  notes                  -- free-text, candidate-owned, application-
                            scoped notes. Distinct from
                            interview_preparations.outcome_notes
                            (interview-scoped, a different fact).

The existing match-time resume_id/resume_variant columns are left
completely untouched -- they remain the scoring-time record; the new
applied_* columns are the separate, later, application-time record.

A new application_follow_ups table adds real history to what was
previously a single overwritable candidate_job_matches.follow_up_date
column: every follow-up "instance" (scheduled, completed, cancelled,
rescheduled-away-from) becomes its own row, never deleted. The
existing follow_up_date column is NOT removed or renamed -- it
continues to mean exactly what it always has ("the current active
pending follow-up date, or NULL"), so every existing reader (the
dashboard widget, the n8n follow-up workflow, the existing PATCH
.../follow-up endpoint) keeps working unchanged. The new table is
purely additive history alongside it.

A new notification_events table gives Phase 4 (optional follow-up
notifications) a place to record NOT_REQUESTED/QUEUED/SENT/FAILED
per (follow-up, cycle) without ever fabricating delivery -- and a
natural dedup key so the same due follow-up is never notified twice
within the same day's cycle.
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _column_exists(conn, table, column):
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def _add_candidate_job_matches_columns(conn):
    for column, ddl_type in (
        ("applied_at", "TEXT"),
        ("applied_resume_id", "TEXT"),
        ("applied_resume_variant", "TEXT"),
        ("notes", "TEXT"),
    ):
        if not _column_exists(conn, "candidate_job_matches", column):
            conn.execute(f"ALTER TABLE candidate_job_matches ADD COLUMN {column} {ddl_type}")


def _create_application_follow_ups(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS application_follow_ups (
            follow_up_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            due_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            notes TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_application_follow_ups_candidate_job "
        "ON application_follow_ups(candidate_id, job_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_application_follow_ups_status_due "
        "ON application_follow_ups(status, due_date)"
    )


def _create_notification_events(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_events (
            event_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            follow_up_id TEXT NOT NULL,
            cycle_date TEXT NOT NULL,
            provider TEXT,
            status TEXT NOT NULL DEFAULT 'NOT_REQUESTED',
            detail TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id),
            FOREIGN KEY(follow_up_id) REFERENCES application_follow_ups(follow_up_id)
        )
        """
    )
    # One notification per (follow_up_id, cycle_date) -- the dedup key
    # Phase 4 requires ("rerunning the workflow does not duplicate").
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_events_dedup "
        "ON notification_events(follow_up_id, cycle_date)"
    )


def migrate(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    _add_candidate_job_matches_columns(conn)
    _create_application_follow_ups(conn)
    _create_notification_events(conn)

    conn.commit()
    conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Add application-tracking columns/tables (applied_at, applied_resume_*, notes, application_follow_ups, notification_events)"
    )
    parser.add_argument("--db", default=str(DEFAULT_DEV_DB))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    print(f"Migrating: {args.db}")
    migrate(args.db)
    print(
        "Done. Ensured: candidate_job_matches.{applied_at,applied_resume_id,"
        "applied_resume_variant,notes}, application_follow_ups, notification_events"
    )


if __name__ == "__main__":
    main()
