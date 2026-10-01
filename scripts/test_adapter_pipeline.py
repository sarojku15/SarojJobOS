#!/usr/bin/env python3

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import MockJobSourceAdapter, SearchQuery, AdapterHealth
from discover_local import normalize_job, deduplicate
from score_job import score_job

# A self-contained fixture profile, deliberately NOT score_job.PROFILE
# (which only exists for the legacy CLI's own entry point and falls
# back to config/profile.json.example's generic values whenever no
# local config/profile.json exists -- see score_job.py's own comment).
# This test asserts a specific score (100/A) against a specific mock
# job, so it needs a profile with known, fixed values of its own --
# never depending on whichever config/profile.json an operator's local
# machine happens to have.
FIXTURE_PROFILE = {
    "candidate": {
        "name": "Test Fixture Candidate",
        "current_title": "Senior Site Reliability Engineer",
        "experience_years": 11,
    },
    "target_roles": ["Senior Site Reliability Engineer", "Senior SRE"],
    "target_locations": ["Bengaluru", "Bangalore", "Remote"],
    "cloud": ["AWS", "Azure"],
    "kubernetes": ["Kubernetes", "EKS", "AKS", "Docker", "Helm"],
    "iac_and_automation": ["Terraform", "Ansible"],
    "cicd_and_devops": ["Jenkins", "GitHub Actions", "ArgoCD"],
    "observability": ["Prometheus", "Grafana", "Splunk", "Dynatrace"],
    "sre": ["SLI", "SLO", "SLA", "Error Budgets", "Incident Management"],
    "programming_and_scripting": ["Python", "Shell"],
    "operating_systems": ["Linux"],
    "security_and_governance": [],
    "tools": ["Git", "Jira"],
    "certifications": [],
    "education": [],
    "resume_variants": {"primary": "SRE-A"},
    "application_rules": {"minimum_experience_years": 5, "preferred_experience_years": 8},
}


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
        scoring = score_job(job, FIXTURE_PROFILE)

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
