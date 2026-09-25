#!/usr/bin/env python3

"""
Background search worker: consumes QUEUED search_queue items and
executes the already-built source adapters, ending at
candidate_job_matches. This module implements exactly one safe
primitive -- run_once() -- a single bounded processing cycle. There is
no daemon loop here; a future scheduler is expected to invoke run_once()
repeatedly.

Pipeline (every step reuses an existing module -- nothing here
re-implements normalization, eligibility, or scoring):

    search_queue (claim)
        -> search_runs.query snapshot (already-queued role/location/source)
        -> source_adapter.SearchQuery  (reconstructed verbatim from the
           snapshot -- the queued search is a historical request, never
           silently recomputed from the candidate's CURRENT preferences)
        -> source_registry.discover_from_sources()  (resolves the
           adapter; this module never imports NaukriAdapter or any
           other concrete adapter class directly)
        -> discover_local.normalize_job() (per raw job; one malformed
           job is skipped, not fatal)
        -> discover_local.deduplicate()  (within this batch)
        -> jobs table UPSERT (tracker.upsert_job() for scored jobs; a
           minimal identity-only upsert for ineligible ones -- see
           _upsert_job_identity_only()'s docstring for why)
        -> job_eligibility.assess_job_eligibility()  (the ONLY
           eligibility gate -- score_job() is never called before this)
        -> score_job(job, candidate_profile, experience_assessment=...)
           only for jobs job_eligibility already deemed eligible
        -> candidate_job_matches UPSERT (candidate_id, job_id) --
           new code in this module, since no prior component wrote to
           this table; every other step above reuses an existing
           function unchanged.

============================================================================
CANDIDATE CONTEXT: frozen query, fresh scoring profile
============================================================================
The QUERY PLAN (which roles/locations/sources to search) is taken
verbatim from search_runs.query -- the JSON snapshot
search_submission.py wrote at submission time -- and is NEVER
recomputed from the candidate's current profile. This is deliberate:
"the queued search is a historical request."

The candidate's PROFILE used for eligibility/scoring, by contrast, is
loaded FRESH via search_submission.load_candidate_confirmed_profile()
at processing time (not frozen in the snapshot). This is also
deliberate, and distinct from the query-plan freezing above: scoring
against the candidate's current, possibly-corrected confirmed profile
is more useful than scoring against a profile that might have been
superseded between submission and processing, and nothing in this
project's architecture asks for a frozen scoring snapshot the way it
explicitly asks for a frozen query snapshot. If the candidate is no
longer ACTIVE or CONFIRMED by the time the worker runs, the queue item
fails cleanly with that exact reason (the same guard search_submission.py
itself already enforces at submission time, reused here via the same
function).

============================================================================
CONCURRENCY LIMITATION (documented, not solved with new infrastructure)
============================================================================
Claiming uses a single SQLite `BEGIN IMMEDIATE` transaction around a
SELECT-then-UPDATE on search_queue.status: QUEUED -> RUNNING. This
correctly prevents double-claiming across sequential invocations of
this worker (including from a second process, since BEGIN IMMEDIATE
takes SQLite's write lock immediately, serializing concurrent claim
attempts against the same file). It is NOT a distributed job queue: no
lease/heartbeat/timeout-based reclaim of a crashed worker's RUNNING
item exists. Per this component's explicit scope, that is not built
here -- a stuck RUNNING item currently requires manual intervention
(see run_once()'s docstring). This is an intentional, documented
limitation, not an oversight.
"""

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "applications" / "jobos.db"

sys.path.insert(0, str(ROOT / "scripts"))

from discover_local import deduplicate, normalize_job
from experience_eligibility import Eligibility
from job_eligibility import assess_job_eligibility
from location_taxonomy import LocationEligibility
from score_job import score_job
from search_submission import SearchSubmissionError, load_candidate_confirmed_profile
from candidate_profile import to_legacy_matching_profile
from source_adapter import SearchQuery
from source_registry import board_name_for_source, discover_from_sources, list_sources
from canonical_job import derive_canonical_job
from cross_source_dedup import find_cross_source_duplicate_candidates
from job_ranking import build_ranking_record
from resume_variant_selector import select_resume_for_candidate, select_resume_for_profile_version
import tracker


# ---------------------------------------------------------------------
# Result / accounting structure
# ---------------------------------------------------------------------

@dataclass
class WorkItemResult:
    queue_id: int
    search_run_id: str
    candidate_id: str
    status: str  # COMPLETED | PARTIAL | BLOCKED | FAILED | SKIPPED (dry-run)
    raw_count: int = 0
    malformed_count: int = 0
    normalized_count: int = 0
    duplicate_count: int = 0
    unique_count: int = 0
    inserted_jobs: int = 0
    existing_jobs: int = 0
    excluded_experience: int = 0
    excluded_location: int = 0
    eligible_count: int = 0
    scored_count: int = 0
    ready_count: int = 0
    matches_created: int = 0
    matches_updated: int = 0
    unimplemented_sources: list = field(default_factory=list)
    blocked_sources: list = field(default_factory=list)
    timed_out_queries: int = 0
    succeeded_queries: int = 0
    errors: list = field(default_factory=list)
    # In-memory-only ranking enrichment (job_ranking.RankingRecord per
    # unique job this cycle) -- never persisted to any DB column. See
    # this module's "RANKING ENRICHMENT" section below for why a
    # failure here is tracked separately from `errors` above: it must
    # never affect errors_count/error_message/status, which existing
    # tests already assert exact values for.
    ranking_records: list = field(default_factory=list)
    ranking_errors: list = field(default_factory=list)
    # Discovery-freshness-constraint reporting (see search_profile.py's
    # max_job_age_days docstring). max_job_age_days_requested is read
    # verbatim from the frozen snapshot -- never recomputed or
    # defaulted here. jobs_exceeding_max_age reuses each job's ALREADY-
    # COMPUTED freshness_age_days from ranking_records above (no second
    # call to freshness.classify_freshness()) -- a purely observational
    # count, never used to filter/drop a job: "ideally zero" when the
    # source's own native filter (see naukri_adapter.py) is working.
    max_job_age_days_requested: int | None = None
    jobs_exceeding_max_age: int = 0


