#!/usr/bin/env python3

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import init_dev_db
import init_tracker
import migrate_v2_schema

# A throwaway fixture profile, used only by the seeding tests below --
# never the real, gitignored config/profile.json (which may not even
# exist on a fresh clone/CI runner). Deliberately NOT "Saroj Kumar
# Nayak" or any real name, so these tests can never depend on or leak
# real personal data.
_FIXTURE_PROFILE = {"candidate": {"name": "Test Fixture Candidate"}}


NEW_TABLES = [
    "candidates",
    "resumes",
    "resume_profile_fields",
    "candidate_search_profile",
    "candidate_job_matches",
    "search_runs",
    "search_queue",
]

NEW_JOB_COLUMNS = [
    "experience_min_years",
    "experience_max_years",
    "skills_json",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "freshness",
    "updated_at",
]


def _use_isolated_db(with_jobs_table=True):
    """
    Point both init_tracker and migrate_v2_schema at a fresh, unique
    temporary SQLite database. If with_jobs_table is True (the
    realistic production scenario), the existing jobs schema is
    created first via the unmodified init_tracker.main() logic,
    mirroring how the real production database already has a jobs
    table before this migration ever runs against it.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_migrate_v2_"))
    tmp_db_path = tmp_dir / "jobos_test.db"

    init_tracker.DATA_DIR = tmp_dir
    init_tracker.DB_PATH = tmp_db_path

    if with_jobs_table:
        init_tracker.main()

    migrate_v2_schema.DATA_DIR = tmp_dir
    migrate_v2_schema.DB_PATH = tmp_db_path

    # Point at an isolated fixture profile, never the real (gitignored,
    # possibly-absent) config/profile.json -- see _FIXTURE_PROFILE.
    fixture_profile_path = tmp_dir / "fixture_profile.json"
    fixture_profile_path.write_text(json.dumps(_FIXTURE_PROFILE), encoding="utf-8")
    migrate_v2_schema.PROFILE_PATH = fixture_profile_path

    return tmp_db_path


def _table_names(db_path):
    conn = sqlite3.connect(db_path)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    return names


def _column_names(db_path, table):
    conn = sqlite3.connect(db_path)
    names = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    conn.close()
    return names


def test_fresh_database_creates_all_tables():
    db_path = _use_isolated_db()
    migrate_v2_schema.migrate()

    tables = _table_names(db_path)
    missing = [t for t in NEW_TABLES if t not in tables]

    assert not missing, f"Missing tables after migration: {missing}"
    assert "jobs" in tables, "jobs table should still exist"

    job_columns = _column_names(db_path, "jobs")
    missing_columns = [c for c in NEW_JOB_COLUMNS if c not in job_columns]
    assert not missing_columns, f"Missing new jobs columns: {missing_columns}"

    print("PASS: fresh database creates all required tables and jobs columns")


def test_migration_is_idempotent():
    db_path = _use_isolated_db()
    migrate_v2_schema.migrate(seed_legacy_candidate=True)

    conn = sqlite3.connect(db_path)
    tables_after_first = _table_names(db_path)
    candidates_after_first = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    profiles_after_first = conn.execute(
        "SELECT COUNT(*) FROM candidate_search_profile"
    ).fetchone()[0]
    conn.close()

    # Run again.
    migrate_v2_schema.migrate(seed_legacy_candidate=True)

    conn = sqlite3.connect(db_path)
    tables_after_second = _table_names(db_path)
    candidates_after_second = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    profiles_after_second = conn.execute(
        "SELECT COUNT(*) FROM candidate_search_profile"
    ).fetchone()[0]
    conn.close()

    assert tables_after_first == tables_after_second, "Schema changed on second run"
    assert candidates_after_first == candidates_after_second == 1, (
        f"Expected exactly 1 candidate after each run, got "
        f"{candidates_after_first} then {candidates_after_second}"
    )
    assert profiles_after_first == profiles_after_second == 1, (
        f"Expected exactly 1 profile after each run, got "
        f"{profiles_after_first} then {profiles_after_second}"
    )

    print("PASS: running migration twice is idempotent")


def test_legacy_candidate_seeded_once_when_opted_in():
    _use_isolated_db()
    migrate_v2_schema.migrate(seed_legacy_candidate=True)
    migrate_v2_schema.migrate(seed_legacy_candidate=True)

    conn = sqlite3.connect(migrate_v2_schema.DB_PATH)
    rows = conn.execute(
        "SELECT candidate_id, name, status FROM candidates WHERE candidate_id = ?",
        (migrate_v2_schema.LEGACY_CANDIDATE_ID,),
    ).fetchall()
    conn.close()

    assert len(rows) == 1, f"Expected exactly one legacy candidate row, got {len(rows)}"
    assert rows[0][1] == _FIXTURE_PROFILE["candidate"]["name"], (
        "Legacy candidate's name should come from the fixture profile, never a hardcoded real name"
    )
    assert rows[0][2] == "ACTIVE"

    print(f"PASS: legacy candidate seeded exactly once when opted in (name={rows[0][1]!r})")


def test_legacy_candidate_profile_v1_seeded_once_when_opted_in():
    _use_isolated_db()
    migrate_v2_schema.migrate(seed_legacy_candidate=True)
    migrate_v2_schema.migrate(seed_legacy_candidate=True)

    conn = sqlite3.connect(migrate_v2_schema.DB_PATH)
    rows = conn.execute(
        "SELECT version, search_mode FROM candidate_search_profile WHERE candidate_id = ? AND version = 1",
        (migrate_v2_schema.LEGACY_CANDIDATE_ID,),
    ).fetchall()
    conn.close()

    assert len(rows) == 1, f"Expected exactly one v1 profile row, got {len(rows)}"
    assert rows[0][1] == "PROFILE"

    print("PASS: legacy candidate profile version 1 seeded exactly once when opted in")


def test_legacy_candidate_profile_confirmed_and_active_when_opted_in():
    _use_isolated_db()
    migrate_v2_schema.migrate(seed_legacy_candidate=True)

    conn = sqlite3.connect(migrate_v2_schema.DB_PATH)
    row = conn.execute(
        "SELECT confirmed_by_user, is_active FROM candidate_search_profile WHERE candidate_id = ? AND version = 1",
        (migrate_v2_schema.LEGACY_CANDIDATE_ID,),
    ).fetchone()
    conn.close()

    assert row == (1, 1), f"Expected (confirmed_by_user=1, is_active=1), got {row}"

    print("PASS: legacy candidate profile v1 is confirmed_by_user=1 and is_active=1")


def test_migrate_default_seeds_no_candidate():
    """
    The release-readiness fix: migrate() with its default argument
    (seed_legacy_candidate=False, as every real caller in this
    repository -- init_dev_db.py, migrate_production_schema.py -- uses
    it) must create zero candidates. No one's real name, and no
    candidate_id, is ever seeded implicitly.
    """
    db_path = _use_isolated_db()
    migrate_v2_schema.migrate()

    conn = sqlite3.connect(db_path)
    candidate_count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    rows = conn.execute("SELECT candidate_id, name FROM candidates").fetchall()
    conn.close()

    assert candidate_count == 0, f"Expected zero candidates by default, got {candidate_count}: {rows}"
    assert all(r[0] != "saroj" for r in rows), f"'saroj' candidate_id must not appear by default: {rows}"
    assert all((r[1] or "") != "Saroj Kumar Nayak" for r in rows), (
        f"Real personal name must never appear by default: {rows}"
    )

    print("PASS: migrate() with default arguments seeds zero candidates")


def test_fresh_dev_db_has_no_candidates_and_no_real_name():
    """
    The actual path every normal developer/friend hits
    (api/db.py -> init_dev_db.init_dev_db()) must produce a completely
    empty, generic candidates table -- confirming init_dev_db.py's own
    behavior is unchanged by this release-readiness fix.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_fresh_dev_db_"))
    tmp_db_path = tmp_dir / "jobos_dev_test.db"

    init_dev_db.init_dev_db(tmp_db_path)

    conn = sqlite3.connect(tmp_db_path)
    candidate_count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    rows = conn.execute("SELECT candidate_id, name FROM candidates").fetchall()
    conn.close()

    assert candidate_count == 0, f"Expected zero candidates from a fresh dev DB, got {candidate_count}: {rows}"
    assert all(r[0] != "saroj" for r in rows), f"'saroj' must not appear in a fresh dev DB: {rows}"
    assert all((r[1] or "") != "Saroj Kumar Nayak" for r in rows), (
        f"Real personal name must never appear in a fresh dev DB: {rows}"
    )

    print("PASS: a fresh init_dev_db.py database has zero candidates and no real personal data")


