"""
Phase 9 results/report retrieval. Reuses generate_run_report.py's
existing load_jobs()/load_candidate_job_matches()/build_report_rows()
(scoring, eligibility, freshness, cross-source dedup all computed by
the existing pipeline they already call) -- adds only a thin,
read-only scoping query to attribute matches to one particular saved
search's own run(s), and a thin dict projection for the JSON API
response. No second scoring/eligibility/report implementation.

Post-Phase-11 bugfix: get_results_for_saved_search() used to
unconditionally call candidate_profile.to_legacy_matching_profile(),
which refuses a non-CONFIRMED profile by design (see that function's
own docstring) -- correct for LIVE re-scoring, but wrong for simply
VIEWING a search's own already-persisted matches, which caused a 500
the moment a candidate's profile went back to DRAFT (e.g. after
editing it) while an earlier CONFIRMED run's results still existed.
The fix keeps live re-scoring for a currently-CONFIRMED profile
byte-for-byte unchanged, and adds a separate, explicit path
(_load_persisted_results()) that serves already-computed
candidate_job_matches rows directly when the profile is not CONFIRMED
-- never calling to_legacy_matching_profile()/score_job()/
job_eligibility() in that case. See ResultsNotReadyError for the one
case where no results can be shown at all.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from candidate_profile import apply_search_target_override, to_legacy_matching_profile
import generate_run_report as report_mod
from freshness import classify_freshness
from experience_eligibility import detect_requirement_type

from profile_store import load_active_profile, load_profile_version, ProfileStoreError
from search_store import get_latest_run_id_for_search, list_run_ids_for_search


class ResultsNotReadyError(Exception):
    """
    Raised only when NEITHER a live rescore NOR a persisted result is
    available: the candidate's profile is not CONFIRMED (so a live
    rescore cannot run) AND this search has no candidate_job_matches
    rows yet either (it has never actually been run against a
    CONFIRMED profile). Never raised merely because the profile is
    DRAFT while persisted results already exist -- see
    get_results_for_saved_search().
    """


def _job_ids_for_search(conn, candidate_id, saved_search_id):
    """
    Item 5 fix (see migrate_v9_candidate_job_search_matches.py): scoped
    from candidate_job_search_matches -- ONE row per (search_run_id,
    job_id), never overwritten by a DIFFERENT run/search -- instead of
    the single-row-per-(candidate,job) candidate_job_matches, whose
    search_run_id column used to get silently overwritten by whichever
    search most recently re-matched the same job, causing an earlier
    search to lose that job from its OWN results view entirely.
    """
    run_ids = list_run_ids_for_search(conn, saved_search_id)
    if not run_ids:
        return set()
    placeholders = ",".join("?" for _ in run_ids)
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT job_id FROM candidate_job_search_matches
            WHERE candidate_id = ? AND search_run_id IN ({placeholders})
            """,
            [candidate_id, *run_ids],
        ).fetchall()
    except Exception as error:
        if "no such table" not in str(error):
            raise
        # Defensive fallback for a DB that predates migrate_v9 (same
        # posture as search_store.get_run_sources() for migrate_v5) --
        # the OLD, collision-prone behavior, strictly better than
        # erroring outright.
        rows = conn.execute(
            f"""
            SELECT DISTINCT job_id FROM candidate_job_matches
            WHERE candidate_id = ? AND search_run_id IN ({placeholders})
            """,
            [candidate_id, *run_ids],
        ).fetchall()
    return {r[0] for r in rows}


def _query_plan_entries_for_search(conn, saved_search_id):
    """
    The frozen query-plan "queries" list (each a {"role","location",...}
    dict) from this saved search's own most recent run -- read-only,
    for apply_search_target_override() below. [] if the search has
    never been run (the live rescore then simply uses the candidate's
    profile-level target_roles/target_locations exactly as before this
    fix). Uses the same canonical "latest run for this search"
    resolution as everything else (get_latest_run_id_for_search()) --
    never a second, independent "which run" computation.
    """
    run_id = get_latest_run_id_for_search(conn, saved_search_id)
    if run_id is None:
        return []
    row = conn.execute(
        "SELECT query FROM search_runs WHERE search_run_id = ?", (run_id,)
    ).fetchone()
    if row is None or not row[0]:
        return []
    try:
        return json.loads(row[0]).get("queries", [])
    except (json.JSONDecodeError, AttributeError):
        return []


