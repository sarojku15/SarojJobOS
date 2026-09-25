#!/usr/bin/env python3

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data/applications/jobos.db"


def connect():
    return sqlite3.connect(DB_PATH)


def get_job(conn, source, job_id):
    return conn.execute(
        """
        SELECT *
        FROM jobs
        WHERE source = ?
          AND job_id = ?
        """,
        (source, job_id),
    ).fetchone()


_DISCOVERY_COLUMNS = ("discovery_source", "discovery_query", "completeness")


def _has_discovery_columns(conn):
    """True iff the jobs table already has Phase 14's 3 additive
    columns (added by migrate_v2_schema._ensure_job_columns() -- see
    that function). A DB that predates that migration (e.g. a legacy
    test's own init_tracker.main()-only schema) must keep working
    exactly as before Phase 14 -- checked fresh per call (a cheap
    PRAGMA, not worth caching given this project's job volumes) rather
    than assumed, since different tests/scripts point `conn` at
    differently-migrated databases within the same process."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    return all(name in columns for name in _DISCOVERY_COLUMNS)


def upsert_job(conn, job, scoring):
    now = datetime.now(timezone.utc).isoformat()
    include_discovery_columns = _has_discovery_columns(conn)

    columns = [
        "job_id", "source", "company", "title", "location", "work_model",
        "job_url", "application_url", "posted_date", "discovered_at",
        "jd_text", "experience_required", "mandatory_skills", "preferred_skills",
        "score", "priority", "matched_skills", "missing_skills",
        "hard_reject_reasons", "status", "last_updated",
    ]
    values = [
        job["job_id"],
        job["source"],
        job["company"],
        job["title"],
        job["location"],
        job["work_model"],
        job["job_url"],
        job["application_url"],
        job["posted_date"],
        job["discovered_at"],
        job["jd_text"],
        job["experience_required"],
        json.dumps(job["mandatory_skills"]),
        json.dumps(job["preferred_skills"]),
        scoring["score"],
        scoring["priority"],
        json.dumps(scoring["matched_skills"]),
        json.dumps(scoring["missing_skills"]),
        json.dumps(scoring["hard_reject_reasons"]),
        scoring["status"],
        now,
    ]
    update_clauses = [
        "company = excluded.company",
        "title = excluded.title",
        "location = excluded.location",
        "work_model = excluded.work_model",
        "job_url = excluded.job_url",
        "application_url = excluded.application_url",
        "posted_date = excluded.posted_date",
        "discovered_at = excluded.discovered_at",
        "jd_text = excluded.jd_text",
        "experience_required = excluded.experience_required",
        "mandatory_skills = excluded.mandatory_skills",
        "preferred_skills = excluded.preferred_skills",
        "score = excluded.score",
        "priority = excluded.priority",
        "matched_skills = excluded.matched_skills",
        "missing_skills = excluded.missing_skills",
        "hard_reject_reasons = excluded.hard_reject_reasons",
        "last_updated = CURRENT_TIMESTAMP",
    ]

    if include_discovery_columns:
        # Phase 14, purely additive -- .get() so any caller building a
        # job dict without these keys (every caller before this phase)
        # still works unchanged, writing "" / "" / NULL.
        columns += list(_DISCOVERY_COLUMNS)
        values += [
            job.get("discovery_source", "") or "",
            job.get("discovery_query", "") or "",
            job.get("completeness"),
        ]
        update_clauses += [f"{name} = excluded.{name}" for name in _DISCOVERY_COLUMNS]

    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"""
        INSERT INTO jobs ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(source, job_id) DO UPDATE SET
            {", ".join(update_clauses)}
        """,
        values,
    )


def commit(conn):
    conn.commit()


def close(conn):
    conn.close()


if __name__ == "__main__":
    print("tracker.py: module loaded successfully")
