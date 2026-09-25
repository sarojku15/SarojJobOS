#!/usr/bin/env python3

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts"))

import init_tracker
import tracker
from source_registry import discover_from_sources
from discover_local import normalize_job, deduplicate
from score_job import score_job, PROFILE
from tracker import connect, upsert_job, commit, close


QUERIES = [
    {
        "source": "MOCK",
        "role": "Senior Site Reliability Engineer",
        "location": "Bengaluru",
    },
    {
        "source": "MOCK",
        "role": "Senior DevOps Engineer",
        "location": "Hyderabad",
    },
]


def _use_isolated_tracker_db():
    """
    Redirect tracker.DB_PATH to a fresh, unique temporary SQLite
    database for this run, initialized with the normal production
    schema via init_tracker.main() (unmodified schema logic, just
    pointed at a temp location). This makes every run of this test
    fully isolated from data/applications/jobos.db and repeatable --
    it can never again inherit leftover rows from an earlier session.
    """
    tmp_dir = tempfile.mkdtemp(prefix="jobos_test_registry_pipeline_")
    tmp_db_path = Path(tmp_dir) / "jobos_test.db"

    init_tracker.DATA_DIR = Path(tmp_dir)
    init_tracker.DB_PATH = tmp_db_path
    init_tracker.main()

    tracker.DB_PATH = tmp_db_path


def run_pipeline():
    raw_jobs = discover_from_sources(QUERIES)

    normalized = [
        normalize_job(job, index)
        for index, job in enumerate(raw_jobs, start=1)
    ]

    unique_jobs, duplicate_count = deduplicate(normalized)

    conn = connect()

    results = []

    for job in unique_jobs:
        scoring = score_job(job, PROFILE)

        upsert_job(
            conn,
            job,
            scoring,
        )

        results.append(
            (
                job,
                scoring,
            )
        )

    commit(conn)
    close(conn)

    return raw_jobs, unique_jobs, duplicate_count, results


def tracker_count():
    conn = sqlite3.connect(tracker.DB_PATH)

    count = conn.execute("""
        SELECT COUNT(*)
        FROM jobs
        WHERE source = 'MOCK'
    """).fetchone()[0]

    conn.close()

    return count


def main():
    _use_isolated_tracker_db()

    print("REGISTRY → FULL PIPELINE TEST")
    print("==============================")

    before = tracker_count()

    print(f"MOCK tracker records before : {before}")

    raw, unique, duplicates, results = run_pipeline()

    after_first = tracker_count()

    print()
    print(f"Raw jobs discovered         : {len(raw)}")
    print(f"Unique jobs                 : {len(unique)}")
    print(f"Duplicates                  : {duplicates}")
    print(f"MOCK records after run #1   : {after_first}")

    for job, scoring in results:
        print(
            f"  {job['job_id']} | "
            f"{job['company']} | "
            f"{job['title']} | "
            f"{scoring['score']} | "
            f"{scoring['priority']} | "
            f"{scoring['status']}"
        )

    # Run exactly the same discovery again.
    run_pipeline()

    after_second = tracker_count()

    print()
    print(
        f"MOCK records after run #2   : {after_second}"
    )

    expected_first = (
        len(raw) == 2
        and len(unique) == 2
        and duplicates == 0
        and after_first == before + 2
    )

    expected_second = (
        after_second == after_first
    )

    print()
    print("CHECK RESULTS")
    print("=============")

    print(
        "PASS: first run inserted exactly two MOCK jobs."
        if expected_first
        else
        "FAIL: first run inserted an unexpected number of jobs."
    )

    print(
        "PASS: second identical run created no duplicates."
        if expected_second
        else
        "FAIL: second run created duplicate tracker records."
    )

    if not (expected_first and expected_second):
        sys.exit(1)

    print()
    print(
        "ALL REGISTRY PIPELINE TESTS PASSED."
    )


if __name__ == "__main__":
    main()