def _match_reason(ranking):
    """Short, honest one-line summary derived entirely from the
    EXISTING score_explanation dict (job_ranking.py) -- never a new
    scoring computation, purely a display projection."""
    explanation = ranking.score_explanation
    if not explanation:
        return "" if ranking.eligible else "; ".join(ranking.eligibility_reasons or []) or "Not eligible"

    parts = []
    if explanation.get("strong_matches"):
        parts.append("Matches: " + ", ".join(explanation["strong_matches"][:4]))
    if explanation.get("gaps"):
        parts.append("Gaps: " + ", ".join(explanation["gaps"][:4]))
    if explanation.get("eligibility_reasons"):
        parts.append("; ".join(explanation["eligibility_reasons"][:2]))
    return " | ".join(parts) if parts else ""


def _row_to_result_dict(row):
    ranking = row.ranking
    explanation = ranking.score_explanation or {}
    return {
        "job_id": ranking.job_id,
        "source": ranking.source,
        "title": ranking.title,
        "company": ranking.company,
        "location": row.location_raw,
        "job_url": ranking.job_url,
        "application_url": row.application_url,
        "score": ranking.score,
        "priority": ranking.priority,
        "eligible": row.eligible,
        "eligibility": ranking.eligibility,
        "eligibility_reasons": ranking.eligibility_reasons,
        "freshness": ranking.freshness,
        "freshness_age_days": ranking.freshness_age_days,
        "experience_required": row.experience_required,
        "requirement_type": ranking.requirement_type,
        "status": row.status,
        "report_status": row.report_status,
        "match_reason": _match_reason(ranking),
        # Phase 13 (GUI result-detail view): the SAME already-computed
        # score_explanation fields _match_reason() above already reads,
        # exposed as separate lists instead of one bundled string --
        # no new scoring, purely an additional display projection.
        "matched_skills": list(explanation.get("strong_matches") or []),
        "missing_skills": list(explanation.get("gaps") or []),
        "resume_variant": row.resume_variant,
        "follow_up_date": row.follow_up_date,
        "first_discovered": row.first_discovered,
        "last_seen": row.last_seen,
        "duplicate_suppressed": row.duplicate_suppressed,
        # Phase 7 (dedup audit): the OTHER source(s) this job was
        # cross-source-matched against (job_ranking.RankingRecord's own
        # duplicate_candidates, already-serialized dicts with
        # other_source/other_job_url/confidence) -- previously only the
        # boolean duplicate_suppressed survived into this API response,
        # silently dropping which other source(s)/URL(s) a suppressed
        # job's own representative actually came from. Deduplication
        # must never delete source information, only flag it.
        "duplicate_candidates": ranking.duplicate_candidates,
    }


def _match_reason_from_persisted(matched_skills, missing_skills, hard_reject_reasons):
    """Same display-projection idea as _match_reason(), built entirely
    from already-persisted candidate_job_matches columns instead of a
    freshly-computed score_explanation -- used by the DRAFT/persisted
    path below. Never a new scoring computation."""
    parts = []
    if matched_skills:
        parts.append("Matches: " + ", ".join(matched_skills[:4]))
    if missing_skills:
        parts.append("Gaps: " + ", ".join(missing_skills[:4]))
    if hard_reject_reasons:
        parts.append("; ".join(hard_reject_reasons[:2]))
    return " | ".join(parts) if parts else ""


def _persisted_report_status(priority, candidate_status, job_status):
    """
    A reduced-fidelity mirror of generate_run_report._compute_report_status()
    for a row that is ALREADY known to be eligible (every
    candidate_job_matches row represents an eligible job by
    construction -- search_worker.upsert_candidate_job_match() is only
    ever called from process_queue_item()'s eligible branch, see that
    function's own docstring), built from already-persisted values
    only. Does not distinguish READY_TO_APPLY from ELIGIBLE_NOT_APPLIED
    (that distinction depends on freshness/duplicate-suppression
    computed fresh over the whole job table by the live path) or apply
    duplicate suppression -- an accepted, honest reduction in fidelity
    for this no-rescore path, never a fabricated value.
    """
    if candidate_status in report_mod.SCORE_BUCKET_REJECTION_STATUSES:
        return "SCORE_REJECTED_NOT_QUALIFIED"
    if job_status in report_mod.APPLIED_LIFECYCLE_STATUSES:
        return job_status
    if priority not in report_mod.QUALIFYING_PRIORITIES:
        return "SCORE_REJECTED_NOT_QUALIFIED"
    return "ELIGIBLE_NOT_APPLIED"


