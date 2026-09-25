#!/usr/bin/env python3

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "applications"
DB_PATH = DATA_DIR / "jobos.db"


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            source TEXT NOT NULL,
            company TEXT,
            title TEXT,
            location TEXT,
            work_model TEXT,
            job_url TEXT,
            application_url TEXT,
            posted_date TEXT,
            discovered_at TEXT,
            jd_text TEXT,
            experience_required TEXT,

            mandatory_skills TEXT,
            preferred_skills TEXT,

            score INTEGER DEFAULT 0,
            priority TEXT,
            matched_skills TEXT,
            missing_skills TEXT,
            hard_reject_reasons TEXT,

            resume_variant TEXT,
            status TEXT NOT NULL DEFAULT 'FOUND',

            screening_questions TEXT,
            screening_answers TEXT,

            application_started_at TEXT,
            application_submitted_at TEXT,

            recruiter_name TEXT,
            recruiter_email TEXT,
            recruiter_contact TEXT,

            interview_round TEXT,
            interview_date TEXT,
            follow_up_date TEXT,

            notes TEXT,
            last_updated TEXT,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(source, job_id)
        )
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_status
        ON jobs(status)
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_priority
        ON jobs(priority)
    """)

    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_jobs_company
        ON jobs(company)
    """)

    connection.commit()
    connection.close()

    print(f"Tracker initialized: {DB_PATH}")


if __name__ == "__main__":
    main()
