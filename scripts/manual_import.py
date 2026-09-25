#!/usr/bin/env python3

"""
Manual job import (Phase 13 broad-discovery, Part 13).

For sources this project has investigated and found no authorized
automated acquisition path for (LinkedIn, Indeed, Foundit, Instahyre,
Cutshort, Wellfound, Shine -- see data/reports/phase12_public_source_
expansion.md and phase13_broad_job_discovery.md), the only remaining
legitimate acquisition path is the human candidate manually reading a
job they are already viewing in their own browser and typing/pasting
its details into JobOS themselves.

DELIBERATE DESIGN CHOICE -- this module never fetches a URL:

This is a plain field-validation + normalization function. It does NOT
accept a URL and fetch/scrape it server-side. That design choice is
intentional: several of these sites' own robots.txt language ("the use
of robots or OTHER AUTOMATED MEANS... is strictly prohibited") reads as
covering any programmatic fetch of their pages, including a single
one-shot fetch triggered by a human click -- not just bulk crawling.
Rather than adjudicate that line ourselves, this project follows its
own pre-existing precedent for this exact situation (see
scripts/naukri_manual_capture.js's docstring in CLAUDE.md): the human,
already legitimately viewing the page in their own browser, is the one
who reads and transcribes the public information. JobOS then only
normalizes, deduplicates, scores, and stores what the human already
told it -- exactly like scripts/add_job.py's existing interactive flow,
just reached from the GUI instead of the CLI.

Nothing here fabricates a field: any field the human did not supply is
left blank, never guessed (Profile Truth Rule, CLAUDE.md).
"""

KNOWN_JOB_SOURCES = {
    "LINKEDIN",
    "INDEED",
    "FOUNDIT",
    "INSTAHYRE",
    "CUTSHORT",
    "WELLFOUND",
    "SHINE",
    "NAUKRI",
    "HIRIST",
    "IIMJOBS",
    "APNA",
    "OTHER",
}


class ManualImportValidationError(ValueError):
    pass


def _clean(value):
    return str(value or "").strip()


def validate_manual_import(payload):
    """Raises ManualImportValidationError with a human-readable message
    for the first problem found. Returns nothing -- call
    build_manual_import_raw_job() after this passes."""
    job_source = _clean(payload.get("job_source")).upper()
    if not job_source:
        raise ManualImportValidationError("Select which job board this listing is from.")
    if job_source not in KNOWN_JOB_SOURCES:
        raise ManualImportValidationError(
            f"Unknown job source {job_source!r}. Choose one of: {', '.join(sorted(KNOWN_JOB_SOURCES))}."
        )

    title = _clean(payload.get("title"))
    if not title:
        raise ManualImportValidationError("Job title is required.")

    company = _clean(payload.get("company"))
    if not company:
        raise ManualImportValidationError("Company name is required.")

    job_url = _clean(payload.get("job_url"))
    if not job_url:
        raise ManualImportValidationError("The job's public URL is required.")
    if not (job_url.startswith("http://") or job_url.startswith("https://")):
        raise ManualImportValidationError("The job URL must start with http:// or https://.")


def build_manual_import_raw_job(payload):
    """Builds a raw job dict in the standard CommonJob shape (see
    source_adapter.MockJobSourceAdapter.search() for the reference
    shape), ready to pass into discover_local.normalize_job(). Caller
    must call validate_manual_import() first (this function assumes
    the payload is already valid and does not re-validate).

    `source` (the identity/dedup key) is set to the REAL platform name
    (e.g. "LINKEDIN"), not a synthetic "*_MANUAL_IMPORT" label -- this
    is deliberate: job_id.py derives a job's ID from source + its
    normalized URL, so a job manually imported today and later
    discovered again by a real, live-validated adapter for the same
    platform will resolve to the SAME job_id and naturally dedupe,
    rather than existing as two permanently-separate rows. The fact
    that THIS particular row arrived via manual import (as opposed to
    an automated adapter) is preserved separately in discovery_source,
    per Part 11's discovery_source/job_source model.
    """
    job_source = _clean(payload.get("job_source")).upper()

    return {
        "source": job_source,
        "job_source": job_source,
        "discovery_source": "MANUAL_IMPORT",
        "company": _clean(payload.get("company")),
        "title": _clean(payload.get("title")),
        "location": _clean(payload.get("location")),
        "work_model": _clean(payload.get("work_model")),
        "job_url": _clean(payload.get("job_url")),
        "application_url": _clean(payload.get("application_url")) or _clean(payload.get("job_url")),
        "posted_date": _clean(payload.get("posted_date")),
        "jd_text": _clean(payload.get("jd_text")),
        "experience_required": _clean(payload.get("experience_required")),
        # Manual entry has no structured skill extraction -- deliberately
        # empty rather than guessed from jd_text. score_job() already
        # tolerates empty mandatory/preferred skill lists (it falls back
        # to jd_text keyword matching), so this does not degrade scoring
        # quality, it just doesn't add fabricated precision.
        "mandatory_skills": [],
        "preferred_skills": [],
    }
