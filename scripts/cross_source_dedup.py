#!/usr/bin/env python3

"""
Cross-source duplicate CANDIDATE detection.

discover_local.deduplicate() already handles exact intra-source
duplicates via the existing UNIQUE(source, job_id) identity -- that is
untouched and unduplicated here. This module answers a different
question: whether two jobs from DIFFERENT sources plausibly describe the
SAME real-world job posting, using multiple independent structural
signals derived from canonical_job.CanonicalJob.

This is intentionally a CANDIDATE detector, not an auto-merge: it never
deletes, drops, or collapses either source's job row -- both keep their
own full provenance (source, source_job_id, job_url) untouched. A caller
decides what to do with a reported DuplicateCandidate (e.g. surface it
for human review); nothing in this module writes to the database.

Conservative by design, per this component's explicit spec: a
false-positive merge is worse than surfacing two genuinely-distinct jobs
as unmatched. Two jobs are only ever reported as duplicate CANDIDATES
when their normalized_title AND normalized_company both match exactly
(never fuzzy-matched) -- these are treated as necessary, not sufficient,
conditions. A confirmed mismatch on either canonical_location (when both
sides have one) or description similarity (when both sides have text)
overrides an otherwise-matching title+company pair back to "not a
duplicate," rather than being averaged away.
"""

from dataclasses import dataclass, field
from difflib import SequenceMatcher


DESCRIPTION_LOW_SIMILARITY_THRESHOLD = 0.35
DESCRIPTION_HIGH_SIMILARITY_THRESHOLD = 0.55


@dataclass
class DuplicateCandidate:
    job_a: object  # CanonicalJob
    job_b: object  # CanonicalJob
    confidence: str  # "HIGH" | "MEDIUM"
    signals: list = field(default_factory=list)
    reason: str = ""


def _description_similarity(text_a, text_b):
    return SequenceMatcher(None, text_a, text_b).ratio()


def compare_pair(job_a, job_b):
    """
    Compare exactly two CanonicalJob instances. Returns a
    DuplicateCandidate, or None if they are not a duplicate candidate.

    Order of checks (each an early exit to "not a candidate" the moment
    evidence contradicts the hypothesis, rather than a weighted average
    that could let one strong match paper over one strong mismatch):
      1. normalized_title must match exactly and be non-empty on both.
      2. normalized_company must match exactly and be non-empty on both.
      3. If both sides have a canonical_location, it must match.
      4. If both sides have a description, its similarity must clear
         DESCRIPTION_LOW_SIMILARITY_THRESHOLD.
    """
    title_a = job_a.normalized_title
    title_b = job_b.normalized_title
    if not title_a or not title_b or title_a != title_b:
        return None

    company_a = job_a.normalized_company
    company_b = job_b.normalized_company
    if not company_a or not company_b or company_a != company_b:
        return None

    signals = ["normalized_title_match", "normalized_company_match"]

    if job_a.canonical_location and job_b.canonical_location:
        if job_a.canonical_location != job_b.canonical_location:
            return None
        signals.append("canonical_location_match")

    confidence = "MEDIUM"

    if job_a.description and job_b.description:
        ratio = _description_similarity(job_a.description, job_b.description)
        if ratio < DESCRIPTION_LOW_SIMILARITY_THRESHOLD:
            return None
        signals.append(f"description_similarity={ratio:.2f}")
        if ratio >= DESCRIPTION_HIGH_SIMILARITY_THRESHOLD:
            confidence = "HIGH"

    reason_parts = ["normalized title and company both matched"]
    if "canonical_location_match" in signals:
        reason_parts.append("locations agree")
    if confidence == "HIGH":
        reason_parts.append("descriptions are highly similar")

    return DuplicateCandidate(
        job_a=job_a,
        job_b=job_b,
        confidence=confidence,
        signals=signals,
        reason="; ".join(reason_parts),
    )


def find_cross_source_duplicate_candidates(canonical_jobs):
    """
    Compare every pair of CanonicalJob objects from DIFFERENT sources
    (same-source pairs are out of scope -- see this module's docstring)
    and return the list of DuplicateCandidate results, in first-seen
    pair order. O(n^2) -- acceptable at this project's current and
    near-term data volumes; not intended for a large-scale corpus.
    """
    candidates = []

    for i in range(len(canonical_jobs)):
        for j in range(i + 1, len(canonical_jobs)):
            job_a = canonical_jobs[i]
            job_b = canonical_jobs[j]

            if job_a.source == job_b.source:
                continue

            result = compare_pair(job_a, job_b)
            if result is not None:
                candidates.append(result)

    return candidates