def _load_persisted_results(conn, candidate_id, job_ids, saved_search_id=None):
    """
    Build result rows directly from already-persisted
    candidate_job_matches + jobs columns -- no candidate_profile is
    loaded, and no scoring/eligibility function is called. Used
    whenever the candidate's profile is not CONFIRMED: viewing a
    search's own already-computed matches must never require (or
    silently trigger) a fresh CONFIRMED-profile rescore.

    classify_freshness() is the one computation performed here -- it
    is profile-independent (depends only on posted_date), exactly like
    job_ranking.py's own use of it for the live path.

    saved_search_id (item 5 fix): when given, fit_score/priority/
    skills/resume_variant/resume_id are read from THIS search's own
    candidate_job_search_matches rows (scoped to its own run_ids) in
    preference to the candidate-wide candidate_job_matches snapshot,
    which a DIFFERENT search re-matching the same job could have since
    overwritten. Falls back to candidate_job_matches for any job with
    no scoped row yet (a DB that predates migrate_v9, or a job matched
    before this fix existed) -- never a missing value.
    """
    if not job_ids:
        return []

    placeholders = ",".join("?" for _ in job_ids)

    scoped_rows_by_job = {}
    if saved_search_id is not None:
        run_ids = list_run_ids_for_search(conn, saved_search_id)
        if run_ids:
            run_placeholders = ",".join("?" for _ in run_ids)
            try:
                cursor = conn.cursor()
                cursor.row_factory = __import__("sqlite3").Row
                scoped = cursor.execute(
                    f"""
                    SELECT job_id, fit_score, priority, matched_skills_json, missing_skills_json,
                           skill_match_json, resume_id, resume_variant
                    FROM candidate_job_search_matches
                    WHERE candidate_id = ? AND search_run_id IN ({run_placeholders})
                    """,
                    [candidate_id, *run_ids],
                ).fetchall()
                scoped_rows_by_job = {r["job_id"]: dict(r) for r in scoped}
            except Exception as error:
                if "no such table" not in str(error):
                    raise

    rows = conn.execute(
        f"""
        SELECT j.job_id, j.source, j.title, j.company, j.location, j.job_url,
               j.application_url, j.posted_date, j.experience_required,
               j.status, j.resume_variant, j.created_at, j.last_updated,
               cjm.fit_score, cjm.priority, cjm.candidate_status,
               cjm.matched_skills_json, cjm.missing_skills_json, cjm.skill_match_json,
               cjm.resume_variant AS candidate_resume_variant
        FROM candidate_job_matches cjm
        JOIN jobs j ON j.job_id = cjm.job_id
        WHERE cjm.candidate_id = ? AND cjm.job_id IN ({placeholders})
        """,
        [candidate_id, *job_ids],
    ).fetchall()

    results = []
    for row in rows:
        # Prefer THIS search's own scoped scoring snapshot (item 5 fix)
        # for fit_score/priority/skills/resume_variant -- these are
        # per-search-run facts, never candidate-wide ones; fall back to
        # the candidate-wide snapshot (cjm.*) only when no scoped row
        # exists yet. candidate_status/report_status are DELIBERATELY
        # excluded from this override -- those remain candidate+job
        # scoped, read from cjm.* only, below.
        scoped = scoped_rows_by_job.get(row["job_id"])
        fit_score = scoped["fit_score"] if scoped else row["fit_score"]
        priority = scoped["priority"] if scoped else row["priority"]
        matched_skills_json = scoped["matched_skills_json"] if scoped else row["matched_skills_json"]
        missing_skills_json = scoped["missing_skills_json"] if scoped else row["missing_skills_json"]
        skill_match_json = scoped["skill_match_json"] if scoped else row["skill_match_json"]
        resume_variant = (scoped["resume_variant"] if scoped else None) or row["candidate_resume_variant"] or row["resume_variant"]

        matched_skills = json.loads(matched_skills_json) if matched_skills_json else []
        missing_skills = json.loads(missing_skills_json) if missing_skills_json else []
        skill_match = json.loads(skill_match_json) if skill_match_json else {}
        hard_reject_reasons = skill_match.get("hard_reject_reasons", [])

        freshness_assessment = classify_freshness(row["posted_date"] or "")
        # THIS candidate's own lifecycle status (cjm.candidate_status)
        # is authoritative when set -- j.status is a single-candidate-
        # era GLOBAL column overwritten by whichever candidate most
        # recently had this job scored (see generate_run_report.py's
        # build_report_rows() for the same fix/rationale).
        job_status = str(row["candidate_status"] or row["status"] or "FOUND")

        results.append(
            {
                "job_id": row["job_id"],
                "source": row["source"],
                "title": row["title"],
                "company": row["company"],
                "location": row["location"],
                "job_url": row["job_url"],
                "application_url": row["application_url"],
                "score": fit_score,
                "priority": priority,
                "eligible": True,
                "eligibility": "ELIGIBLE",
                "eligibility_reasons": [],
                "freshness": freshness_assessment.category.value,
                "freshness_age_days": freshness_assessment.age_days,
                "experience_required": row["experience_required"],
                "requirement_type": detect_requirement_type(row["experience_required"] or ""),
                "status": job_status,
                "report_status": _persisted_report_status(priority, row["candidate_status"], job_status),
                "match_reason": _match_reason_from_persisted(matched_skills, missing_skills, hard_reject_reasons),
                "matched_skills": matched_skills,
                "missing_skills": missing_skills,
                # THIS search's own scoped resume (item 5 fix, see
                # scoped_rows_by_job above) is authoritative when
                # present -- falls back to the candidate-wide snapshot,
                # then the legacy global column, never fabricated.
                "resume_variant": resume_variant,
                "first_discovered": row["created_at"],
                "last_seen": row["last_updated"] or row["created_at"],
                "duplicate_suppressed": False,
                # This reduced-fidelity (no live-rescore) path never runs
                # cross-source dedup itself (see this function's own
                # docstring) -- honestly empty, never fabricated.
                "duplicate_candidates": [],
            }
        )

    return results


