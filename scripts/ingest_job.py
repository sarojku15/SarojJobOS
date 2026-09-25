#!/usr/bin/env python3

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts"))

from score_job import score_job, PROFILE
from job_eligibility import assess_job_eligibility
from tracker import connect, upsert_job, commit, close
from discover_local import load_jobs


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/ingest_job.py <job.json>")
        sys.exit(1)

    job_path = ROOT / sys.argv[1]

    if not job_path.exists():
        print(f"ERROR: File not found: {job_path}")
        sys.exit(1)

    jobs = load_jobs(job_path)

    if len(jobs) != 1:
        print(
            f"ERROR: Expected exactly one job, "
            f"but found {len(jobs)}"
        )
        sys.exit(1)

    job = jobs[0]

    # Normalize through the same discovery layer used by
    # the batch pipeline.
    from discover_local import normalize_job

    job = normalize_job(job, 1)

    eligibility_result = assess_job_eligibility(job, PROFILE)

    if not eligibility_result.eligible:
        print("Job ingestion result")
        print("--------------------")
        print(f"Job ID       : {job['job_id']}")
        print(f"Source       : {job['source']}")
        print(f"Company      : {job['company']}")
        print(f"Title        : {job['title']}")
        print(f"Location     : {job['location']}")
        print(f"Status       : EXCLUDED ({eligibility_result.reason_code})")
        print(f"Reason       : {eligibility_result.reason}")
        print()
        print(
            "Job was NOT scored or written to the tracker -- it did not "
            "pass the shared experience/location eligibility gate."
        )
        return

    scoring = score_job(
        job, PROFILE, experience_assessment=eligibility_result.experience_assessment
    )

    conn = connect()

    upsert_job(
        conn,
        job,
        scoring,
    )

    commit(conn)
    close(conn)

    print("Job ingestion result")
    print("--------------------")
    print(f"Job ID       : {job['job_id']}")
    print(f"Source       : {job['source']}")
    print(f"Company      : {job['company']}")
    print(f"Title        : {job['title']}")
    print(f"Location     : {job['location']}")
    print(f"Score        : {scoring['score']}")
    print(f"Priority     : {scoring['priority']}")
    print(f"Status       : {scoring['status']}")

    if scoring["matched_skills"]:
        print(
            "Matched      : "
            + ", ".join(scoring["matched_skills"])
        )

    if scoring["missing_skills"]:
        print(
            "Missing      : "
            + ", ".join(scoring["missing_skills"])
        )

    if scoring["hard_reject_reasons"]:
        print("Reject reasons:")

        for reason in scoring["hard_reject_reasons"]:
            print(f"  - {reason}")


if __name__ == "__main__":
    main()