def _now():
    return datetime.now(timezone.utc).isoformat()


def _resolve_pinned_profile_version(conn, search_run_id):
    """
    The saved search's own profile_version PIN for this run, if this
    run is tied to a saved search that has one set -- None for an
    unpinned saved search, or a run with no saved search at all (e.g.
    ad-hoc/CLI submission). See migrate_v8_resume_profile_
    traceability.py and process_queue_item()'s own comment on why this
    is the one, single opt-in exception to "fresh, not frozen" scoring.
    """
    try:
        row = conn.execute(
            """
            SELECT ss.profile_version FROM saved_search_runs ssr
            JOIN saved_searches ss ON ss.saved_search_id = ssr.saved_search_id
            WHERE ssr.search_run_id = ?
            """,
            (search_run_id,),
        ).fetchone()
    except sqlite3.OperationalError as error:
        # Defensive, same posture as search_store.get_run_sources():
        # a DB with no saved_searches/saved_search_runs tables at all
        # (e.g. this module's own lower-level queue/worker tests, which
        # never go through the saved-search layer), or one that
        # predates migrate_v8_resume_profile_traceability.py's
        # profile_version column, simply has no possible pin -- always
        # fresh, exactly like today's default.
        if "no such table" in str(error) or "no such column" in str(error):
            return None
        raise
    return row[0] if row and row[0] is not None else None


def _resolve_used_profile_version(conn, candidate_id, pinned_profile_version):
    """
    The REAL candidate_search_profile.version number actually used for
    scoring: the pin if one was given, else whichever version is
    currently active. Deliberately NOT CandidateProfile.metadata.
    profile_version -- that is a separate, unmaintained field (always
    defaults to 1, see candidate_profile.py's own comment about a
    "future component that manages profile_version history" that was
    never built) and must never be confused with this table's own
    version column.
    """
    if pinned_profile_version is not None:
        return pinned_profile_version
    row = conn.execute(
        "SELECT version FROM candidate_search_profile WHERE candidate_id = ? AND is_active = 1 ORDER BY version DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------

def claim_next_queue_item(conn, candidate_id=None, search_run_id=None):
    """
    Atomically claim the oldest eligible QUEUED search_queue item
    (status QUEUED -> RUNNING), optionally restricted to a specific
    candidate_id and/or search_run_id (a debugging/testing convenience,
    never required for normal operation).

    Uses BEGIN IMMEDIATE so the SELECT-then-UPDATE is not racy against
    a second concurrent caller on the same database file -- see this
    module's docstring for the documented concurrency limitation.

    Returns a dict {"queue_id", "search_run_id", "candidate_id"} for
    the claimed item, or None if nothing eligible is QUEUED.
    """
    conditions = ["sq.status = 'QUEUED'", "sr.status = 'QUEUED'"]
    params = []

    if candidate_id is not None:
        conditions.append("sq.candidate_id = ?")
        params.append(candidate_id)

    if search_run_id is not None:
        conditions.append("sq.search_run_id = ?")
        params.append(search_run_id)

    where_clause = " AND ".join(conditions)

    conn.execute("BEGIN IMMEDIATE")

    try:
        row = conn.execute(
            f"""
            SELECT sq.queue_id, sq.search_run_id, sq.candidate_id
            FROM search_queue sq
            JOIN search_runs sr ON sr.search_run_id = sq.search_run_id
            WHERE {where_clause}
            ORDER BY sq.created_at ASC
            LIMIT 1
            """,
            params,
        ).fetchone()

        if row is None:
            conn.rollback()
            return None

        queue_id, claimed_search_run_id, claimed_candidate_id = row
        now = _now()

        conn.execute(
            "UPDATE search_queue SET status = 'RUNNING', claimed_at = ? WHERE queue_id = ? AND status = 'QUEUED'",
            (now, queue_id),
        )
        conn.execute(
            "UPDATE search_runs SET status = 'RUNNING', started_at = ?, updated_at = ? WHERE search_run_id = ? AND status = 'QUEUED'",
            (now, now, claimed_search_run_id),
        )

        conn.commit()

        return {
            "queue_id": queue_id,
            "search_run_id": claimed_search_run_id,
            "candidate_id": claimed_candidate_id,
        }
    except Exception:
        conn.rollback()
        raise


def peek_next_queue_item(conn, candidate_id=None, search_run_id=None):
    """
    Read-only equivalent of claim_next_queue_item() -- used by
    --dry-run so nothing is claimed and no status changes at all.
    """
    conditions = ["sq.status = 'QUEUED'", "sr.status = 'QUEUED'"]
    params = []

    if candidate_id is not None:
        conditions.append("sq.candidate_id = ?")
        params.append(candidate_id)

    if search_run_id is not None:
        conditions.append("sq.search_run_id = ?")
        params.append(search_run_id)

    where_clause = " AND ".join(conditions)

    row = conn.execute(
        f"""
        SELECT sq.queue_id, sq.search_run_id, sq.candidate_id, sr.query
        FROM search_queue sq
        JOIN search_runs sr ON sr.search_run_id = sq.search_run_id
        WHERE {where_clause}
        ORDER BY sq.created_at ASC
        LIMIT 1
        """,
        params,
    ).fetchone()

    if row is None:
        return None

    queue_id, run_id, cand_id, query_json = row
    try:
        snapshot = json.loads(query_json or "{}")
    except json.JSONDecodeError:
        snapshot = {}

    return {
        "queue_id": queue_id,
        "search_run_id": run_id,
        "candidate_id": cand_id,
        "snapshot": snapshot,
    }


# ---------------------------------------------------------------------
# Query reconstruction from the frozen snapshot
# ---------------------------------------------------------------------

def build_queries_from_snapshot(snapshot):
    """
    Reconstruct (source, SearchQuery) tuples EXACTLY from the frozen
    search_runs.query JSON snapshot -- never from the candidate's
    current preferences. Returns (queries, unimplemented_sources,
    per_source_totals): queries is the list ready for
    source_registry.discover_from_sources(); unimplemented_sources
    lists any requested source with no registered adapter (skipped
    here, never attempted, and never causes the whole batch to crash --
    see this module's docstring on why get_adapter() is not called
    directly for every source blindly).
    """
    registered = set(list_sources())
    entries = snapshot.get("queries", [])

    queries = []
    unimplemented_sources = []
    per_source_totals = {}

    for entry in entries:
        source = entry.get("source")

        if source not in registered:
            if source not in unimplemented_sources:
                unimplemented_sources.append(source)
            continue

        search_query = SearchQuery(
            role=entry.get("role", ""),
            location=entry.get("location", ""),
            max_job_age_days=entry.get("max_job_age_days"),
        )
        queries.append((source, search_query))
        per_source_totals[source] = per_source_totals.get(source, 0) + 1

    return queries, unimplemented_sources, per_source_totals


# ---------------------------------------------------------------------
# Global jobs persistence
# ---------------------------------------------------------------------

def _job_exists(conn, source, job_id):
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE source = ? AND job_id = ?", (source, job_id)
    ).fetchone()
    return row is not None