def _test_only_job_ids(conn, candidate_id):
    """
    Item 4/9 fix (real bug found live: the dashboard's "Jobs Found"/
    "Matching" aggregates previously read the ENTIRE global `jobs`
    table -- shared across every candidate/search/live-verification
    run ever -- not this candidate's own activity; on this project's
    own dev DB that showed 1555 "Jobs Found" for a candidate who had
    never run more than a handful of their own real searches).

    Returns the set of job_ids that are tracked ONLY through this
    candidate's TEST/SYSTEM-type searches, never a USER search -- these
    are excluded from dashboard aggregates. A job with NO
    candidate_job_search_matches row at all (a manual import, or a
    match that predates migrate_v9's tracking) is NEVER included here
    -- benefit of the doubt, not test, since manual import has no
    "test" concept and older data should not silently vanish from the
    dashboard just because it predates this fix.
    """
    try:
        all_tracked = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT job_id FROM candidate_job_search_matches WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchall()
        }
        user_search_tracked = {
            r[0] for r in conn.execute(
                """
                SELECT DISTINCT cjsm.job_id
                FROM candidate_job_search_matches cjsm
                JOIN saved_search_runs ssr ON ssr.search_run_id = cjsm.search_run_id
                JOIN saved_searches ss ON ss.saved_search_id = ssr.saved_search_id
                WHERE cjsm.candidate_id = ? AND ss.search_type = 'USER'
                """,
                (candidate_id,),
            ).fetchall()
        }
    except Exception as error:
        if "no such table" in str(error) or "no such column" in str(error):
            # A DB predating migrate_v9/migrate_v10 -- nothing to
            # exclude, same posture as every other defensive fallback
            # in this project for a pre-migration DB.
            return set()
        raise
    return all_tracked - user_search_tracked


