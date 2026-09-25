#!/usr/bin/env python3

"""
Canonical job normalization: a pure, additive, offline derived view built
on top of discover_local.normalize_job()'s existing output.

This module does NOT replace, wrap, or duplicate normalize_job() -- a
CanonicalJob is always derived FROM an already-normalized job dict
(the same dict shape search_worker.py and tracker.py already consume),
never instead of one. Nothing here writes to the database, changes the
existing jobs-table contract, or is wired into search_worker.py's
persistence path -- it is a read-only enrichment layer for cross-source
comparison (see cross_source_dedup.py), built for exactly the fields the
existing schema/pipeline does not yet standardize on:

  normalized_title, normalized_company, canonical_location,
  canonical_work_model, experience_min_years, experience_max_years,
  skills (combined), salary_*, employment_type, freshness, updated_date,
  source_metadata.

Existing candidate-agnostic parsing is reused wherever it already exists:
  - location_taxonomy.parse_job_location_and_work_model() for locations
    and work model (the ONLY place city/region/country alias knowledge
    lives -- not duplicated here).
  - experience_eligibility.parse_experience_text() for numeric
    experience-year parsing from the human-facing experience_required
    string (the ONLY place that regex knowledge lives -- not duplicated
    here).

Unknown/missing source fields are never invented: a field this module
cannot derive from the normalized job dict is always None (or an empty
list/string, matching the type), never guessed at or defaulted to a
plausible-looking value.
"""

import re
from dataclasses import dataclass, field

from location_taxonomy import (
    LocationKind,
    WorkModel,
    parse_job_location_and_work_model,
)
from experience_eligibility import parse_experience_text


# ---------------------------------------------------------------------
# Title normalization
# ---------------------------------------------------------------------

# Small, deterministic, conservative expansion map for cross-source TITLE
# comparison only -- never used to change the job's own displayed/stored
# `title` field. Deliberately excludes any seniority word ("Senior",
# "Lead", "Junior", "Principal", ...): collapsing those together is
# exactly the over-normalization the spec this module implements warns
# against (a Senior and a Lead posting for the same role are NOT the
# same normalized title). Only genuinely equivalent short forms/
# abbreviations for THIS candidate's own established role vocabulary
# (config/profile.json) are included.
_TITLE_TOKEN_MAP = {
    "sr": "senior",
    "jr": "junior",
    "sre": "site reliability engineer",
    "devops": "devops",
}

_ROMAN_NUMERAL_SUFFIX = re.compile(r"\b(i|ii|iii|iv|v)\b$", re.IGNORECASE)
_ROMAN_TO_ARABIC = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}