def _upsert_job_identity_only(conn, job):
    """
    For a job that job_eligibility.py has determined is INELIGIBLE for
    the current candidate: the job still belongs in the GLOBAL jobs
    table (a different candidate might well be eligible for it later),
    but this candidate's ineligibility must never be written into that
    global row's score/priority/matched_skills/missing_skills/
    hard_reject_reasons/status columns -- those are, in this schema,
    effectively single-candidate-era columns with no per-candidate
    disambiguation (see this module's docstring's known-limitation
    note), and writing one candidate's non-match into them would be
    actively misleading to a different candidate reading the same
    global row later.

    This performs a MINIMAL upsert of only the source-provided
    descriptive fields (company, title, location, work_model, urls,
    dates, jd_text, experience_required, skills) via the same
    UNIQUE(source, job_id) identity tracker.upsert_job() itself relies
    on -- it is not a second normalizer or a duplicate of
    tracker.upsert_job()'s logic, only a narrower column set, because
    tracker.upsert_job() unconditionally writes real scoring columns
    this code path must not touch.

    Returns True if this INSERTED a new job row, False if it updated
    an existing one.
    """
    existed = _job_exists(conn, job["source"], job["job_id"])
    now = datetime.now(timezone.utc).isoformat()

    columns = [
        "job_id", "source", "company", "title", "location", "work_model",
        "job_url", "application_url", "posted_date", "discovered_at",
        "jd_text", "experience_required", "mandatory_skills", "preferred_skills",
    ]
    values = [
        job["job_id"],
        job["source"],
        job["company"],
        job["title"],
        job["location"],
        job["work_model"],
        job["job_url"],
        job["application_url"],
        job["posted_date"],
        job["discovered_at"],
        job["jd_text"],
        job["experience_required"],
        json.dumps(job["mandatory_skills"]),
        json.dumps(job["preferred_skills"]),
    ]
    update_clauses = [
        "company = excluded.company",
        "title = excluded.title",
        "location = excluded.location",
        "work_model = excluded.work_model",
        "job_url = excluded.job_url",
        "application_url = excluded.application_url",
        "posted_date = excluded.posted_date",
        "jd_text = excluded.jd_text",
        "experience_required = excluded.experience_required",
        "mandatory_skills = excluded.mandatory_skills",
        "preferred_skills = excluded.preferred_skills",
        "last_updated = CURRENT_TIMESTAMP",
    ]

    # Phase 14, purely additive -- see tracker._has_discovery_columns()'s
    # docstring: a DB that predates that migration keeps working exactly
    # as before, unconditionally, rather than erroring on a missing column.
    if tracker._has_discovery_columns(conn):
        columns += ["discovery_source", "discovery_query", "completeness"]
        values += [
            job.get("discovery_source", "") or "",
            job.get("discovery_query", "") or "",
            job.get("completeness"),
        ]
        update_clauses += [
            "discovery_source = excluded.discovery_source",
            "discovery_query = excluded.discovery_query",
            "completeness = excluded.completeness",
        ]

    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"""
        INSERT INTO jobs ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(source, job_id) DO UPDATE SET
            {", ".join(update_clauses)}
        """,
        values,
    )

    return not existed


def _upsert_job_scored(conn, job, scoring):
    """
    For an ELIGIBLE job: reuse tracker.upsert_job() completely
    unmodified. Returns True if this inserted a new job row, False if
    it updated an existing one.

    Known, documented limitation (inherited from reusing this
    single-candidate-era function unchanged, exactly as this
    component's task instructions require -- "reuse existing job
    normalization and job_id logic," "do not duplicate those
    algorithms"): jobs.score/priority/matched_skills/missing_skills/
    hard_reject_reasons/status reflect whichever candidate most
    recently had this worker score this global job -- they are NOT
    per-candidate. The per-candidate authoritative result is
    candidate_job_matches, which this module also writes for exactly
    this reason.
    """
    existed = _job_exists(conn, job["source"], job["job_id"])
    tracker.upsert_job(conn, job, scoring)
    return not existed


# ---------------------------------------------------------------------
# candidate_job_matches persistence (new -- no prior component wrote here)
# ---------------------------------------------------------------------

def _match_exists(conn, candidate_id, job_id):
    row = conn.execute(
        "SELECT 1 FROM candidate_job_matches WHERE candidate_id = ? AND job_id = ?",
        (candidate_id, job_id),
    ).fetchone()
    return row is not None


