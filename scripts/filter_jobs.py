#!/usr/bin/env python3

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data/applications/jobos.db"

sys.path.insert(0, str(ROOT / "scripts"))

from discover_local import load_jobs, normalize_job, deduplicate


def filter_new_jobs(jobs):
    conn = sqlite3.connect(DB_PATH)

    eligible = []
    already_tracked = []

    for job in jobs:
        row = conn.execute(
            """
            SELECT status
            FROM jobs
            WHERE source = ?
              AND job_id = ?
            """,
            (job["source"], job["job_id"]),
        ).fetchone()

        if row:
            already_tracked.append(
                {
                    "job": job,
                    "status": row[0],
                }
            )
        else:
            eligible.append(job)

    conn.close()

    return eligible, already_tracked


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python3 scripts/filter_jobs.py <jobs.json>"
        )
        sys.exit(1)

    path = ROOT / sys.argv[1]

    if not path.exists():
        print(f"ERROR: file not found: {path}")
        sys.exit(1)

    raw_jobs = load_jobs(path)

    normalized_jobs = [
        normalize_job(job, index)
        for index, job in enumerate(raw_jobs, start=1)
    ]

    unique_jobs, duplicate_count = deduplicate(
        normalized_jobs
    )

    eligible, tracked = filter_new_jobs(
        unique_jobs
    )

    print("Job filter result")
    print("=================")
    print(f"Input jobs          : {len(raw_jobs)}")
    print(f"Duplicates removed  : {duplicate_count}")
    print(f"Unique jobs         : {len(unique_jobs)}")
    print(f"Already tracked     : {len(tracked)}")
    print(f"New jobs            : {len(eligible)}")
    print()

    if tracked:
        print("Already tracked:")

        for item in tracked:
            job = item["job"]

            print(
                f"  {job['source']} | "
                f"{job['job_id']} | "
                f"{item['status']}"
            )

    print()

    if eligible:
        print("New jobs:")

        for job in eligible:
            print(
                f"  {job['source']} | "
                f"{job['job_id']} | "
                f"{job['company']} | "
                f"{job['title']}"
            )


if __name__ == "__main__":
    main()