def normalize_title(raw_title):
    """
    Deterministic, conservative title normalization for cross-source
    COMPARISON only. Handles case folding, "/"-separated combined
    titles, punctuation/whitespace collapsing, a small abbreviation
    expansion map, adjacent-duplicate-word collapsing (an expansion can
    introduce a repeated word, e.g. "... SRE Engineer" -> "... site
    reliability engineer engineer" -> collapsed to one trailing
    "engineer"), and a trailing Roman-numeral seniority/level suffix
    converted to an Arabic digit ("Engineer III" -> "engineer 3").

    Never strips a seniority word. Never merges genuinely different
    role families.
    """
    text = str(raw_title or "").strip().lower()
    if not text:
        return ""

    text = text.replace("/", " ")
    text = re.sub(r"[.,]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    words = []
    for token in text.split(" "):
        stripped = token.rstrip(".")
        replacement = _TITLE_TOKEN_MAP.get(stripped, stripped)
        words.extend(replacement.split(" "))

    deduped = []
    for word in words:
        if deduped and deduped[-1] == word:
            continue
        deduped.append(word)
    text = " ".join(deduped)

    match = _ROMAN_NUMERAL_SUFFIX.search(text)
    if match:
        roman = match.group(1).lower()
        text = _ROMAN_NUMERAL_SUFFIX.sub(_ROMAN_TO_ARABIC[roman], text)

    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------
# Company normalization
# ---------------------------------------------------------------------

# Standard legal-entity suffixes only -- deliberately NOT generic
# business words ("technologies", "solutions", "systems", "group",
# "india", ...), since stripping those could merge genuinely different
# companies that happen to share a common word. Ordered longest-first so
# "pvt ltd" / "private limited" are tried before their shorter "ltd" /
# "limited" tails.
_LEGAL_SUFFIXES = sorted(
    [
        "private limited",
        "pvt limited",
        "pvt ltd",
        "limited",
        "ltd",
        "llp",
        "llc",
        "corporation",
        "corp",
        "inc",
        "co",
    ],
    key=len,
    reverse=True,
)


def normalize_company(raw_company):
    """
    Deterministic, conservative company-name normalization for
    cross-source COMPARISON only: case folding, punctuation/whitespace
    collapsing, and stripping at most one trailing standard legal-entity
    suffix (e.g. "Example Technologies Pvt Ltd" -> "example
    technologies"). Never strips ordinary business words.
    """
    text = str(raw_company or "").strip().lower()
    if not text:
        return ""

    text = re.sub(r"[.,]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    for suffix in _LEGAL_SUFFIXES:
        pattern = re.compile(rf"\s+{re.escape(suffix)}$")
        stripped = pattern.sub("", text)
        if stripped != text:
            text = stripped
            break

    return text.strip()


# ---------------------------------------------------------------------
# Canonical location / work model
# ---------------------------------------------------------------------

_REMOTE_KINDS = (
    LocationKind.REMOTE_UNSPECIFIED,
    LocationKind.REMOTE_COUNTRY,
    LocationKind.REMOTE_GLOBAL,
)


def _derive_canonical_location(locations):
    """
    Collapse a parsed location list (location_taxonomy.NormalizedLocation
    objects, most specific listed first) into one summarizing string for
    comparison, preferring the most specific recognized signal: a named
    CITY, then any REMOTE_* kind (collapsed to the single label "Remote"
    -- the scope distinction, e.g. "remote to India" vs. globally remote,
    still survives unabridged in `locations`, just not in this one
    summary field), then a REGION, then a COUNTRY/PAN_COUNTRY. Returns
    None when nothing in the list was recognized at all (LocationKind.
    UNKNOWN only) -- never guesses.
    """
    for loc in locations:
        if loc.kind == LocationKind.CITY:
            return loc.city

    if any(loc.kind in _REMOTE_KINDS for loc in locations):
        return "Remote"

    for loc in locations:
        if loc.kind == LocationKind.REGION:
            return loc.region

    for loc in locations:
        if loc.kind == LocationKind.PAN_COUNTRY:
            return f"Pan-{loc.country}" if loc.country else "Pan-country"

    for loc in locations:
        if loc.kind == LocationKind.COUNTRY:
            return loc.country

    return None


# ---------------------------------------------------------------------
# Canonical job derivation
# ---------------------------------------------------------------------

@dataclass
class CanonicalJob:
    source: str
    source_job_id: str
    title: str
    normalized_title: str
    company: str
    normalized_company: str
    job_url: str
    locations: list
    canonical_location: str | None
    canonical_work_model: str
    employment_type: str | None
    experience_min_years: float | None
    experience_max_years: float | None
    skills: list
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    freshness: str | None
    updated_date: str | None
    description: str
    source_metadata: dict = field(default_factory=dict)


# Fields already consumed explicitly below; anything else present on the
# normalized job dict is preserved, unmodified, under source_metadata --
# so no source-provided information is ever silently dropped, even if
# this module has no dedicated canonical field for it yet.
_CONSUMED_FIELDS = {
    "source",
    "job_id",
    "company",
    "title",
    "location",
    "work_model",
    "job_url",
    "jd_text",
    "experience_required",
    "mandatory_skills",
    "preferred_skills",
    "experience_min_months",
    "posted_date",
}


def derive_canonical_job(normalized_job: dict) -> CanonicalJob:
    """
    Derive a CanonicalJob from a job dict already produced by
    discover_local.normalize_job(). Pure function -- no I/O, no
    database access, deterministic for a given input.

    Fields the current source contract (source_adapter.RawJob) has no
    equivalent for at all (employment_type, salary_min/max/currency/
    period, updated_date) are always None: no source adapter in this
    project provides them yet, and inventing a value would violate the
    project's Profile/Data Truth Rule.
    """
    title = str(normalized_job.get("title", "") or "")
    company = str(normalized_job.get("company", "") or "")

    locations, work_model = parse_job_location_and_work_model(
        normalized_job.get("location", ""), normalized_job.get("work_model", "")
    )

    exp_min, exp_max = parse_experience_text(normalized_job.get("experience_required", ""))

    mandatory_skills = normalized_job.get("mandatory_skills") or []
    preferred_skills = normalized_job.get("preferred_skills") or []
    skills = []
    for skill in list(mandatory_skills) + list(preferred_skills):
        if skill not in skills:
            skills.append(skill)

    source_metadata = {
        key: value
        for key, value in normalized_job.items()
        if key not in _CONSUMED_FIELDS
    }
    if normalized_job.get("experience_min_months") is not None:
        source_metadata["experience_min_months"] = normalized_job["experience_min_months"]

    return CanonicalJob(
        source=str(normalized_job.get("source", "") or ""),
        source_job_id=str(normalized_job.get("job_id", "") or ""),
        title=title,
        normalized_title=normalize_title(title),
        company=company,
        normalized_company=normalize_company(company),
        job_url=str(normalized_job.get("job_url", "") or ""),
        locations=locations,
        canonical_location=_derive_canonical_location(locations),
        canonical_work_model=work_model.value,
        employment_type=None,
        experience_min_years=exp_min,
        experience_max_years=exp_max,
        skills=skills,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        salary_period=None,
        freshness=(str(normalized_job["posted_date"]).strip() or None)
        if normalized_job.get("posted_date")
        else None,
        updated_date=None,
        description=str(normalized_job.get("jd_text", "") or ""),
        source_metadata=source_metadata,
    )
