#!/usr/bin/env python3

import hashlib
import re
from urllib.parse import urlparse


def normalize(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def job_id_from_url(source, job_url):
    """
    Generate a stable ID from source + normalized job URL.
    Returns None when no usable URL exists.
    """
    if not job_url:
        return None

    url = job_url.strip()

    parsed = urlparse(url)

    if not parsed.scheme or not parsed.netloc:
        return None

    normalized_url = (
        f"{parsed.scheme.lower()}://"
        f"{parsed.netloc.lower()}"
        f"{parsed.path.rstrip('/')}"
    )

    if parsed.query:
        normalized_url += f"?{parsed.query}"

    raw = f"{normalize(source)}|{normalized_url}"

    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    return f"{normalize(source).upper()}-{digest}"


def job_id_from_fields(source, company, title, location):
    """
    Stable fallback when a source does not provide a job ID
    and no usable job URL exists.
    """
    raw = "|".join(
        [
            normalize(source),
            normalize(company),
            normalize(title),
            normalize(location),
        ]
    )

    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    return f"{normalize(source).upper()}-{digest}"


def generate_job_id(job):
    """
    Priority:
      1. Source-provided job_id
      2. Stable URL-based ID
      3. Stable field-based ID
    """
    if job.get("job_id"):
        return str(job["job_id"])

    source = job.get("source", "UNKNOWN")

    from_url = job_id_from_url(
        source,
        job.get("job_url", "")
    )

    if from_url:
        return from_url

    return job_id_from_fields(
        source,
        job.get("company", ""),
        job.get("title", ""),
        job.get("location", ""),
    )


if __name__ == "__main__":
    tests = [
        {
            "source": "LinkedIn",
            "job_url": "https://www.linkedin.com/jobs/view/123456789/",
        },
        {
            "source": "LinkedIn",
            "job_url": "https://www.linkedin.com/jobs/view/123456789/",
        },
        {
            "source": "Naukri",
            "company": "Example Company",
            "title": "Senior SRE",
            "location": "Bengaluru",
        },
        {
            "source": "Naukri",
            "company": "Example Company",
            "title": "Senior SRE",
            "location": "Bengaluru",
        },
    ]

    for i, job in enumerate(tests, 1):
        print(f"TEST-{i}: {generate_job_id(job)}")