def upsert_candidate_job_match(conn, candidate_id, job, scoring, eligibility_result, search_run_id, profile_version=None):
    """
    Create or update the ONE candidate_job_matches row for
    (candidate_id, job_id) -- the table's own PRIMARY KEY already
    enforces "the same candidate + same global job resolves
    deterministically," so this is a plain INSERT ... ON CONFLICT DO
    UPDATE keyed on that existing constraint; no new uniqueness
    mechanism is introduced.

    skill_match_json holds a combined, self-documenting snapshot of
    everything score_job() returned beyond the (also separately
    columned) matched/missing skill lists -- specifically the hard-
    reject reasons, since this table has no dedicated column for them.

    profile_version: which candidate_search_profile.version was
    ACTUALLY used to compute this scoring (see process_queue_item()'s
    "fresh, not frozen [unless pinned]" resolution) -- stamped onto
    this row for full per-match traceability (migrate_v8_resume_
    profile_traceability.py). None (the default, e.g. manual job
    import) means "not tracked for this match," never fabricated.

    Returns True if this inserted a new match row, False if it updated
    an existing one.
    """
    existed = _match_exists(conn, candidate_id, job["job_id"])
    now = _now()

    skill_match_snapshot = {
        "matched_skills": scoring["matched_skills"],
        "missing_skills": scoring["missing_skills"],
        "hard_reject_reasons": scoring["hard_reject_reasons"],
    }

    # Resume selection: when this match was scored against a SPECIFIC
    # profile_version (a pinned search, or any tracked run), prefer the
    # EXACT resume that produced that version (select_resume_for_
    # profile_version) -- never a newer one the candidate may have
    # uploaded since. Only falls back to "candidate's newest resume"
    # (select_resume_for_candidate) when that version has no resume
    # lineage recorded (e.g. a manually-entered profile) or
    # profile_version itself is unknown (manual job import) -- never
    # fabricated either way.
    resume_id, resume_variant = (None, None)
    if profile_version is not None:
        resume_id, resume_variant = select_resume_for_profile_version(conn, candidate_id, profile_version)
    if resume_id is None:
        resume_id, resume_variant = select_resume_for_candidate(conn, candidate_id)

    conn.execute(
        """
        INSERT INTO candidate_job_matches (
            candidate_id, job_id, search_run_id, resume_id, fit_score, priority,
            experience_eligibility, salary_match, location_match,
            skill_match_json, matched_skills_json, missing_skills_json,
            candidate_status, created_at, updated_at, resume_variant, profile_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id, job_id) DO UPDATE SET
            search_run_id = excluded.search_run_id,
            resume_id = excluded.resume_id,
            resume_variant = excluded.resume_variant,
            profile_version = excluded.profile_version,
            fit_score = excluded.fit_score,
            priority = excluded.priority,
            experience_eligibility = excluded.experience_eligibility,
            location_match = excluded.location_match,
            skill_match_json = excluded.skill_match_json,
            matched_skills_json = excluded.matched_skills_json,
            missing_skills_json = excluded.missing_skills_json,
            updated_at = excluded.updated_at
        -- resume_id/resume_variant ARE refreshed on every re-match
        -- (unlike candidate_status below): they reflect which resume
        -- was on file for this candidate at scoring time, a system-
        -- computed fact that should stay current, not a human decision
        -- to protect from being overwritten.
        -- candidate_status is DELIBERATELY absent from this SET clause:
        -- it is only ever seeded from scoring["status"] on the initial
        -- INSERT (this row's very first row.candidate_status value,
        -- below). A later re-match of the SAME (candidate_id, job_id)
        -- -- e.g. this job surfacing again in a subsequent search run --
        -- must never silently overwrite a human's own lifecycle
        -- decision (SHORTLISTED/APPROVED/APPLIED/...) back to whatever
        -- the score bucket recomputed. See the new job-status
        -- transition endpoint (api/main.py) for the only other writer
        -- of this column.
        """,
        (
            candidate_id,
            job["job_id"],
            search_run_id,
            resume_id,
            scoring["score"],
            scoring["priority"],
            eligibility_result.experience_assessment.eligibility.value,
            eligibility_result.location_assessment.eligibility.value,
            json.dumps(skill_match_snapshot),
            json.dumps(scoring["matched_skills"]),
            json.dumps(scoring["missing_skills"]),
            scoring["status"],
            now,
            now,
            resume_variant,
            profile_version,
        ),
    )

    # Per-search-run scoring snapshot (item 5 fix -- see
    # migrate_v9_candidate_job_search_matches.py's own docstring for
    # the full root-cause writeup): one row per (search_run_id,
    # job_id), NEVER overwritten by a different run -- this is what
    # makes an earlier search's own results/traceability immune to a
    # later, different search re-matching the same job. Skipped for a
    # manual import (search_run_id=None here) -- manual imports have
    # no search run to attribute a scoring snapshot to; the single
    # candidate_job_matches row above is already the complete record
    # for that case, unchanged.
    if search_run_id is not None:
        conn.execute(
            """
            INSERT INTO candidate_job_search_matches (
                search_run_id, job_id, candidate_id, fit_score, priority,
                experience_eligibility, location_match, skill_match_json,
                matched_skills_json, missing_skills_json, resume_id,
                resume_variant, profile_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(search_run_id, job_id) DO UPDATE SET
                fit_score = excluded.fit_score,
                priority = excluded.priority,
                experience_eligibility = excluded.experience_eligibility,
                location_match = excluded.location_match,
                skill_match_json = excluded.skill_match_json,
                matched_skills_json = excluded.matched_skills_json,
                missing_skills_json = excluded.missing_skills_json,
                resume_id = excluded.resume_id,
                resume_variant = excluded.resume_variant,
                profile_version = excluded.profile_version
            """,
            (
                search_run_id,
                job["job_id"],
                candidate_id,
                scoring["score"],
                scoring["priority"],
                eligibility_result.experience_assessment.eligibility.value,
                eligibility_result.location_assessment.eligibility.value,
                json.dumps(skill_match_snapshot),
                json.dumps(scoring["matched_skills"]),
                json.dumps(scoring["missing_skills"]),
                resume_id,
                resume_variant,
                profile_version,
                now,
            ),
        )

    return not existed


