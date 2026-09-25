#!/usr/bin/env python3

"""
Thin wrapper around the EXISTING scripts/discover_local.normalize_job()
-- never a second normalization implementation. Applies it per-job so
one malformed record (already supposed to have been filtered by
validate_discovered_job.py, but defense in depth) never aborts the
whole batch, and tags each result with a genuine `discovered_at`
timestamp distinct from any source-provided `posted_date` (Phase J:
never collapse the two).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from discover_local import normalize_job


def _now():
    return datetime.now(timezone.utc).isoformat()


def normalize_discovered_jobs(jobs):
    """Returns (normalized: list[dict], errors: list[{"job": dict, "error": str}]).
    Reuses discover_local.normalize_job() unmodified; only adds this
    Skill's own `discovered_at` bookkeeping on top."""
    normalized = []
    errors = []

    for index, job in enumerate(jobs):
        try:
            result = normalize_job(job, index)
        except ValueError as error:
            errors.append({"job": job, "error": str(error)})
            continue

        if not result.get("discovered_at"):
            result["discovered_at"] = _now()

        normalized.append(result)

    return normalized, errors


def dedup_within_batch(jobs):
    """Within-this-discovery-call dedup by (source, job_url) -- see
    search-query-strategy.md. Distinct from, and does not replace,
    cross_source_dedup.py's cross-SOURCE duplicate detection
    downstream. Keeps the first occurrence."""
    seen = set()
    unique = []
    for job in jobs:
        key = (job.get("source"), job.get("job_url") or job.get("application_url"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)
    return unique


if __name__ == "__main__":
    import json

    sample = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else []
    normalized, errors = normalize_discovered_jobs(dedup_within_batch(sample))
    print(json.dumps({"normalized": normalized, "errors": errors}, indent=2))
