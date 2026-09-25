#!/usr/bin/env python3

import json
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "applications" / "jobos.db"


def _use_isolated_db():
    """
    Redirect this test's own DB_PATH to a fresh, unique temporary
    SQLite database, initialized with the normal production schema
    via init_tracker.main() (unmodified schema logic, just pointed at
    a temp location). Keeps this test fully isolated from
    data/applications/jobos.db and repeatable.
    """
    global DB_PATH

    sys.path.insert(0, str(ROOT / "scripts"))
    import init_tracker

    tmp_dir = tempfile.mkdtemp(prefix="jobos_test_tracker_")
    tmp_db_path = Path(tmp_dir) / "jobos_test.db"

    init_tracker.DATA_DIR = Path(tmp_dir)
    init_tracker.DB_PATH = tmp_db_path
    init_tracker.main()

    DB_PATH = tmp_db_path


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def insert_job(connection, job, scoring):
    connection.execute(
        """
        INSERT INTO jobs (
            job_id,
            source,
            company,
            title,
            location,
            work_model,
            job_url,
            application_url,
            posted_date,
            discovered_at,
            jd_text,
            experience_required,
            mandatory_skills,
            preferred_skills,
            score,
            priority,
            matched_skills,
            missing_skills,
            hard_reject_reasons,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, job_id) DO UPDATE SET
            company = excluded.company,
            title = excluded.title,
            location = excluded.location,
            work_model = excluded.work_model,
            job_url = excluded.job_url,
            application_url = excluded.application_url,
            posted_date = excluded.posted_date,
            discovered_at = excluded.discovered_at,
            jd_text = excluded.jd_text,
            experience_required = excluded.experience_required,
            mandatory_skills = excluded.mandatory_skills,
            preferred_skills = excluded.preferred_skills,
            score = excluded.score,
            priority = excluded.priority,
            matched_skills = excluded.matched_skills,
            missing_skills = excluded.missing_skills,
            hard_reject_reasons = excluded.hard_reject_reasons,
            last_updated = CURRENT_TIMESTAMP
        """,
        (
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
            json.dumps(job.get("mandatory_skills", [])),
            json.dumps(job.get("preferred_skills", [])),
            scoring["score"],
            scoring["priority"],
            json.dumps(scoring["matched_skills"]),
            json.dumps(scoring["missing_skills"]),
            json.dumps(scoring["hard_reject_reasons"]),
            scoring["status"]
        )
    )


def main():
    _use_isolated_db()

    test_files = [
        "data/test_job.json",
        "data/test_job_b.json",
        "data/test_job_reject.json"
    ]

    from score_job import score_job, PROFILE

    connection = sqlite3.connect(DB_PATH)

    for filename in test_files:
        job = load_json(ROOT / filename)
        scoring = score_job(job, PROFILE)
        insert_job(connection, job, scoring)

    connection.commit()

    rows = connection.execute(
        """
        SELECT job_id, company, title, score, priority, status
        FROM jobs
        ORDER BY id
        """
    ).fetchall()

    connection.close()

    print("Tracker test results:")

    for row in rows:
        print(
            f"{row[0]} | {row[1]} | {row[2]} | "
            f"{row[3]} | {row[4]} | {row[5]}"
        )

    print(f"\nTotal jobs in tracker: {len(rows)}")


if __name__ == "__main__":
    main()
