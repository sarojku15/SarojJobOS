#!/usr/bin/env python3

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "scripts"))

from job_id import generate_job_id
import re


def normalize_url(value):
    value = str(value or "").strip()

    # Markdown: [display text](https://actual-url)
    if "](" in value and ")" in value:
        destination = value.split("](", 1)[1].rsplit(")", 1)[0].strip()
        if destination.startswith(("http://", "https://")):
            return destination

    # Extract a normal HTTP(S) URL if embedded in surrounding text.
    match = re.search(r"https?://[^\s)]+", value)
    if match:
        return match.group(0).rstrip("]")

    return value.strip("[]")


def load_jobs(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise ValueError(
            "Input must be a JSON object or an array of job objects"
        )

    return data


def normalize_job(job, index):
    required = ["company", "title"]

    for field in required:
        if not job.get(field):
            raise ValueError(
                f"Job #{index}: missing required field '{field}'"
            )

    normalized = {
        "source": str(job.get("source", "LOCAL")).strip(),
        "company": str(job["company"]).strip(),
        "title": str(job["title"]).strip(),
        "location": str(job.get("location", "")).strip(),
        "work_model": str(job.get("work_model", "")).strip(),
        "job_url": normalize_url(job.get("job_url", "")),
        "application_url": normalize_url(
            job.get("application_url", "")
        ),
        "posted_date": str(
            job.get("posted_date", "")
        ).strip(),
        "discovered_at": str(
            job.get("discovered_at", "")
        ).strip(),
        # Additive (Phase 13 broad-discovery): WHERE this job's URL/data
        # was actually found (job_source) vs. HOW it was found
        # (discovery_source) -- distinct concepts once a job can arrive
        # via a search-provider lookup or a manually-pasted import
        # rather than a direct adapter. Both default to `source` so
        # every existing caller (a direct adapter's own raw dict, which
        # never sets these) is completely unaffected: job_source ==
        # discovery_source == source, exactly today's behavior.
        "job_source": str(
            job.get("job_source", "") or job.get("source", "")
        ).strip(),
        "discovery_source": str(
            job.get("discovery_source", "") or job.get("source", "")
        ).strip(),
        # Additive (Phase 14): the exact site-scoped search-provider
        # query string that found this job (blank for a direct
        # adapter/manual import, which never set this), and a 0..1
        # "how much did the search result itself give us" completeness
        # score (None when not computed -- never fabricated as 1.0 for
        # a source that never actually assessed it).
        "discovery_query": str(job.get("discovery_query", "") or "").strip(),
        "completeness": job.get("completeness"),
        "jd_text": str(
            job.get("jd_text", "")
        ).strip(),
        "experience_required": str(
            job.get("experience_required", "")
        ).strip(),
        "mandatory_skills": job.get(
            "mandatory_skills", []
        ),
        "preferred_skills": job.get(
            "preferred_skills", []
        ),
        "experience_min_months": job.get("experience_min_months"),
    }

    # Preserve a source-provided ID when available.
    # Otherwise generate a deterministic ID.
    normalized["job_id"] = generate_job_id(job)

    return normalized


def deduplicate(jobs):
    unique = {}
    duplicates = 0

    for job in jobs:
        key = (
            job["source"].lower(),
            job["job_id"].lower(),
        )

        if key in unique:
            duplicates += 1
            continue

        unique[key] = job

    return list(unique.values()), duplicates


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: python3 scripts/discover_local.py <jobs.json>"
        )
        sys.exit(1)

    path = ROOT / sys.argv[1]

    if not path.exists():
        print(f"ERROR: file not found: {path}")
        sys.exit(1)

    raw_jobs = load_jobs(path)

    normalized = []

    for index, job in enumerate(raw_jobs, start=1):
        normalized.append(
            normalize_job(job, index)
        )

    unique_jobs, duplicate_count = deduplicate(
        normalized
    )

    print("Local discovery result")
    print("----------------------")
    print(f"Raw jobs      : {len(raw_jobs)}")
    print(f"Unique jobs   : {len(unique_jobs)}")
    print(f"Duplicates    : {duplicate_count}")
    print()

    for job in unique_jobs:
        print(
            f"{job['source']} | "
            f"{job['job_id']} | "
            f"{job['company']} | "
            f"{job['title']} | "
            f"{job['location']}"
        )


if __name__ == "__main__":
    main()