# ---------------------------------------------------------------------
# Manual job import (Phase 13 broad-discovery, Part 13)
# ---------------------------------------------------------------------

@dataclass
class ManualImportResult:
    job_id: str
    source: str
    company: str
    title: str
    eligible: bool
    eligibility_reason: str = ""
    score: int | None = None
    priority: str | None = None
    status: str | None = None
    matched_skills: list = field(default_factory=list)
    missing_skills: list = field(default_factory=list)
    match_created: bool = False


class ManualImportError(ValueError):
    """Raised when the candidate's profile is not CONFIRMED -- the same
    gate process_queue_item() enforces for a real search run (via
    load_candidate_confirmed_profile()), reused here unchanged rather
    than re-implemented, so a manually-imported job is scored under
    exactly the same eligibility rules as one found by a live adapter."""


def import_manual_job(conn, candidate_id, raw_job):
    """
    One human-supplied job (already validated + shaped by
    scripts/manual_import.py's build_manual_import_raw_job()) through
    the SAME normalize -> eligibility -> score -> persist steps
    process_queue_item() uses for an adapter-discovered job -- no
    second scoring/eligibility implementation. The only things this
    function does NOT do that process_queue_item() does: there is no
    search_queue/search_runs row (search_run_id is written as NULL --
    a nullable column, see migrate_v2_schema.py), and there is no
    adapter/network execution (the "raw job" already came from the
    human, not discover_from_sources()).

    Runs in its own transaction; commits on success, rolls back and
    re-raises on any exception -- callers never see a half-written
    job/match pair.
    """
    normalized = normalize_job(dict(raw_job), 1)

    try:
        candidate_profile = load_candidate_confirmed_profile(conn, candidate_id)
        legacy_profile = to_legacy_matching_profile(candidate_profile)
    except SearchSubmissionError as error:
        raise ManualImportError(str(error)) from error
    except ValueError as error:
        # to_legacy_matching_profile() raises a plain ValueError (not
        # SearchSubmissionError) for a non-CONFIRMED profile -- see its
        # own docstring/CLAUDE.md's DRAFT/CONFIRMED rule. This is the
        # exact same gate a real search run is protected by (submit_
        # search() checks profile_status before ever queuing a run);
        # manual import has no equivalent pre-check step, so it is
        # enforced here instead, at the same place the error actually
        # originates -- never weakened, never auto-confirmed.
        raise ManualImportError(str(error)) from error

    conn.execute("BEGIN")
    try:
        eligibility_result = assess_job_eligibility(normalized, legacy_profile)

        if not eligibility_result.eligible:
            _upsert_job_identity_only(conn, normalized)
            conn.commit()
            return ManualImportResult(
                job_id=normalized["job_id"],
                source=normalized["source"],
                company=normalized["company"],
                title=normalized["title"],
                eligible=False,
                eligibility_reason=eligibility_result.reason,
            )

        scoring = score_job(normalized, legacy_profile, experience_assessment=eligibility_result.experience_assessment)
        _upsert_job_scored(conn, normalized, scoring)
        match_created = upsert_candidate_job_match(
            conn, candidate_id, normalized, scoring, eligibility_result, search_run_id=None,
            profile_version=_resolve_used_profile_version(conn, candidate_id, None),
        )
        conn.commit()

        return ManualImportResult(
            job_id=normalized["job_id"],
            source=normalized["source"],
            company=normalized["company"],
            title=normalized["title"],
            eligible=True,
            score=scoring["score"],
            priority=scoring["priority"],
            status=scoring["status"],
            matched_skills=scoring["matched_skills"],
            missing_skills=scoring["missing_skills"],
            match_created=match_created,
        )
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------
# Aggregate status
# ---------------------------------------------------------------------

def _aggregate_status(run_report, had_unexpected_exception, any_query_attempted):
    if had_unexpected_exception:
        return "FAILED"

    if not any_query_attempted:
        return "FAILED"

    any_blocked = any(state.blocked for state in run_report)
    any_succeeded_query = any(state.queries_succeeded > 0 for state in run_report)
    any_failed_query = any(state.queries_failed > 0 for state in run_report)

    if any_blocked and not any_succeeded_query:
        return "BLOCKED"

    if (any_blocked or any_failed_query) and any_succeeded_query:
        return "PARTIAL"

    if any_succeeded_query and not any_blocked and not any_failed_query:
        return "COMPLETED"

    return "FAILED"


# ---------------------------------------------------------------------
# Per-source execution audit (search_run_sources)
# ---------------------------------------------------------------------

# Six distinct states, deliberately never collapsed into one another --
# see migrate_v5_search_run_sources.py's module docstring for why this
# table exists at all (search_runs itself only ever stored run-wide
# aggregates, never per-source detail).
SOURCE_STATUS_NOT_ATTEMPTED = "NOT_ATTEMPTED"
SOURCE_STATUS_NOT_CONFIGURED = "NOT_CONFIGURED"
SOURCE_STATUS_BLOCKED = "BLOCKED"
SOURCE_STATUS_FAILED = "FAILED"
SOURCE_STATUS_ZERO = "ZERO"
SOURCE_STATUS_SUCCESS = "SUCCESS"


