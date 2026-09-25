#!/usr/bin/env python3

"""
Search-run submission: converts a candidate's CONFIRMED CandidateProfile
into a deterministic query plan and persists it as one QUEUED
search_runs row plus one corresponding QUEUED search_queue row.

This module executes NO search: it imports source_registry only for
list_sources() (a static name list -- see search_profile.py), never
calls get_adapter()/discover_from_sources()/any adapter's search() or
health_check(), makes no network or browser call of any kind, and ends
at "QUEUED" exactly as this component is scoped to.

Pipeline:
    candidates (DB) + candidate_search_profile (DB, profile_json)
        -> candidate_profile.normalize_candidate_profile()
        -> search_profile.build_search_profile()        [requires CONFIRMED]
        -> query_planner.build_queries_from_search_profile()
        -> INSERT search_runs (status=QUEUED) + search_queue (status=QUEUED)
           in one transaction (commit both or roll back both)

No second copy of the candidate's profile is created: the SAME
candidate_search_profile.profile_json row the rest of this project's DB
schema already defines is read here, via candidate_profile.py's own
(de)serialization -- never a parallel blob format.
"""

import hashlib
import json
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "applications" / "jobos.db"

sys.path.insert(0, str(ROOT / "scripts"))

from candidate_profile import ProfileStatus, normalize_candidate_profile
from query_planner import (
    DEFAULT_MAX_QUERIES_PER_SUBMISSION,
    build_queries_from_search_profile,
)
from search_profile import SearchProfileError, build_search_profile


class SearchSubmissionError(ValueError):
    """
    Raised for any reason a search submission cannot proceed: unknown
    candidate, inactive candidate, no active search-profile row, a
    profile_json that does not match the canonical candidate-profile
    schema, a non-CONFIRMED profile, or an invalid/empty search profile.
    """