def test_existing_jobs_remain_global_and_unaltered():
    db_path = _use_isolated_db()

    # Insert one pre-existing job row, matching the shape the real
    # production database already has, BEFORE running the migration.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO jobs (job_id, source, company, title, status)
        VALUES ('JOB-1', 'MOCK', 'Acme Corp', 'SRE', 'FOUND')
        """
    )
    conn.commit()
    conn.close()

    migrate_v2_schema.migrate()

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT job_id, source, company, title, status FROM jobs WHERE job_id = 'JOB-1'"
    ).fetchone()

    # No candidate_id column should have been added to jobs.
    columns = _column_names(db_path, "jobs")
    conn.close()

    assert row == ("JOB-1", "MOCK", "Acme Corp", "SRE", "FOUND"), (
        f"Existing job row was altered: {row}"
    )
    assert "candidate_id" not in columns, "jobs table must NOT gain a candidate_id column"

    print("PASS: existing job rows remain global and unaltered; no candidate_id added to jobs")


def test_unique_source_job_id_constraint_preserved():
    db_path = _use_isolated_db()
    migrate_v2_schema.migrate()

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO jobs (job_id, source, company, title, status)
        VALUES ('JOB-DUP', 'MOCK', 'Acme Corp', 'SRE', 'FOUND')
        """
    )
    conn.commit()

    raised = False
    try:
        conn.execute(
            """
            INSERT INTO jobs (job_id, source, company, title, status)
            VALUES ('JOB-DUP', 'MOCK', 'Other Corp', 'Other Title', 'FOUND')
            """
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raised = True
    finally:
        conn.close()

    assert raised, "Duplicate (source, job_id) insert should have raised IntegrityError"

    print("PASS: existing UNIQUE(source, job_id) constraint on jobs still enforced")


def test_two_candidates_share_one_global_job():
    db_path = _use_isolated_db()
    migrate_v2_schema.migrate()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute(
        """
        INSERT INTO jobs (job_id, source, company, title, status)
        VALUES ('JOB-SHARED', 'MOCK', 'Acme Corp', 'SRE', 'FOUND')
        """
    )

    now = migrate_v2_schema._now()

    conn.execute(
        """
        INSERT INTO candidates (candidate_id, name, created_at, updated_at, status)
        VALUES ('candidate-a', 'Test Candidate A', ?, ?, 'ACTIVE')
        """,
        (now, now),
    )

    conn.execute(
        """
        INSERT INTO candidates (candidate_id, name, created_at, updated_at, status)
        VALUES ('candidate-b', 'Test Candidate B', ?, ?, 'ACTIVE')
        """,
        (now, now),
    )

    conn.execute(
        """
        INSERT INTO candidate_job_matches (
            candidate_id, job_id, fit_score, priority, created_at, updated_at
        )
        VALUES ('candidate-a', 'JOB-SHARED', 95, 'A', ?, ?)
        """,
        (now, now),
    )

    conn.execute(
        """
        INSERT INTO candidate_job_matches (
            candidate_id, job_id, fit_score, priority, created_at, updated_at
        )
        VALUES ('candidate-b', 'JOB-SHARED', 72, 'C', ?, ?)
        """,
        (now, now),
    )

    conn.commit()

    job_count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE job_id = 'JOB-SHARED'"
    ).fetchone()[0]

    match_count = conn.execute(
        "SELECT COUNT(*) FROM candidate_job_matches WHERE job_id = 'JOB-SHARED'"
    ).fetchone()[0]

    conn.close()

    assert job_count == 1, f"Expected exactly one global job row, got {job_count}"
    assert match_count == 2, f"Expected exactly two candidate match rows, got {match_count}"

    print(
        "PASS: two candidates reference the same global job via "
        "candidate_job_matches without duplicating the job"
    )


