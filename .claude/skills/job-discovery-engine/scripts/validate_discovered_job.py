#!/usr/bin/env python3

"""
Phase J quality gate -- see ../references/quality-rules.md. Applied to
every raw job dict from every acquisition mechanism (adapter, ATS
provider, or Web Search discovery) before it is treated as an
actionable job. Pure, offline, no network/DB access. Never raises for
a single malformed record -- returns (valid, reasons) so a caller can
collect rejects without aborting the batch.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import source_registry

_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def _is_usable_url(value):
    return bool(value) and isinstance(value, str) and bool(_URL_RE.match(value.strip()))


def validate_discovered_job(job):
    """Returns (valid: bool, reasons: list[str]). `job` is a raw
    CommonJob-shaped dict (see job-schema.md)."""
    reasons = []

    if not isinstance(job, dict):
        return False, [f"job must be a dict, got {type(job).__name__}"]

    title = job.get("title")
    if not title or not isinstance(title, str) or not title.strip():
        reasons.append("missing or empty 'title'")

    company = job.get("company")
    if not company or not isinstance(company, str) or not company.strip():
        reasons.append("missing or empty 'company'")

    source = job.get("source")
    if not source or not isinstance(source, str):
        reasons.append("missing or empty 'source'")
    else:
        known_sources = set(source_registry.list_sources())
        if source.upper() not in known_sources:
            reasons.append(f"'source' {source!r} is not a known registered source")

    job_url = job.get("job_url")
    application_url = job.get("application_url")
    if not (_is_usable_url(job_url) or _is_usable_url(application_url)):
        reasons.append(
            "no usable job_url or application_url -- a job with neither is "
            "never presented as an actionable job"
        )

    return (len(reasons) == 0), reasons


def validate_batch(jobs):
    """Returns (accepted: list[dict], rejected: list[{"job": dict, "reasons": [...]}])."""
    accepted = []
    rejected = []

    for job in jobs:
        valid, reasons = validate_discovered_job(job)
        if valid:
            accepted.append(job)
        else:
            rejected.append({"job": job, "reasons": reasons})

    return accepted, rejected


if __name__ == "__main__":
    import json

    sample = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else []
    accepted, rejected = validate_batch(sample if isinstance(sample, list) else [sample])
    print(json.dumps({"accepted": accepted, "rejected": rejected}, indent=2))