def _compute_source_status(state):
    """Pure function: one SourceRunState (or None, for a requested
    source with genuinely no adapter registered at all) -> one of the
    six SOURCE_STATUS_* constants, plus (error_type, error_message)."""
    if state is None:
        return SOURCE_STATUS_NOT_ATTEMPTED, "NO_ADAPTER", "No adapter is registered for this source."

    if state.not_enabled:
        detail = f"adapter_status={state.adapter_status}" if state.adapter_status else ""
        return SOURCE_STATUS_NOT_CONFIGURED, "NOT_CONFIGURED", detail or "Adapter is not ENABLED (no provider key configured, or a direct-crawl skeleton)."

    if state.blocked:
        error_type = state.blocked_reason.value if state.blocked_reason else "BLOCKED"
        return SOURCE_STATUS_BLOCKED, error_type, "Source blocked the request (health check or search())."

    if state.queries_attempted == 0:
        return SOURCE_STATUS_NOT_ATTEMPTED, None, "Enabled and reachable, but zero queries were issued for this source in this run's plan."

    if state.queries_succeeded == 0:
        return SOURCE_STATUS_FAILED, "QUERY_FAILED", f"All {state.queries_attempted} quer{'y' if state.queries_attempted == 1 else 'ies'} failed (timeout/error) without a block."

    if state.jobs_found == 0:
        return SOURCE_STATUS_ZERO, None, None

    return SOURCE_STATUS_SUCCESS, None, None


def _persist_search_run_sources(conn, search_run_id, queries, run_report, unimplemented_sources, ranking_records):
    """
    One row per requested source (every registry key that appeared in
    this run's query plan, PLUS any unimplemented_sources that never
    even made it into the plan), persisting exactly what run_report
    (source_registry.discover_from_sources()'s in-memory-only output)
    already computed -- never re-executes anything, makes no network
    call.

    Deliberately defensive: if search_run_sources doesn't exist yet
    (an older DB that hasn't run migrate_v5_search_run_sources.py),
    this silently no-ops rather than failing the whole queue item --
    this table is purely additive audit information, never load-bearing
    for the existing jobs/candidate_job_matches pipeline.

    Runs in its OWN short transaction, after the main persistence
    transaction has already committed -- matches this module's existing
    "DB writes in short, separate transactions" convention (see
    _finalize_queue_item, and the "RANKING ENRICHMENT" section's own
    comment on why it runs strictly after that commit).
    """
    requested_sources = list(dict.fromkeys([source for source, _ in queries] + list(unimplemented_sources)))
    if not requested_sources:
        return

    state_by_source = {s.source: s for s in run_report}

    eligible_by_board = {}
    for record in ranking_records:
        if record.eligible:
            eligible_by_board[record.source] = eligible_by_board.get(record.source, 0) + 1

    now = _now()
    rows = []
    for source in requested_sources:
        state = state_by_source.get(source)
        status, error_type, error_message = _compute_source_status(state)
        board = board_name_for_source(source)
        eligible_count = eligible_by_board.get(board, 0)

        started_at = state.started_at if state else None
        completed_at = state.completed_at if state else None
        duration_ms = None
        if started_at and completed_at:
            try:
                duration_ms = int(
                    (datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)).total_seconds() * 1000
                )
            except ValueError:
                duration_ms = None

        rows.append(
            (
                search_run_id,
                source,
                1 if status not in (SOURCE_STATUS_NOT_ATTEMPTED, SOURCE_STATUS_NOT_CONFIGURED) else 0,
                started_at,
                completed_at,
                duration_ms,
                (1 if state.health.reachable else 0) if (state and state.health) else None,
                (1 if state.queries_succeeded > 0 else 0) if state else None,
                state.jobs_found if state else 0,
                eligible_count,
                # displayed_count: at the moment a search completes,
                # every eligible job it found IS what a caller viewing
                # results right now would see -- results_store.py never
                # applies additional per-source filtering beyond
                # eligibility. Recorded here as a snapshot, not
                # recomputed live by this worker.
                eligible_count,
                status,
                error_type,
                error_message,
                now,
                # Adapter-reported extra facts (e.g. Apna's detail-fetch
                # attempted/succeeded/failed counts) -- {} (serialized
                # as an empty-object JSON string, never NULL/omitted)
                # for every other adapter that doesn't report any, so
                # a reader can always safely json.loads() this column.
                json.dumps(state.extra_details) if (state and state.extra_details) else json.dumps({}),
            )
        )

    conn.execute("BEGIN")
    try:
        conn.executemany(
            """
            INSERT INTO search_run_sources (
                search_run_id, source, attempted, started_at, completed_at, duration_ms,
                reachable, succeeded, raw_count, eligible_count, displayed_count,
                status, error_type, error_message, created_at, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(search_run_id, source) DO UPDATE SET
                attempted = excluded.attempted, started_at = excluded.started_at,
                completed_at = excluded.completed_at, duration_ms = excluded.duration_ms,
                reachable = excluded.reachable, succeeded = excluded.succeeded,
                raw_count = excluded.raw_count, eligible_count = excluded.eligible_count,
                displayed_count = excluded.displayed_count, status = excluded.status,
                error_type = excluded.error_type, error_message = excluded.error_message,
                details_json = excluded.details_json
            """,
            rows,
        )
        conn.commit()
    except sqlite3.OperationalError as error:
        conn.rollback()
        if "no such table" not in str(error):
            raise


# ---------------------------------------------------------------------
# Per-item processing
# ---------------------------------------------------------------------