def test_candidates_have_different_match_records_and_are_not_confused():
    db_path = _use_isolated_db()
    migrate_v2_schema.migrate()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute(
        """
        INSERT INTO jobs (job_id, source, company, title, status)
        VALUES ('JOB-SHARED-2', 'MOCK', 'Acme Corp', 'SRE', 'FOUND')
        """
    )

    now = migrate_v2_schema._now()

    conn.execute(
        """
        INSERT INTO candidates (candidate_id, name, created_at, updated_at, status)
        VALUES ('candidate-d', 'Test Candidate D', ?, ?, 'ACTIVE')
        """,
        (now, now),
    )

    conn.execute(
        """
        INSERT INTO candidates (candidate_id, name, created_at, updated_at, status)
        VALUES ('candidate-c', 'Test Candidate C', ?, ?, 'ACTIVE')
        """,
        (now, now),
    )

    conn.execute(
        """
        INSERT INTO candidate_job_matches (
            candidate_id, job_id, fit_score, priority, created_at, updated_at
        )
        VALUES ('candidate-d', 'JOB-SHARED-2', 95, 'A', ?, ?)
        """,
        (now, now),
    )

    conn.execute(
        """
        INSERT INTO candidate_job_matches (
            candidate_id, job_id, fit_score, priority, created_at, updated_at
        )
        VALUES ('candidate-c', 'JOB-SHARED-2', 40, 'REJECT', ?, ?)
        """,
        (now, now),
    )

    conn.commit()

    candidate_d_row = conn.execute(
        """
        SELECT fit_score, priority FROM candidate_job_matches
        WHERE candidate_id = 'candidate-d' AND job_id = 'JOB-SHARED-2'
        """
    ).fetchone()

    candidate_c_row = conn.execute(
        """
        SELECT fit_score, priority FROM candidate_job_matches
        WHERE candidate_id = 'candidate-c' AND job_id = 'JOB-SHARED-2'
        """
    ).fetchone()

    conn.close()

    assert candidate_d_row == (95, "A"), f"Candidate D's match record is wrong: {candidate_d_row}"
    assert candidate_c_row == (40, "REJECT"), (
        f"Candidate C's match record is wrong: {candidate_c_row}"
    )
    assert candidate_d_row != candidate_c_row, "Match records must not be confused between candidates"

    print(
        "PASS: distinct candidate match records for the same job are "
        "stored correctly and never confused"
    )