@dataclass
class SearchSubmissionResult:
    submitted: bool
    search_run_id: str
    candidate_id: str
    query_count: int
    sources: list
    target_roles: list
    target_locations: list
    minimum_match_score: int
    duplicate_of: str = None
    query_plan: list = field(default_factory=list)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(candidate_id, profile_version, query_plan, minimum_match_score, maximum_results):
    """
    Deterministic fingerprint of exactly what was requested -- used
    only for the in-process duplicate-QUEUED-submission check below,
    never as a distributed lock. Same candidate + same profile version
    + same query plan + same score/result thresholds => same
    fingerprint, every time.
    """
    payload = json.dumps(
        {
            "candidate_id": candidate_id,
            "profile_version": profile_version,
            "queries": query_plan,
            "minimum_match_score": minimum_match_score,
            "maximum_results": maximum_results,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_candidate_confirmed_profile(conn, candidate_id, profile_version=None):
    """
    Load candidate_id's candidate_search_profile row and deserialize it
    into a CandidateProfile via candidate_profile.
    normalize_candidate_profile().

    profile_version=None (the default): loads whichever version is
    currently ACTIVE -- unchanged pre-existing behavior.

    profile_version=<int>: loads that EXACT historical version instead
    (see migrate_v8_resume_profile_traceability.py /
    saved_searches.profile_version) -- this is what lets a saved search
    pinned to an older resume/profile keep scoring against THAT
    version even after the candidate uploads and confirms a newer one.
    Raises SearchSubmissionError if that version doesn't exist for this
    candidate, or was never confirmed (a pin can only ever point at a
    version that was genuinely confirmed at some point, exactly like
    the active-profile path already requires CONFIRMED below).

    Raises SearchSubmissionError for: unknown candidate, a candidate
    whose status is not ACTIVE, no matching profile row at all, or a
    profile_json that normalize_candidate_profile() itself rejects
    (e.g. a legacy-shaped blob missing "identity"). Never falls back to
    any default/global profile.
    """
    candidate_row = conn.execute(
        "SELECT candidate_id, status FROM candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()

    if candidate_row is None:
        raise SearchSubmissionError(f"Unknown candidate: {candidate_id!r}")

    if candidate_row[1] != "ACTIVE":
        raise SearchSubmissionError(
            f"Candidate {candidate_id!r} is not ACTIVE (status={candidate_row[1]!r})"
        )

    if profile_version is None:
        profile_row = conn.execute(
            """
            SELECT profile_json FROM candidate_search_profile
            WHERE candidate_id = ? AND is_active = 1
            ORDER BY version DESC
            LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
    else:
        profile_row = conn.execute(
            """
            SELECT profile_json FROM candidate_search_profile
            WHERE candidate_id = ? AND version = ? AND confirmed_by_user = 1
            """,
            (candidate_id, profile_version),
        ).fetchone()
        if profile_row is None:
            raise SearchSubmissionError(
                f"Candidate {candidate_id!r} has no CONFIRMED profile version {profile_version!r} "
                "to pin this search to"
            )

    if profile_row is None:
        raise SearchSubmissionError(
            f"No active candidate_search_profile row found for candidate {candidate_id!r}"
        )

    try:
        raw = json.loads(profile_row[0])
    except json.JSONDecodeError as error:
        raise SearchSubmissionError(
            f"candidate_search_profile.profile_json for {candidate_id!r} "
            f"is not valid JSON: {error}"
        ) from error

    try:
        profile = normalize_candidate_profile(raw)
    except ValueError as error:
        raise SearchSubmissionError(
            f"candidate_search_profile.profile_json for {candidate_id!r} does "
            f"not match the canonical candidate-profile schema "
            f"(candidate_profile.normalize_candidate_profile() rejected it): {error}"
        ) from error

    return profile


def build_search_plan(
    conn,
    candidate_id,
    sources=None,
    minimum_match_score=None,
    maximum_results=None,
    minimum_experience_years=None,
    maximum_experience_years=None,
    max_job_age_days=None,
    max_queries=DEFAULT_MAX_QUERIES_PER_SUBMISSION,
    target_roles_override=None,
    target_locations_override=None,
    work_models_override=None,
    query_plan_override=None,
    profile_version_override=None,
):
    """
    Read-only steps of submission: load candidate, load profile, verify
    ACTIVE, verify CONFIRMED, build the search profile, build the
    deterministic query plan. Touches search_runs/search_queue not at
    all. Raises SearchSubmissionError on any failure.

    profile_version_override: passed straight through to
    load_candidate_confirmed_profile() -- see its own docstring. None
    (the default) preserves the original "always use the current
    active profile" behavior exactly.

    target_roles_override / target_locations_override /
    work_models_override pass straight through to
    search_profile.build_search_profile() -- see its docstring. None
    (the default) means unchanged pre-Phase-9 behavior.

    query_plan_override (Phase 14.6): when given, this exact list of
    query-plan dicts (same shape as
    query_planner.build_queries_from_search_profile()'s output --
    {"sequence","source","role","location","max_job_age_days"}) is used
    INSTEAD of calling build_queries_from_search_profile(). Every other
    step (profile load/CONFIRMED check, search_profile construction for
    metadata/fingerprinting) is unchanged -- this exists so an external
    planner (scripts/daily_search_planner.py) can submit a
    deduplicated/consolidated/budget-capped subset of the full matrix
    through this exact same submission path, never a second one. None
    (the default) preserves the original behavior exactly.

    Returns (search_profile, query_plan).
    """
    profile = load_candidate_confirmed_profile(conn, candidate_id, profile_version=profile_version_override)

    if profile.metadata.profile_status != ProfileStatus.CONFIRMED:
        raise SearchSubmissionError(
            f"Candidate {candidate_id!r}'s profile is "
            f"{profile.metadata.profile_status.value}, not CONFIRMED -- "
            f"a DRAFT profile cannot be submitted for search"
        )

    try:
        search_profile = build_search_profile(
            profile,
            active=True,
            sources=sources,
            minimum_match_score=minimum_match_score,
            maximum_results=maximum_results,
            minimum_experience_years=minimum_experience_years,
            maximum_experience_years=maximum_experience_years,
            max_job_age_days=max_job_age_days,
            target_roles_override=target_roles_override,
            target_locations_override=target_locations_override,
            work_models_override=work_models_override,
        )
    except SearchProfileError as error:
        raise SearchSubmissionError(str(error)) from error

    if query_plan_override is not None:
        query_plan = query_plan_override
    else:
        query_plan = build_queries_from_search_profile(search_profile, max_queries=max_queries)

    if not query_plan:
        raise SearchSubmissionError(
            f"Search profile for {candidate_id!r} produced an empty query plan"
        )

    return search_profile, query_plan


def submit_search(
    db_path,
    candidate_id,
    sources=None,
    minimum_match_score=None,
    maximum_results=None,
    minimum_experience_years=None,
    maximum_experience_years=None,
    max_job_age_days=None,
    max_queries=DEFAULT_MAX_QUERIES_PER_SUBMISSION,
    dry_run=False,
    target_roles_override=None,
    target_locations_override=None,
    work_models_override=None,
    query_plan_override=None,
    profile_version_override=None,
):
    """
    Full submission. Builds the plan (read-only), then -- unless
    dry_run=True -- atomically inserts one QUEUED search_runs row and
    one corresponding QUEUED search_queue row. Executes no search.

    query_plan_override: see build_search_plan()'s docstring -- passed
    straight through.

    Duplicate-submission policy: if an existing search_runs row for
    this candidate is still status='QUEUED' with an identical
    fingerprint (same profile_version, same query plan, same
    minimum_match_score/maximum_results), no new row is created --
    the existing search_run_id is returned instead
    (result.duplicate_of is set, result.submitted is False). A
    COMPLETED or FAILED run with the same fingerprint does NOT block a
    fresh submission -- only an unprocessed QUEUED one does. This uses
    a simple application-level check within one connection, not a
    distributed lock.

    dry_run=True performs every read-only step (including the
    duplicate check) but never opens a write transaction -- this is
    what the CLI's default (unconfirmed) preview mode uses.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        search_profile, query_plan = build_search_plan(
            conn,
            candidate_id,
            sources=sources,
            minimum_match_score=minimum_match_score,
            maximum_results=maximum_results,
            minimum_experience_years=minimum_experience_years,
            maximum_experience_years=maximum_experience_years,
            max_job_age_days=max_job_age_days,
            max_queries=max_queries,
            target_roles_override=target_roles_override,
            target_locations_override=target_locations_override,
            work_models_override=work_models_override,
            query_plan_override=query_plan_override,
            profile_version_override=profile_version_override,
        )

        fingerprint = _fingerprint(
            candidate_id,
            search_profile.profile_version,
            query_plan,
            search_profile.minimum_match_score,
            search_profile.maximum_results,
        )

        existing_queued = conn.execute(
            "SELECT search_run_id, query FROM search_runs "
            "WHERE candidate_id = ? AND status = 'QUEUED'",
            (candidate_id,),
        ).fetchall()

        for existing_run_id, existing_query_text in existing_queued:
            try:
                existing_snapshot = json.loads(existing_query_text or "{}")
            except json.JSONDecodeError:
                continue

            if existing_snapshot.get("fingerprint") == fingerprint:
                return SearchSubmissionResult(
                    submitted=False,
                    search_run_id=existing_run_id,
                    candidate_id=candidate_id,
                    query_count=len(query_plan),
                    sources=search_profile.sources,
                    target_roles=search_profile.target_roles,
                    target_locations=search_profile.target_locations,
                    minimum_match_score=search_profile.minimum_match_score,
                    duplicate_of=existing_run_id,
                    query_plan=query_plan,
                )

        search_run_id = str(uuid.uuid4())
        now = _now()

        snapshot = {
            "fingerprint": fingerprint,
            "profile_version": search_profile.profile_version,
            "queries": query_plan,
            "minimum_match_score": search_profile.minimum_match_score,
            "maximum_results": search_profile.maximum_results,
            "submitted_at": now,
        }

        if dry_run:
            return SearchSubmissionResult(
                submitted=False,
                search_run_id=search_run_id,
                candidate_id=candidate_id,
                query_count=len(query_plan),
                sources=search_profile.sources,
                target_roles=search_profile.target_roles,
                target_locations=search_profile.target_locations,
                minimum_match_score=search_profile.minimum_match_score,
                query_plan=query_plan,
            )

        conn.execute("BEGIN")

        conn.execute(
            """
            INSERT INTO search_runs (
                search_run_id, candidate_id, status, source, role, location,
                query, queries_total, queries_completed, created_at, updated_at
            )
            VALUES (?, ?, 'QUEUED', ?, NULL, NULL, ?, ?, 0, ?, ?)
            """,
            (
                search_run_id,
                candidate_id,
                ",".join(search_profile.sources),
                json.dumps(snapshot),
                len(query_plan),
                now,
                now,
            ),
        )

        conn.execute(
            """
            INSERT INTO search_queue (search_run_id, candidate_id, status, created_at)
            VALUES (?, ?, 'QUEUED', ?)
            """,
            (search_run_id, candidate_id, now),
        )

        conn.commit()

        return SearchSubmissionResult(
            submitted=True,
            search_run_id=search_run_id,
            candidate_id=candidate_id,
            query_count=len(query_plan),
            sources=search_profile.sources,
            target_roles=search_profile.target_roles,
            target_locations=search_profile.target_locations,
            minimum_match_score=search_profile.minimum_match_score,
            query_plan=query_plan,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