def process_queue_item(conn, claimed, db_write_lock_note=None):
    """
    Process ONE already-claimed queue item end to end. `claimed` is the
    dict returned by claim_next_queue_item(). Returns a WorkItemResult.

    Network/adapter execution (discover_from_sources()) happens with NO
    database transaction open. All DB writes (job/match persistence,
    then final queue/run status) happen in their own short-lived
    transactions -- see this module's docstring's "Transaction
    Boundaries" note.
    """
    queue_id = claimed["queue_id"]
    search_run_id = claimed["search_run_id"]
    candidate_id = claimed["candidate_id"]

    result = WorkItemResult(queue_id=queue_id, search_run_id=search_run_id, candidate_id=candidate_id, status="FAILED")

    run_row = conn.execute(
        "SELECT query FROM search_runs WHERE search_run_id = ?", (search_run_id,)
    ).fetchone()

    try:
        snapshot = json.loads((run_row[0] if run_row else None) or "{}")
    except json.JSONDecodeError:
        snapshot = {}

    queries, unimplemented_sources, per_source_totals = build_queries_from_snapshot(snapshot)
    result.unimplemented_sources = unimplemented_sources

    # Read verbatim from the frozen snapshot -- every query entry in one
    # search_run shares the same candidate-level max_job_age_days (see
    # query_planner.build_queries_from_search_profile()), so the first
    # entry that has one set is authoritative for this whole run.
    result.max_job_age_days_requested = next(
        (
            entry.get("max_job_age_days")
            for entry in snapshot.get("queries", [])
            if entry.get("max_job_age_days") is not None
        ),
        None,
    )

    # Resolve the candidate's profile: fresh (current active) by
    # default -- see module docstring's "fresh, not frozen" rationale
    # -- UNLESS this run's own saved search explicitly PINS a specific
    # historical profile_version (migrate_v8_resume_profile_
    # traceability.py). This is an opt-in exception, added for full
    # resume/profile traceability: a search pinned to an older
    # resume/profile must never silently rescore against a newer one
    # uploaded afterward. A run with no pinned saved search (or an
    # unpinned one) behaves EXACTLY as before -- pinned_profile_version
    # is None, load_candidate_confirmed_profile() loads the active
    # profile exactly like it always has.
    pinned_profile_version = _resolve_pinned_profile_version(conn, search_run_id)
    try:
        candidate_profile = load_candidate_confirmed_profile(conn, candidate_id, profile_version=pinned_profile_version)
        legacy_profile = to_legacy_matching_profile(candidate_profile)
        used_profile_version = _resolve_used_profile_version(conn, candidate_id, pinned_profile_version)
    except SearchSubmissionError as error:
        result.errors.append(str(error))
        _finalize_queue_item(conn, queue_id, search_run_id, "FAILED", result, error_message=str(error))
        return result

    if not queries:
        message = "No executable queries in this search_run's snapshot (all sources unimplemented or plan was empty)."
        result.errors.append(message)
        _finalize_queue_item(conn, queue_id, search_run_id, "FAILED", result, error_message=message)
        return result

    # --- network/adapter execution: NO transaction open here ---
    run_report = []
    had_unexpected_exception = False
    raw_jobs = []

    started_at = _now()

    try:
        raw_jobs = discover_from_sources(queries, run_report=run_report)
    except Exception as error:
        had_unexpected_exception = True
        result.errors.append(f"Unexpected exception during source execution: {error}")

    result.raw_count = len(raw_jobs)
    result.blocked_sources = [state.source for state in run_report if state.blocked]
    result.timed_out_queries = sum(state.queries_failed for state in run_report)
    result.succeeded_queries = sum(state.queries_succeeded for state in run_report)

    any_query_attempted = any(state.queries_attempted > 0 for state in run_report)

    # --- normalization (pure, no DB) ---
    normalized_jobs = []
    for index, raw_job in enumerate(raw_jobs, start=1):
        try:
            raw_job = dict(raw_job)
            raw_job.setdefault("discovered_at", started_at)
            normalized_jobs.append(normalize_job(raw_job, index))
        except Exception as error:
            result.malformed_count += 1
            result.errors.append(f"Malformed raw job at index {index}: {error}")

    result.normalized_count = len(normalized_jobs)

    unique_jobs, duplicate_count = deduplicate(normalized_jobs)
    result.duplicate_count = duplicate_count
    result.unique_count = len(unique_jobs)

    # --- persistence: one transaction, no network activity inside it ---
    conn.execute("BEGIN")

    try:
        for job in unique_jobs:
            try:
                eligibility_result = assess_job_eligibility(job, legacy_profile)
            except Exception as error:
                result.errors.append(f"Eligibility evaluation failed for job {job.get('job_id')}: {error}")
                continue

            if not eligibility_result.eligible:
                if eligibility_result.experience_assessment.eligibility == Eligibility.BELOW_PROFILE:
                    result.excluded_experience += 1
                if eligibility_result.location_assessment.eligibility == LocationEligibility.NO_MATCH:
                    result.excluded_location += 1

                inserted = _upsert_job_identity_only(conn, job)
                if inserted:
                    result.inserted_jobs += 1
                else:
                    result.existing_jobs += 1
                continue

            result.eligible_count += 1

            try:
                scoring = score_job(
                    job, legacy_profile, experience_assessment=eligibility_result.experience_assessment
                )
            except Exception as error:
                result.errors.append(f"Scoring failed for job {job.get('job_id')}: {error}")
                continue

            inserted = _upsert_job_scored(conn, job, scoring)
            if inserted:
                result.inserted_jobs += 1
            else:
                result.existing_jobs += 1

            result.scored_count += 1
            if scoring["status"] == "READY_FOR_APPROVAL":
                result.ready_count += 1

            match_inserted = upsert_candidate_job_match(
                conn, candidate_id, job, scoring, eligibility_result, search_run_id,
                profile_version=used_profile_version,
            )
            if match_inserted:
                result.matches_created += 1
            else:
                result.matches_updated += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # ------------------------------------------------------------------
    # RANKING ENRICHMENT (in-memory only -- no DB writes, no schema)
    # ------------------------------------------------------------------
    # Runs strictly AFTER the persistence transaction above has already
    # committed, over the same `unique_jobs` that transaction already
    # processed. This never re-opens that transaction and never touches
    # a jobs/candidate_job_matches row -- it exists purely to attach a
    # job_ranking.RankingRecord (score explanation + freshness +
    # cross-source duplicate-candidate info) to `result` for callers
    # that want it (e.g. this component's own tests, or a future
    # report/CLI layer), without persisting anything new.
    #
    # Reuses score_job()/assess_job_eligibility() a second time (via
    # build_ranking_record()) rather than threading the already-computed
    # eligibility_result/scoring out of the loop above -- a deliberate,
    # minimal choice: both functions are pure and deterministic, so a
    # second call on the same job dict always reproduces the identical
    # result computed above, and this keeps the existing, already-tested
    # persistence loop completely untouched rather than restructured to
    # smuggle extra values out of it.
    #
    # Failures here are tracked in `ranking_errors`, a SEPARATE list from
    # `errors` -- deliberately never folded into `errors_count`/
    # `error_message` (see _finalize_queue_item()) or into `status` (see
    # _aggregate_status(), which never reads ranking_records/
    # ranking_errors at all). A ranking-enrichment failure must never
    # corrupt queue/run state or change existing test-asserted values.
    try:
        canonical_jobs = [derive_canonical_job(job) for job in unique_jobs]
        cross_source_duplicates = find_cross_source_duplicate_candidates(canonical_jobs)
    except Exception as error:
        cross_source_duplicates = []
        result.ranking_errors.append(f"Cross-source duplicate detection failed (ranking only): {error}")

    duplicates_by_identity = {}
    for dup_candidate in cross_source_duplicates:
        for cjob in (dup_candidate.job_a, dup_candidate.job_b):
            duplicates_by_identity.setdefault((cjob.source, cjob.source_job_id), []).append(dup_candidate)

    for job in unique_jobs:
        try:
            key = (job.get("source"), job.get("job_id"))
            ranking_record = build_ranking_record(
                job, legacy_profile, duplicate_candidates=duplicates_by_identity.get(key, [])
            )
            result.ranking_records.append(ranking_record)
        except Exception as error:
            result.ranking_errors.append(
                f"Ranking record construction failed for job {job.get('job_id')} (ranking only): {error}"
            )

    if result.max_job_age_days_requested is not None:
        result.jobs_exceeding_max_age = sum(
            1
            for rr in result.ranking_records
            if rr.freshness_age_days is not None and rr.freshness_age_days > result.max_job_age_days_requested
        )

    # Per-source execution audit (search_run_sources) -- additive,
    # never load-bearing: a failure here must never corrupt queue/run
    # state, exactly like the ranking-enrichment section above.
    try:
        _persist_search_run_sources(conn, search_run_id, queries, run_report, unimplemented_sources, result.ranking_records)
    except Exception as error:
        result.ranking_errors.append(f"search_run_sources persistence failed (audit only): {error}")

    status = _aggregate_status(run_report, had_unexpected_exception, any_query_attempted)
    error_message = "; ".join(result.errors[:5]) if result.errors else None

    _finalize_queue_item(conn, queue_id, search_run_id, status, result, error_message=error_message, started_at=started_at)

    return result


