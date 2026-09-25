#!/usr/bin/env python3

"""
Final ranking record: a pure, offline orchestration of the existing
pipeline pieces into one structured, report/CLI/API-ready record.

    normalized job
        -> job_eligibility.assess_job_eligibility()   (unchanged, reused)
        -> score_job.score_job()                       (unchanged, reused
           -- called ONLY when eligible, exactly mirroring
           search_worker.py's own eligibility-before-scoring behavior;
           see this module's docstring note below)
        -> score_explanation.build_score_explanation() (new, additive)
        -> freshness.classify_freshness()               (new, additive)
        -> canonical_job.derive_canonical_job()         (existing, reused)
        -> RankingRecord

This module performs NO I/O and writes nothing to any database --
nothing here is wired into search_worker.py's persistence path. A
caller with a database connection decides separately whether/how to
store or display a RankingRecord.

Eligibility-before-scoring, preserved: exactly like search_worker.py,
score_job() is never called for a job assess_job_eligibility() has
already deemed ineligible. An ineligible RankingRecord therefore always
has score/priority/status/score_explanation = None -- this is the
correct, existing behavior being mirrored, not a bug.
"""

from dataclasses import dataclass, field

from canonical_job import derive_canonical_job
from freshness import classify_freshness
from job_eligibility import assess_job_eligibility
from score_explanation import build_score_explanation
from score_job import score_job


@dataclass
class RankingRecord:
    job_id: str
    source: str
    title: str
    company: str
    job_url: str
    eligible: bool
    eligibility: str
    eligibility_reasons: list
    score: int | None
    priority: str | None
    status: str | None
    score_explanation: dict | None
    freshness: str
    freshness_age_days: int | None
    normalized_title: str
    normalized_company: str
    canonical_location: str | None
    duplicate_candidates: list = field(default_factory=list)
    # Informational only (experience_eligibility.detect_requirement_
    # type) -- never affects `eligible`/`eligibility` above, which are
    # already decided before this is populated. Defaulted (rather than
    # required) so existing direct RankingRecord(...) construction in
    # this project's tests is unaffected.
    requirement_type: str = "UNKNOWN"


def _serialize_duplicate_candidate(candidate, this_job_source, this_job_source_job_id):
    """
    Reduce a cross_source_dedup.DuplicateCandidate that involves THIS
    job to a small, JSON-serializable summary describing the OTHER side
    of the pair -- callers pass in only candidates already known to
    involve this job (see build_ranking_record's docstring); this
    module does not run cross-source dedup itself.
    """
    if (candidate.job_a.source, candidate.job_a.source_job_id) == (
        this_job_source,
        this_job_source_job_id,
    ):
        other = candidate.job_b
    else:
        other = candidate.job_a

    return {
        "other_source": other.source,
        "other_source_job_id": other.source_job_id,
        "other_job_url": other.job_url,
        "confidence": candidate.confidence,
        "signals": list(candidate.signals),
        "reason": candidate.reason,
    }


def build_ranking_record(normalized_job, candidate_profile, duplicate_candidates=None):
    """
    normalized_job: a dict already produced by discover_local.normalize_job()
    (or a Naukri RawJob dict with the same field contract).

    candidate_profile: the flat legacy-shaped dict score_job.py,
    experience_eligibility.py, and location_taxonomy.py already consume
    (candidate_profile.to_legacy_matching_profile()'s output, or the
    same shape config/profile.json has always had).

    duplicate_candidates: optional list of cross_source_dedup.
    DuplicateCandidate objects that the CALLER has already determined
    involve this specific job (e.g. by running
    cross_source_dedup.find_cross_source_duplicate_candidates() over a
    wider batch and filtering for this job's identity). Cross-source
    dedup is intentionally NOT run inside this function -- it requires
    comparing against a pool of other jobs this function has no access
    to, and is not wired into any live pipeline by this component
    (per this task's explicit scope).
    """
    eligibility_result = assess_job_eligibility(normalized_job, candidate_profile)

    scoring = None
    explanation = None
    if eligibility_result.eligible:
        scoring = score_job(
            normalized_job,
            candidate_profile,
            experience_assessment=eligibility_result.experience_assessment,
        )
        explanation = build_score_explanation(eligibility_result, scoring)

    if explanation is not None:
        eligibility_reasons = list(explanation.eligibility_reasons)
    else:
        eligibility_reasons = [eligibility_result.reason]

    freshness_assessment = classify_freshness(normalized_job.get("posted_date", ""))
    canonical = derive_canonical_job(normalized_job)

    this_source = str(normalized_job.get("source", "") or "")
    this_job_id = str(normalized_job.get("job_id", "") or "")

    serialized_duplicates = [
        _serialize_duplicate_candidate(candidate, this_source, this_job_id)
        for candidate in (duplicate_candidates or [])
    ]

    return RankingRecord(
        job_id=this_job_id,
        source=this_source,
        title=str(normalized_job.get("title", "") or ""),
        company=str(normalized_job.get("company", "") or ""),
        job_url=str(normalized_job.get("job_url", "") or ""),
        eligible=eligibility_result.eligible,
        eligibility=eligibility_result.reason_code,
        eligibility_reasons=eligibility_reasons,
        score=scoring["score"] if scoring is not None else None,
        priority=scoring["priority"] if scoring is not None else None,
        status=scoring["status"] if scoring is not None else None,
        score_explanation=(
            {
                "score": explanation.score,
                "priority": explanation.priority,
                "status": explanation.status,
                "eligible": explanation.eligible,
                "eligibility": explanation.eligibility,
                "components": explanation.components,
                "strong_matches": explanation.strong_matches,
                "gaps": explanation.gaps,
                "eligibility_reasons": explanation.eligibility_reasons,
            }
            if explanation is not None
            else None
        ),
        freshness=freshness_assessment.category.value,
        freshness_age_days=freshness_assessment.age_days,
        # Informational only (see experience_eligibility.detect_requirement_
        # type's docstring) -- never affects eligibility_result.eligible
        # above, which was already decided before this line runs.
        requirement_type=eligibility_result.experience_assessment.requirement_type,
        normalized_title=canonical.normalized_title,
        normalized_company=canonical.normalized_company,
        canonical_location=canonical.canonical_location,
        duplicate_candidates=serialized_duplicates,
    )
