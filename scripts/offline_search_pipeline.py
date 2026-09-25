#!/usr/bin/env python3

"""
End-to-end OFFLINE search simulation: fixture-derived raw job dicts all
the way through to a deterministic ranked report, using nothing but
already-existing, already-tested components. This module writes no
files, opens no database connection, invokes no source adapter, and
makes no network call -- it is pure, synchronous, in-memory computation
from a list of raw job dicts (and a candidate profile dict) to a
RankedSearchResult.

Pipeline (every stage reuses an existing module unchanged):

    raw job dicts
        -> discover_local.normalize_job()          (per job; a malformed
           job is skipped, not fatal -- mirrors search_worker.py's own
           per-job resilience)
        -> discover_local.deduplicate()             (existing exact
           intra-batch (source, job_id) dedup, unchanged)
        -> canonical_job.derive_canonical_job()      (per unique job)
        -> cross_source_dedup.find_cross_source_duplicate_candidates()
           (once, over the whole batch)
        -> job_ranking.build_ranking_record()        (per unique job,
           given the duplicate candidates that involve it -- this is
           itself eligibility-before-scoring via job_eligibility.py and
           score_job.py, unchanged)
        -> deterministic sort (see RANKING POLICY below)

RANKING POLICY (exact, documented order -- never a weighted formula):
    1. Eligible jobs always rank above ineligible jobs, unconditionally.
       Freshness or any other secondary signal can never move an
       ineligible job above an eligible one.
    2. Among jobs with the same eligibility, higher match score first.
       score_job.py's 100-point score is the only ranking-relevant
       score; nothing here recomputes or perturbs it.
    3. Among jobs with the same eligibility AND the same score,
       freshness breaks the tie (HOT before FRESH before AGING before
       OLD before STALE before UNKNOWN). Freshness is read-only here --
       it never feeds back into score or eligibility.
    4. Final deterministic tie-breaker: (source, job_id) ascending, so
       two jobs identical on every ranking-relevant signal always sort
       in the same order on every run.

Duplicate candidates are surfaced, never merged or dropped: every
source listing that survives normalization+intra-batch-dedup keeps its
own RankingRecord. cross_source_dedup's DuplicateCandidate objects are
reported alongside the ranking, and each involved RankingRecord also
carries them (via build_ranking_record's own duplicate_candidates
field) -- but nothing is deleted, merged, or assigned a canonical_job_id.
"""

from dataclasses import dataclass, field

from discover_local import normalize_job, deduplicate
from canonical_job import derive_canonical_job
from cross_source_dedup import find_cross_source_duplicate_candidates
from job_ranking import build_ranking_record


_FRESHNESS_RANK = {
    "HOT": 0,
    "FRESH": 1,
    "AGING": 2,
    "OLD": 3,
    "STALE": 4,
    "UNKNOWN": 5,
}


@dataclass
class OfflinePipelineResult:
    candidate_name: str | None
    evaluated_count: int
    malformed_count: int
    duplicate_count: int  # intra-batch exact (source, job_id) duplicates removed
    eligible_count: int
    ineligible_count: int
    ranked_jobs: list = field(default_factory=list)  # list[RankingRecord], final sorted order
    duplicate_relationships: list = field(default_factory=list)  # list[dict]
    freshness_summary: dict = field(default_factory=dict)
    source_summary: dict = field(default_factory=dict)


def _sort_key(record):
    score_for_sort = record.score if record.score is not None else -1
    return (
        0 if record.eligible else 1,
        -score_for_sort,
        _FRESHNESS_RANK.get(record.freshness, 5),
        record.source,
        record.job_id,
    )


def _serialize_duplicate_relationship(candidate):
    return {
        "job_a": {
            "source": candidate.job_a.source,
            "source_job_id": candidate.job_a.source_job_id,
            "title": candidate.job_a.title,
            "company": candidate.job_a.company,
        },
        "job_b": {
            "source": candidate.job_b.source,
            "source_job_id": candidate.job_b.source_job_id,
            "title": candidate.job_b.title,
            "company": candidate.job_b.company,
        },
        "confidence": candidate.confidence,
        "signals": list(candidate.signals),
        "reason": candidate.reason,
    }


def run_offline_pipeline(raw_jobs, candidate_profile, candidate_name=None):
    """
    raw_jobs: list of raw job dicts (the RawJob-ish shape every source
    adapter and the existing fixtures already use).

    candidate_profile: the flat legacy-shaped dict score_job.py,
    experience_eligibility.py, and location_taxonomy.py already consume.

    candidate_name: optional display name for the report only (never
    invented if omitted -- falls back to candidate_profile["candidate"]
    ["name"] when present, else None).

    Returns an OfflinePipelineResult. Pure computation: no I/O, no
    database access, no network access, no adapter invocation.
    """
    if candidate_name is None:
        candidate_name = (candidate_profile.get("candidate") or {}).get("name")

    normalized_jobs = []
    malformed_count = 0

    for index, raw_job in enumerate(raw_jobs, start=1):
        try:
            normalized_jobs.append(normalize_job(dict(raw_job), index))
        except Exception:
            malformed_count += 1

    unique_jobs, duplicate_count = deduplicate(normalized_jobs)

    canonical_jobs = [derive_canonical_job(job) for job in unique_jobs]
    duplicate_candidates = find_cross_source_duplicate_candidates(canonical_jobs)

    duplicates_by_identity = {}
    for candidate in duplicate_candidates:
        for job in (candidate.job_a, candidate.job_b):
            key = (job.source, job.source_job_id)
            duplicates_by_identity.setdefault(key, []).append(candidate)

    ranked_jobs = []
    eligible_count = 0
    ineligible_count = 0
    freshness_summary = {}
    source_summary = {}

    for job in unique_jobs:
        key = (job["source"], job["job_id"])
        record = build_ranking_record(
            job, candidate_profile, duplicate_candidates=duplicates_by_identity.get(key, [])
        )
        ranked_jobs.append(record)

        if record.eligible:
            eligible_count += 1
        else:
            ineligible_count += 1

        freshness_summary[record.freshness] = freshness_summary.get(record.freshness, 0) + 1
        source_summary[record.source] = source_summary.get(record.source, 0) + 1

    ranked_jobs.sort(key=_sort_key)

    duplicate_relationships = [
        _serialize_duplicate_relationship(candidate) for candidate in duplicate_candidates
    ]

    return OfflinePipelineResult(
        candidate_name=candidate_name,
        evaluated_count=len(unique_jobs),
        malformed_count=malformed_count,
        duplicate_count=duplicate_count,
        eligible_count=eligible_count,
        ineligible_count=ineligible_count,
        ranked_jobs=ranked_jobs,
        duplicate_relationships=duplicate_relationships,
        freshness_summary=freshness_summary,
        source_summary=source_summary,
    )
