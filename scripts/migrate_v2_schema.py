#!/usr/bin/env python3

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "applications"
DB_PATH = DATA_DIR / "jobos.db"
PROFILE_PATH = ROOT / "config" / "profile.json"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _create_new_tables(conn):
    """
    Create, if absent, the v2 multi-candidate tables. jobs remains
    global and untouched here -- see _ensure_job_columns() for the
    only change made to it (new nullable columns, no constraint
    change, no candidate_id).
    """

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidates (
            candidate_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE'
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resumes (
            resume_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            resume_version TEXT,
            file_path TEXT,
            uploaded_at TEXT NOT NULL,
            parsed_at TEXT,
            parser_version TEXT,
            status TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id),
            UNIQUE(candidate_id, content_hash)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resume_profile_fields (
            field_id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            resume_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            field_value TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id),
            FOREIGN KEY(resume_id) REFERENCES resumes(resume_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_search_profile (
            profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            search_mode TEXT NOT NULL,
            profile_json TEXT NOT NULL,
            confirmed_by_user INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 0,
            source_resume_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id),
            UNIQUE(candidate_id, version)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_job_matches (
            candidate_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            search_run_id TEXT,
            resume_id TEXT,
            fit_score INTEGER,
            priority TEXT,
            experience_eligibility TEXT,
            salary_match TEXT,
            location_match TEXT,
            skill_match_json TEXT,
            matched_skills_json TEXT,
            missing_skills_json TEXT,
            candidate_status TEXT,
            resume_variant TEXT,
            profile_version INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(candidate_id, job_id),
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id),
            FOREIGN KEY(resume_id) REFERENCES resumes(resume_id),
            FOREIGN KEY(search_run_id) REFERENCES search_runs(search_run_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_runs (
            search_run_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT,
            role TEXT,
            location TEXT,
            query TEXT,
            started_at TEXT,
            completed_at TEXT,
            queries_total INTEGER DEFAULT 0,
            queries_completed INTEGER DEFAULT 0,
            jobs_discovered INTEGER DEFAULT 0,
            jobs_deduplicated INTEGER DEFAULT 0,
            jobs_experience_excluded INTEGER DEFAULT 0,
            jobs_eligible INTEGER DEFAULT 0,
            jobs_scored INTEGER DEFAULT 0,
            jobs_ready INTEGER DEFAULT 0,
            errors_count INTEGER DEFAULT 0,
            blocked_queries INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_queue (
            queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_run_id TEXT NOT NULL UNIQUE,
            candidate_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            claimed_at TEXT,
            completed_at TEXT,
            FOREIGN KEY(search_run_id) REFERENCES search_runs(search_run_id),
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
        )
        """
    )

    # Per-search-run scoring snapshot (item 5 fix -- see
    # migrate_v9_candidate_job_search_matches.py's own docstring for
    # the full root-cause writeup). Inlined into the base schema here
    # (unlike most additive tables, which stay in their own
    # migrate_vN.py only) because search_worker.py's core
    # upsert_candidate_job_match() -- exercised by the large majority
    # of this project's offline tests -- writes to it unconditionally
    # whenever search_run_id is not None; a test DB built via this
    # function alone (bypassing init_dev_db.py) would otherwise lack
    # it entirely.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_job_search_matches (
            search_run_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            fit_score INTEGER,
            priority TEXT,
            experience_eligibility TEXT,
            location_match TEXT,
            skill_match_json TEXT,
            matched_skills_json TEXT,
            missing_skills_json TEXT,
            resume_id TEXT,
            resume_variant TEXT,
            profile_version INTEGER,
            created_at TEXT NOT NULL,
            PRIMARY KEY(search_run_id, job_id),
            FOREIGN KEY(search_run_id) REFERENCES search_runs(search_run_id),
            FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_job_search_matches_candidate_job "
        "ON candidate_job_search_matches(candidate_id, job_id)"
    )


def _ensure_job_columns(conn):
    """
    Add new, nullable, global descriptive columns to the EXISTING
    jobs table if they are not already present. Never touches
    existing columns, existing data, or the existing
    UNIQUE(source, job_id) constraint. Does not add candidate_id.

    If the jobs table does not exist yet (a genuinely empty/fresh
    database), this is skipped rather than erroring -- there is
    nothing to alter.
    """
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()

    if table_exists is None:
        print(
            "NOTE: 'jobs' table does not exist yet in this database; "
            "skipping column additions."
        )
        return

    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
    }

    new_columns = [
        ("experience_min_years", "INTEGER"),
        ("experience_max_years", "INTEGER"),
        ("skills_json", "TEXT"),
        ("salary_min", "REAL"),
        ("salary_max", "REAL"),
        ("salary_currency", "TEXT"),
        ("salary_period", "TEXT"),
        ("freshness", "TEXT"),
        ("updated_at", "TEXT"),
        # Phase 14: how this job was actually discovered, distinct from
        # `source` (which board it's ON). NULL/"" for every job written
        # before this phase and for every existing direct adapter,
        # which never set these -- generate_run_report.py treats a
        # blank/NULL discovery_source as "DIRECT" for display. Written
        # by tracker.upsert_job()/search_worker._upsert_job_identity_
        # only() only when the caller's job dict actually provides a
        # value (discover_local.normalize_job()'s own default already
        # makes discovery_source == source for a direct adapter, so in
        # practice this column is populated for every source -- but the
        # column itself stays nullable for a genuinely absent value).
        ("discovery_source", "TEXT"),
        ("discovery_query", "TEXT"),
        ("completeness", "REAL"),
    ]

    for name, col_type in new_columns:
        if name not in existing_columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {col_type}")


def _seed_saroj(conn):
    """
    Seed candidate_id="saroj" and candidate_search_profile version 1,
    sourced entirely from config/profile.json (never hardcoded).
    Idempotent: checks for existing rows before inserting, keyed on
    the deterministic candidate_id for candidates, and on
    (candidate_id, version) for the profile (profile_id is
    autoincrement, not deterministic, so it cannot rely on a natural
    primary-key collision the way candidates can).
    """
    with PROFILE_PATH.open("r", encoding="utf-8") as f:
        profile = json.load(f)

    candidate_id = "saroj"
    name = profile.get("candidate", {}).get("name")

    if not name:
        raise ValueError(
            "config/profile.json is missing candidate.name; "
            "cannot seed the Saroj candidate without it."
        )

    now = _now()

    existing_candidate = conn.execute(
        "SELECT candidate_id FROM candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()

    if existing_candidate is None:
        conn.execute(
            """
            INSERT INTO candidates (
                candidate_id, name, email, phone,
                created_at, updated_at, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (candidate_id, name, None, None, now, now, "ACTIVE"),
        )

    existing_profile = conn.execute(
        """
        SELECT profile_id FROM candidate_search_profile
        WHERE candidate_id = ? AND version = 1
        """,
        (candidate_id,),
    ).fetchone()

    if existing_profile is None:
        profile_json = json.dumps(profile)

        conn.execute(
            """
            INSERT INTO candidate_search_profile (
                candidate_id, version, search_mode, profile_json,
                confirmed_by_user, is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (candidate_id, 1, "PROFILE", profile_json, 1, 1, now, now),
        )


def migrate():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    _create_new_tables(conn)
    _ensure_job_columns(conn)
    _seed_saroj(conn)

    conn.commit()
    conn.close()


def _file_stats(path):
    if not path.exists():
        return None

    data = path.read_bytes()

    return {
        "size": len(data),
        "mtime": path.stat().st_mtime,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _row_counts():
    conn = sqlite3.connect(DB_PATH)

    tables = [
        "candidates",
        "candidate_search_profile",
        "resumes",
        "candidate_job_matches",
        "search_runs",
        "jobs",
    ]

    counts = {}

    for table in tables:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()

        counts[table] = (
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if exists
            else None
        )

    conn.close()
    return counts


def main():
    print("MIGRATE V2 SCHEMA")
    print("=================")
    print(f"Database: {DB_PATH}")
    print()

    before = _file_stats(DB_PATH)

    if before:
        print("Before migration:")
        print(f"  size   : {before['size']} bytes")
        print(f"  mtime  : {before['mtime']}")
        print(f"  sha256 : {before['sha256']}")
    else:
        print("Before migration: database file does not exist yet.")

    print()

    migrate()

    after = _file_stats(DB_PATH)

    print("After migration:")
    print(f"  size   : {after['size']} bytes")
    print(f"  mtime  : {after['mtime']}")
    print(f"  sha256 : {after['sha256']}")
    print()

    counts = _row_counts()

    print("Row counts after migration:")
    for table, count in counts.items():
        print(f"  {table:<25}: {count}")


if __name__ == "__main__":
    main()