def get_dashboard_summary(conn, candidate_id):
    """Aggregate counts for the dashboard -- reuses
    generate_run_report.py's existing build_report_rows()/
    build_source_health()/build_run_summary() exactly as the Excel
    report does; no second computation of jobs-found/matching/
    APPLY_TODAY/duplicate counts.

    Scoped to THIS candidate's own matched jobs (candidate_job_matches
    is already candidate-scoped), minus any job tracked only through a
    TEST/SYSTEM search of theirs (see _test_only_job_ids()) -- never
    the entire global `jobs` table, and never another candidate's or
    another search's activity.
    """
    profile = load_active_profile(conn, candidate_id)
    if profile.metadata.profile_status.value != "CONFIRMED":
        return None

    legacy_profile = to_legacy_matching_profile(profile)

    # Same test-isolation fix applied to the source-health aggregate:
    # only runs belonging to this candidate's OWN USER-type searches
    # (never a TEST search's run, e.g. this project's own live-
    # verification searches) feed the dashboard's per-source picture.
    try:
        user_run_ids = {
            r[0] for r in conn.execute(
                """
                SELECT DISTINCT ssr.search_run_id
                FROM saved_search_runs ssr
                JOIN saved_searches ss ON ss.saved_search_id = ssr.saved_search_id
                WHERE ss.candidate_id = ? AND ss.search_type = 'USER'
                """,
                (candidate_id,),
            ).fetchall()
        }
    except Exception as error:
        if "no such column" in str(error) or "no such table" in str(error):
            user_run_ids = None
        else:
            raise

    # Same fix as get_results_for_saved_search()/process_queue_item():
    # the dashboard aggregates across every one of this candidate's OWN
    # USER-type searches at once, so its role/location override is the
    # UNION of every one of those searches' own frozen query-plan
    # entries -- never the candidate profile's job_preferences target_
    # roles/locations alone, which are commonly empty. Without this,
    # the dashboard silently lost the same "Core role alignment"/
    # "Location" points every affected search's own results view
    # already correctly awards, making dashboard totals disagree with
    # the results page for the exact same jobs.
    if user_run_ids:
        placeholders = ",".join("?" for _ in user_run_ids)
        rows = conn.execute(
            f"SELECT query FROM search_runs WHERE search_run_id IN ({placeholders})",
            list(user_run_ids),
        ).fetchall()
        combined_entries = []
        for (query_json,) in rows:
            if not query_json:
                continue
            try:
                combined_entries.extend(json.loads(query_json).get("queries", []))
            except (json.JSONDecodeError, AttributeError):
                continue
        apply_search_target_override(legacy_profile, combined_entries)

    matches = report_mod.load_candidate_job_matches(conn, candidate_id)
    test_only_job_ids = _test_only_job_ids(conn, candidate_id)
    scoped_job_ids = set(matches.keys()) - test_only_job_ids

    jobs = report_mod.load_jobs(conn, exclude_test_mock=True)
    jobs = [j for j in jobs if j.get("job_id") in scoped_job_ids]

    report_rows, _dupes = report_mod.build_report_rows(jobs, legacy_profile, candidate_job_matches=matches)

    if user_run_ids is not None:
        source_health = report_mod.build_source_health(conn, candidate_id=candidate_id, run_ids_filter=user_run_ids)
    else:
        source_health = report_mod.build_source_health(conn, candidate_id=candidate_id)
    return report_mod.build_run_summary(report_rows, source_health)


def get_scoped_resume_overrides(conn, candidate_id, saved_search_id):
    """
    Item 5/10 fix: {job_id: {"resume_id", "resume_variant"}} sourced
    from candidate_job_search_matches, scoped to ONE saved search's own
    run_ids -- correct for resume_variant/resume_id, which must reflect
    THIS search's own scoring run, never whichever DIFFERENT search
    most recently re-matched the same job. Used by both the JSON
    results API (_apply_scoped_resume_overrides() below) and the
    per-search Excel export (api/main.py's download_search_report(),
    via generate_run_report.generate()'s resume_overrides_by_job
    param) -- one canonical scoped-override query, never two.
    """
    run_ids = list_run_ids_for_search(conn, saved_search_id)
    if not run_ids:
        return {}
    placeholders = ",".join("?" for _ in run_ids)
    try:
        rows = conn.execute(
            f"""
            SELECT job_id, resume_id, resume_variant FROM candidate_job_search_matches
            WHERE candidate_id = ? AND search_run_id IN ({placeholders})
            """,
            [candidate_id, *run_ids],
        ).fetchall()
    except Exception as error:
        if "no such table" in str(error):
            return {}
        raise
    return {job_id: {"resume_id": resume_id, "resume_variant": resume_variant} for job_id, resume_id, resume_variant in rows}