def test_foreign_keys_enforced():
    db_path = _use_isolated_db()
    migrate_v2_schema.migrate()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute(
        """
        INSERT INTO jobs (job_id, source, company, title, status)
        VALUES ('JOB-FK', 'MOCK', 'Acme Corp', 'SRE', 'FOUND')
        """
    )
    conn.commit()

    now = migrate_v2_schema._now()

    raised = False
    try:
        conn.execute(
            """
            INSERT INTO candidate_job_matches (
                candidate_id, job_id, fit_score, priority, created_at, updated_at
            )
            VALUES ('nonexistent-candidate', 'JOB-FK', 50, 'C', ?, ?)
            """,
            (now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raised = True
    finally:
        conn.close()

    assert raised, (
        "Inserting a candidate_job_matches row for a nonexistent "
        "candidate_id should raise a foreign key IntegrityError"
    )

    print("PASS: foreign key constraints are enforced where configured")


def main():
    print("MIGRATE V2 SCHEMA TEST SUITE")
    print("============================")
    print()

    tests = [
        test_fresh_database_creates_all_tables,
        test_migration_is_idempotent,
        test_legacy_candidate_seeded_once_when_opted_in,
        test_legacy_candidate_profile_v1_seeded_once_when_opted_in,
        test_legacy_candidate_profile_confirmed_and_active_when_opted_in,
        test_migrate_default_seeds_no_candidate,
        test_fresh_dev_db_has_no_candidates_and_no_real_name,
        test_existing_jobs_remain_global_and_unaltered,
        test_unique_source_job_id_constraint_preserved,
        test_two_candidates_share_one_global_job,
        test_candidates_have_different_match_records_and_are_not_confused,
        test_foreign_keys_enforced,
    ]

    failures = []

    for test in tests:
        try:
            test()
        except AssertionError as error:
            failures.append(f"{test.__name__}: {error}")
            print(f"FAIL: {test.__name__}: {error}")
        except Exception as error:  # noqa: BLE001 -- report anything unexpected as a failure
            failures.append(f"{test.__name__}: unexpected {type(error).__name__}: {error}")
            print(f"FAIL: {test.__name__}: unexpected {type(error).__name__}: {error}")

    print()

    if failures:
        print(f"{len(failures)} of {len(tests)} tests FAILED.")
        sys.exit(1)

    print(f"All {len(tests)} tests passed.")
    print()
    print(
        "NOTE: every test above ran against its own isolated temporary "
        "SQLite database via migrate_v2_schema.DB_PATH redirection -- "
        "the production database (data/applications/jobos.db) was never "
        "opened by this test file."
    )


if __name__ == "__main__":
    main()
