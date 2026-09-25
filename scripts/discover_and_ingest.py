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


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python3 scripts/discover_and_ingest.py <jobs.json>"
        )
        sys.exit(1)

    path = ROOT / sys.argv[1]

    if not path.exists():
        print(f"ERROR: file not found: {path}")
        sys.exit(1)

    raw_jobs = load_jobs(path)

    normalized = [
        normalize_job(job, index)
        for index, job in enumerate(raw_jobs, start=1)
    ]

    unique_jobs, duplicate_count = deduplicate(normalized)

    new_jobs, already_tracked = filter_new_jobs(
        unique_jobs
    )

    conn = connect()

    results = []
    excluded = []

    for job in new_jobs:
        eligibility_result = assess_job_eligibility(job, PROFILE)

        if not eligibility_result.eligible:
            excluded.append(
                {
                    "job": job,
                    "eligibility_result": eligibility_result,
                }
            )
            continue

        scoring = score_job(
            job, PROFILE, experience_assessment=eligibility_result.experience_assessment
        )

        upsert_job(
            conn,
            job,
            scoring,
        )

        results.append(
            {
                "job": job,
                "scoring": scoring,
            }
        )

    commit(conn)
    close(conn)

    print("Discovery + ingestion result")
    print("============================")
    print(f"Raw jobs          : {len(raw_jobs)}")
    print(f"Duplicates        : {duplicate_count}")
    print(f"Unique jobs       : {len(unique_jobs)}")
    print(f"Already tracked   : {len(already_tracked)}")
    print(f"Excluded (elig.)  : {len(excluded)}")
    print(f"New jobs ingested : {len(results)}")
    print()

    if excluded:
        print("Excluded (did not pass eligibility gate):")
        for item in excluded:
            job = item["job"]
            eligibility_result = item["eligibility_result"]

            print(
                f"  {job['source']} | "
                f"{job['job_id']} | "
                f"{eligibility_result.reason_code} | "
                f"{eligibility_result.reason}"
            )
        print()

    if already_tracked:
        print("Already tracked:")
        for item in already_tracked:
            job = item["job"]

            print(
                f"  {job['source']} | "
                f"{job['job_id']}"
            )

    print()

    if results:
        print("New jobs ingested:")

        for item in results:
            job = item["job"]
            scoring = item["scoring"]

            print(
                f"  {job['source']} | "
                f"{job['job_id']} | "
                f"{job['company']} | "
                f"{job['title']} | "
                f"{scoring['score']} | "
                f"{scoring['priority']} | "
                f"{scoring['status']}"
            )


if __name__ == "__main__":
    main()