def _apply_scoped_resume_overrides(conn, candidate_id, saved_search_id, matches):
    """
    `matches` (from generate_run_report.load_candidate_job_matches())
    is the candidate-WIDE "latest snapshot" -- correct for
    candidate_status (global, unchanged here) but NOT for
    resume_variant/resume_id (see get_scoped_resume_overrides() above).
    Overrides resume_variant/resume_id IN PLACE on `matches` --
    candidate_status is never touched (that column doesn't exist on
    the scoped table at all, so there is nothing to accidentally
    override).
    """
    overrides = get_scoped_resume_overrides(conn, candidate_id, saved_search_id)
    for job_id, override in overrides.items():
        matches.setdefault(job_id, {})
        matches[job_id]["resume_id"] = override["resume_id"]
        matches[job_id]["resume_variant"] = override["resume_variant"]


def get_results_for_saved_search(conn, candidate_id, saved_search_id, pinned_profile_version=None):
    """
    Results already computed for this saved search's run(s).

    pinned_profile_version (item 6: search-run traceability -- see
    migrate_v8_resume_profile_traceability.py): when the caller passes
    this saved search's own profile_version pin, the live rescore below
    (if any) is run against THAT EXACT historical profile version,
    never whichever profile is currently active -- so a search pinned
    to an older resume/profile keeps representing it, even after the
    candidate uploads and confirms a newer one. None (the default)
    preserves the original behavior exactly: always the current active
    profile.

    If the resolved profile (pinned, or current active when unpinned)
    is CONFIRMED, behavior is UNCHANGED from before this fix: a live
    rescore of every job via generate_run_report.build_report_rows(),
    scoped down to this search's own matched job_ids.

    If the profile is DRAFT/ARCHIVED (or missing), NO live rescore is
    attempted -- to_legacy_matching_profile() is never called with a
    non-CONFIRMED profile, and a DRAFT profile is never silently
    promoted. Already-persisted candidate_job_matches rows for this
    search are returned directly instead (_load_persisted_results()).
    Only if there are none of those either does this raise
    ResultsNotReadyError -- never a 500, never fabricated results.
    """
    scoped_job_ids = _job_ids_for_search(conn, candidate_id, saved_search_id)

    try:
        if pinned_profile_version is not None:
            profile = load_profile_version(conn, candidate_id, pinned_profile_version)
        else:
            profile = load_active_profile(conn, candidate_id)
        is_confirmed = profile.metadata.profile_status.value == "CONFIRMED"
    except ProfileStoreError:
        profile = None
        is_confirmed = False

    if is_confirmed:
        legacy_profile = to_legacy_matching_profile(profile)
        apply_search_target_override(legacy_profile, _query_plan_entries_for_search(conn, saved_search_id))

        jobs = report_mod.load_jobs(conn, exclude_test_mock=True)
        matches = report_mod.load_candidate_job_matches(conn, candidate_id)
        _apply_scoped_resume_overrides(conn, candidate_id, saved_search_id, matches)

        report_rows, _duplicate_candidates = report_mod.build_report_rows(
            jobs, legacy_profile, candidate_job_matches=matches
        )

        return [
            _row_to_result_dict(row)
            for row in report_rows
            if row.ranking.job_id in scoped_job_ids
        ]

    if not scoped_job_ids:
        raise ResultsNotReadyError(
            "This candidate's profile is not CONFIRMED, and this search has no "
            "persisted results yet. Confirm the profile, then run the search, "
            "to see results."
        )

    return _load_persisted_results(conn, candidate_id, scoped_job_ids, saved_search_id=saved_search_id)
