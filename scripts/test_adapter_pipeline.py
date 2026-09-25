#!/usr/bin/env python3

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import MockJobSourceAdapter, SearchQuery, AdapterHealth
from discover_local import normalize_job, deduplicate
from score_job import score_job, PROFILE


def main():
    adapter = MockJobSourceAdapter()

    print("SOURCE ADAPTER PIPELINE TEST")
    print("============================")

    # --- New adapter contract: health_check() returns AdapterHealth ---
    health = adapter.health_check()

    print(f"Health check        : {health}")

    if not isinstance(health, AdapterHealth):
        print(
            "FAIL: health_check() did not return an AdapterHealth instance."
        )
        sys.exit(1)

    # --- New adapter contract: search() takes a SearchQuery ---
    search_query = SearchQuery(
        role="Senior Site Reliability Engineer",
        location="Bengaluru",
    )

    if not (
        isinstance(search_query, SearchQuery)
        and search_query.role == "Senior Site Reliability Engineer"
        and search_query.location == "Bengaluru"
    ):
        print(
            "FAIL: SearchQuery did not construct with the expected fields."
        )
        sys.exit(1)

    raw_jobs = adapter.search(search_query)

    print(f"Raw jobs discovered : {len(raw_jobs)}")

    if not (
        len(raw_jobs) == 1
        and raw_jobs[0]["source"] == "MOCK"
        and raw_jobs[0]["company"] == "Mock Technology"
        and raw_jobs[0]["title"] == "Senior Site Reliability Engineer"
        and raw_jobs[0]["location"] == "Bengaluru"
    ):
        print(
            "FAIL: MockJobSourceAdapter.search(SearchQuery(...)) did not "
            "return the expected job data."
        )
        sys.exit(1)

    normalized = [
        normalize_job(job, index)
        for index, job in enumerate(raw_jobs, start=1)
    ]

    print(f"Normalized jobs     : {len(normalized)}")

    unique_jobs, duplicate_count = deduplicate(normalized)

    print(f"Unique jobs         : {len(unique_jobs)}")
    print(f"Duplicates          : {duplicate_count}")
    print()

    for job in unique_jobs:
        scoring = score_job(job, PROFILE)

        print("JOB")
        print("---")
        print(f"Job ID      : {job['job_id']}")
        print(f"Source      : {job['source']}")
        print(f"Company     : {job['company']}")
        print(f"Title       : {job['title']}")
        print(f"Location    : {job['location']}")
        print(f"Score       : {scoring['score']}")
        print(f"Priority    : {scoring['priority']}")
        print(f"Status      : {scoring['status']}")
        print(
            "Matched     : "
            + ", ".join(scoring["matched_skills"])
        )

        if scoring["missing_skills"]:
            print(
                "Missing     : "
                + ", ".join(scoring["missing_skills"])
            )

        print()

        expected = (
            job["source"] == "MOCK"
            and job["company"] == "Mock Technology"
            and job["title"] == "Senior Site Reliability Engineer"
            and job["location"] == "Bengaluru"
            and job["job_id"].startswith("MOCK-")
            and scoring["score"] == 100
            and scoring["priority"] == "A"
            and scoring["status"] == "READY_FOR_APPROVAL"
        )

        if expected:
            print("PASS: adapter output successfully passed through pipeline.")
        else:
            print("FAIL: adapter pipeline regression detected.")
            sys.exit(1)


if __name__ == "__main__":
    main()