def _finalize_queue_item(conn, queue_id, search_run_id, status, result, error_message=None, started_at=None):
    """
    Final, short-lived transaction: update search_queue and search_runs
    to their terminal state for this cycle. Always runs -- including on
    the early-failure paths above -- so a queue item never remains
    misleadingly RUNNING.
    """
    now = _now()

    conn.execute("BEGIN")
    try:
        conn.execute(
            "UPDATE search_queue SET status = ?, completed_at = ? WHERE queue_id = ?",
            (status, now, queue_id),
        )
        conn.execute(
            """
            UPDATE search_runs
            SET status = ?, completed_at = ?, updated_at = ?,
                queries_completed = ?, jobs_discovered = ?, jobs_deduplicated = ?,
                jobs_experience_excluded = ?, jobs_eligible = ?, jobs_scored = ?,
                jobs_ready = ?, errors_count = ?, blocked_queries = ?, error_message = ?
            WHERE search_run_id = ?
            """,
            (
                status,
                now,
                now,
                result.succeeded_queries + result.timed_out_queries,
                result.raw_count,
                result.duplicate_count,
                result.excluded_experience,
                result.eligible_count,
                result.scored_count,
                result.ready_count,
                result.malformed_count + len(result.errors),
                len(result.blocked_sources),
                error_message,
                search_run_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    result.status = status


# ---------------------------------------------------------------------
# CLI-facing orchestration
# ---------------------------------------------------------------------

def run_once(db_path, candidate_id=None, search_run_id=None, max_items=1, dry_run=False):
    """
    The single safe primitive this component provides: claim and
    process up to `max_items` eligible QUEUED search_queue items, then
    return. Never loops indefinitely; never daemonizes.

    dry_run=True claims NOTHING (uses peek_next_queue_item() instead)
    and makes NO source-adapter call at all -- it only reports what
    WOULD be processed next, for a safe plan/inspection preview.

    A RUNNING item left behind by a crashed prior worker invocation is
    NOT automatically reclaimed by this function (see module docstring)
    -- resetting such an item to QUEUED (or FAILED) is currently a
    manual/administrative action, consistent with this component's
    explicit scope (no retry framework).
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    results = []

    try:
        for _ in range(max_items):
            if dry_run:
                claimed = peek_next_queue_item(conn, candidate_id=candidate_id, search_run_id=search_run_id)
                if claimed is None:
                    break

                snapshot = claimed["snapshot"]
                results.append(
                    WorkItemResult(
                        queue_id=claimed["queue_id"],
                        search_run_id=claimed["search_run_id"],
                        candidate_id=claimed["candidate_id"],
                        status="SKIPPED",
                        raw_count=0,
                        unique_count=len(snapshot.get("queries", [])),
                    )
                )
                break

            claimed = claim_next_queue_item(conn, candidate_id=candidate_id, search_run_id=search_run_id)
            if claimed is None:
                break

            result = process_queue_item(conn, claimed)
            results.append(result)
    finally:
        conn.close()

    return results
