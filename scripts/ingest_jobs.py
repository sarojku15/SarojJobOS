#!/usr/bin/env python3

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts"))

from discover_local import load_jobs, normalize_job, deduplicate
from filter_jobs import filter_new_jobs
from score_job import score_job, PROFILE
from job_eligibility import assess_job_eligibility
from tracker import connect, upsert_job, commit, close


MIN_SCORE = 70


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/ingest_jobs.py <jobs.json>")
        sys.exit(1)

    input_path = ROOT / sys.argv[1]

    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)

    raw_jobs = load_jobs(input_path)

    normalized = [
        normalize_job(job, index)
        for index, job in enumerate(raw_jobs, start=1)
    ]

    unique_jobs, duplicate_count = deduplicate(normalized)
    new_jobs, already_tracked = filter_new_jobs(unique_jobs)

    conn = connect()

    accepted = 0
    rejected = 0
    excluded = 0

    print("JOB INTAKE RESULT")
    print("=================")
    print(f"Raw jobs         : {len(raw_jobs)}")
    print(f"Duplicates       : {duplicate_count}")
    print(f"Unique jobs      : {len(unique_jobs)}")
    print(f"Already tracked  : {len(already_tracked)}")
    print()

    for job in new_jobs:
        eligibility_result = assess_job_eligibility(job, PROFILE)

        if not eligibility_result.eligible:
            excluded += 1

            print(
                f"EXCLUDE | {eligibility_result.reason_code} | "
                f"{job['source']} | "
                f"{job['company']} | "
                f"{job['title']} | "
                f"{job['job_id']}"
            )
            continue

        scoring = score_job(
            job, PROFILE, experience_assessment=eligibility_result.experience_assessment
        )

        if scoring["score"] >= MIN_SCORE and scoring["priority"] != "REJECT":
            upsert_job(conn, job, scoring)
            accepted += 1

            print(
                f"ACCEPT | {scoring['score']:>3} | "
                f"{scoring['priority']} | "
                f"{job['source']} | "
                f"{job['company']} | "
                f"{job['title']} | "
                f"{job['job_id']}"
            )
        else:
            rejected += 1

            print(
                f"REJECT | {scoring['score']:>3} | "
                f"{job['source']} | "
                f"{job['company']} | "
                f"{job['title']} | "
                f"{job['job_id']}"
            )

    commit(conn)
    close(conn)

    print()
    print("SUMMARY")
    print("-------")
    print(f"Accepted >=70   : {accepted}")
    print(f"Rejected <70    : {rejected}")
    print(f"Excluded (elig.): {excluded}")
    print(f"Already tracked : {len(already_tracked)}")


if __name__ == "__main__":
    main()
