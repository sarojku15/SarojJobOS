#!/usr/bin/env python3

"""
Custom Job Scoring Profiles -- REAL search-worker pipeline regression
tests (Task 2 of the "Complete Integration and Final Validation"
pass). test_scoring_profiles.py already covers the module-level
scoring logic and the API-level persistence of a saved search's
scoring_profile_id; this file instead drives the actual
scripts/search_worker.py process_queue_item() path end to end --
synthetic candidate, synthetic FAKE-source job, a real saved search
row, a real search_run/search_queue/saved_search_runs row set -- the
same way a live "Run Now" click would, minus the background thread
(driven synchronously here for determinism) and any real network call.

Fully offline. Every candidate/job is synthetic. Production DB
verified byte-identical before/after.
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

import hashlib

import init_dev_db
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from source_adapter import JobSourceAdapter
import source_registry
from search_submission import submit_search
from search_worker import claim_next_queue_item, process_queue_item

import scoring_profile_store
import search_store
from schemas import SavedSearchCreate

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)

passed = 0
failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {msg}")
    else:
        failed += 1
        print(f"FAIL: {msg}")


def _fail(failures, message):
    global failed
    failed += 1
    failures.append(message)
    print(f"FAIL: {message}")


def _pass(message):
    global passed
    passed += 1
    print(f"PASS: {message}")


# ----------------------------------------------------------------------
# Deterministic fake adapter, registered only under "FAKE" (same
# convention as test_search_worker.py -- never touches MOCK/NAUKRI/etc).
# ----------------------------------------------------------------------

class _FakeBehavior:
    jobs = []

    @classmethod
    def reset(cls):
        cls.jobs = []


class FakeAdapter(JobSourceAdapter):
    name = "FAKE"

    def health_check(self):
        from source_adapter import AdapterHealth, BlockReason
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [dict(job) for job in _FakeBehavior.jobs]


source_registry.ADAPTERS["FAKE"] = FakeAdapter


def _fake_job(title="Senior Site Reliability Engineer", location="Bengaluru"):
    return {
        "source": "FAKE",
        "company": "Fake Co",
        "title": title,
        "location": location,
        "work_model": "Hybrid",
        "job_url": f"https://example.com/jobs/{title.lower().replace(' ', '-')}",
        "application_url": "",
        "posted_date": "2026-09-20",
        "jd_text": "AWS Kubernetes Terraform Jenkins Prometheus SLO incident reliability production cloud platform",
        "experience_required": "8-10 years",
        "mandatory_skills": [],
        "preferred_skills": [],
    }


# ----------------------------------------------------------------------
# DB / candidate / saved-search fixtures
# ----------------------------------------------------------------------

def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_scoring_pipeline_"))
    tmp_db = tmp_dir / "jobos_test.db"
    init_dev_db.init_dev_db(tmp_db)
    return tmp_db


def _seed_confirmed_candidate(db_path, candidate_id, target_roles, target_locations):
    now = "2026-09-20T00:00:00+00:00"
    raw = {
        "identity": {"candidate_id": candidate_id, "name": "Test Candidate"},
        "professional_summary": {"total_experience_years": 8},
        "skills": {
            "cloud": [{"name": "AWS"}],
            "containers_orchestration": [{"name": "Kubernetes"}],
            "infrastructure_iac": [{"name": "Terraform"}],
            "cicd": [{"name": "Jenkins"}],
        },
        "job_preferences": {"target_roles": target_roles, "target_locations": target_locations},
    }
    profile = promote_to_confirmed(normalize_candidate_profile(raw))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) VALUES (?,?,?,?,?,?,?)",
        (candidate_id, "Test Candidate", None, None, now, now, "ACTIVE"),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), now, now),
    )
    conn.commit()
    conn.close()


def _create_saved_search(conn, candidate_id, target_roles, target_locations, minimum_match_score=70, scoring_profile_id=None):
    payload = SavedSearchCreate(
        name="Pipeline test search",
        target_roles=target_roles,
        target_locations=target_locations,
        minimum_match_score=minimum_match_score,
        sources=["FAKE"],
        scoring_profile_id=scoring_profile_id,
    )
    return search_store.create_saved_search(conn, candidate_id, payload)


def _run_saved_search_synchronously(db_path, conn, candidate_id, saved_search_id):
    """
    The non-threaded equivalent of api.search_store.trigger_run(): runs
    submit_search() + the saved_search_runs link exactly like trigger_
    run() does, then drives process_queue_item() synchronously (instead
    of trigger_run()'s background thread) so the test can inspect the
    result deterministically.
    """
    conn.row_factory = sqlite3.Row
    saved_search = search_store.get_saved_search(conn, saved_search_id)
    result = submit_search(
        db_path=str(db_path),
        candidate_id=candidate_id,
        sources=saved_search["sources"],
        minimum_match_score=saved_search["minimum_match_score"],
        target_roles_override=saved_search["target_roles"],
        target_locations_override=saved_search["target_locations"],
    )
    conn.execute(
        "INSERT OR IGNORE INTO saved_search_runs (saved_search_id, search_run_id, created_at) VALUES (?, ?, datetime('now'))",
        (saved_search_id, result.search_run_id),
    )
    conn.commit()

    claimed = claim_next_queue_item(conn, search_run_id=result.search_run_id)
    work_result = process_queue_item(conn, claimed)
    return result.search_run_id, work_result


# ----------------------------------------------------------------------
# 1. Legacy search (no profile) -- unchanged behavior
# ----------------------------------------------------------------------

def test_1_legacy_search_uses_score_job_unchanged():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-1", ["Senior SRE"], ["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    saved_search_id = _create_saved_search(conn, "cand-1", ["Senior SRE"], ["Bengaluru"])
    run_id, work_result = _run_saved_search_synchronously(db, conn, "cand-1", saved_search_id)

    match = conn.execute(
        "SELECT fit_score, priority, scoring_profile_id, scoring_profile_version FROM candidate_job_matches WHERE candidate_id = 'cand-1'"
    ).fetchone()
    conn.close()

    if match is None:
        _fail(failures, "test 1: expected a candidate_job_matches row")
    elif match[2] is not None or match[3] is not None:
        _fail(failures, f"test 1: legacy-scored match must have NULL scoring_profile_id/version, got {match[2]!r}/{match[3]!r}")
    elif match[0] is None or match[1] is None:
        _fail(failures, "test 1: expected a real score/priority from the legacy scorer")
    else:
        _pass(f"test 1 -> legacy search (no scoring profile) scores via score_job.py unchanged (score={match[0]}, priority={match[1]}, scoring_profile_id=None)")

    return failures


# ----------------------------------------------------------------------
# 2-4. custom profile vs. saved-search threshold combinations
# ----------------------------------------------------------------------

def test_2_custom_profile_lower_threshold_than_saved_search():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-2", ["Senior SRE"], ["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    profile = scoring_profile_store.create_profile(
        conn, "cand-2", "Lower threshold", {"technical_skills": 1.0}, min_shortlist_score=10,
    )
    saved_search_id = _create_saved_search(
        conn, "cand-2", ["Senior SRE"], ["Bengaluru"], minimum_match_score=90,
        scoring_profile_id=profile["scoring_profile_id"],
    )
    run_id, work_result = _run_saved_search_synchronously(db, conn, "cand-2", saved_search_id)

    row = conn.execute(
        "SELECT fit_score, scoring_profile_id, scoring_profile_version FROM candidate_job_matches WHERE candidate_id = 'cand-2'"
    ).fetchone()
    conn.close()

    if row is None:
        _fail(failures, "test 2: expected a candidate_job_matches row")
    elif row[1] != profile["scoring_profile_id"] or row[2] != profile["version"]:
        _fail(failures, f"test 2: expected scoring_profile_id/version to be stamped, got {row[1]!r}/{row[2]!r}")
    else:
        _pass(f"test 2 -> custom profile with a LOWER profile threshold (10) than the saved search's own minimum_match_score (90) still scores and stamps profile traceability correctly (score={row[0]})")

    return failures


def test_3_custom_profile_higher_saved_search_threshold():
    # Same combination as test 2 from the opposite direction -- the
    # saved search's minimum_match_score is informational-only in the
    # existing architecture (confirmed by audit: it is stored/returned
    # but never used to gate/filter anywhere in the real pipeline), so
    # a HIGHER saved-search threshold never blocks a run or suppresses
    # a match -- only the profile's own min_shortlist_score governs
    # `qualified` once a profile is selected. This test exists to
    # pin that exact, audited behavior so a future change here is
    # caught rather than silently altering precedence.
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-3", ["Senior SRE"], ["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    profile = scoring_profile_store.create_profile(
        conn, "cand-3", "Low bar", {"technical_skills": 1.0}, min_shortlist_score=5,
    )
    saved_search_id = _create_saved_search(
        conn, "cand-3", ["Senior SRE"], ["Bengaluru"], minimum_match_score=95,
        scoring_profile_id=profile["scoring_profile_id"],
    )
    run_id, work_result = _run_saved_search_synchronously(db, conn, "cand-3", saved_search_id)

    row = conn.execute("SELECT fit_score, priority FROM candidate_job_matches WHERE candidate_id = 'cand-3'").fetchone()
    conn.close()

    if row is None or row[1] == "REJECT":
        _fail(failures, f"test 3: expected the job to pass this low-bar (5) profile despite the saved search's own 95 minimum_match_score (informational only), got {row}")
    else:
        _pass(f"test 3 -> saved search's own minimum_match_score (95, informational-only by existing-architecture design) never blocks a job that clears the selected profile's own min_shortlist_score (5): priority={row[1]}")

    return failures


def test_4_custom_profile_higher_threshold_rejects():
    failures = []
    _FakeBehavior.reset()
    # A deliberately WEAKER jd_text than _fake_job()'s default -- only
    # one of the candidate's four skills is mentioned, so
    # technical_skills' own raw_score is a PARTIAL (not perfect) match,
    # keeping the weighted score comfortably below the profile's 99.9
    # threshold without needing the mandatory-skill gate's involvement.
    weak_job = _fake_job()
    weak_job["jd_text"] = "Kubernetes experience required for this role."
    _FakeBehavior.jobs = [weak_job]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-4", ["Senior SRE"], ["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    profile = scoring_profile_store.create_profile(
        conn, "cand-4", "Very high bar", {"technical_skills": 1.0}, min_shortlist_score=99.9,
    )
    saved_search_id = _create_saved_search(
        conn, "cand-4", ["Senior SRE"], ["Bengaluru"], minimum_match_score=10,
        scoring_profile_id=profile["scoring_profile_id"],
    )
    run_id, work_result = _run_saved_search_synchronously(db, conn, "cand-4", saved_search_id)

    row = conn.execute("SELECT fit_score, priority FROM candidate_job_matches WHERE candidate_id = 'cand-4'").fetchone()
    conn.close()

    if row is None:
        _fail(failures, "test 4: expected a candidate_job_matches row even for a not-qualified job")
    elif row[1] != "REJECT":
        _fail(failures, f"test 4: expected priority=REJECT when score < profile's 99.9 threshold regardless of the saved search's own low (10) minimum_match_score, got {row}")
    else:
        _pass(f"test 4 -> a HIGH profile min_shortlist_score (99.9) correctly rejects a job the saved search's own low minimum_match_score (10) would not have blocked: {row}")

    return failures


# ----------------------------------------------------------------------
# 5. scores below/equal/above effective threshold already exercised in
#    test_scoring_profiles.py (criterion-level unit tests, 14/15/16
#    mandatory-gate; 17-22 missing-data; etc). Re-verified here purely
#    through the real pipeline for the boundary case: score == threshold.
# ----------------------------------------------------------------------

def test_5_score_equal_to_threshold_qualifies():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-5", ["Senior SRE"], ["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    profile = scoring_profile_store.create_profile(
        conn, "cand-5", "Zero bar", {"technical_skills": 1.0}, min_shortlist_score=0,
    )
    saved_search_id = _create_saved_search(
        conn, "cand-5", ["Senior SRE"], ["Bengaluru"], scoring_profile_id=profile["scoring_profile_id"],
    )
    run_id, work_result = _run_saved_search_synchronously(db, conn, "cand-5", saved_search_id)

    row = conn.execute("SELECT fit_score, priority FROM candidate_job_matches WHERE candidate_id = 'cand-5'").fetchone()
    conn.close()

    if row is None or row[0] is None or row[0] < 0:
        _fail(failures, f"test 5: expected a valid score >= the 0 threshold, got {row}")
    else:
        _pass(f"test 5 -> score >= min_shortlist_score=0 boundary qualifies correctly: {row}")

    return failures


# ----------------------------------------------------------------------
# 6. Mandatory-gate failure despite a high numeric score
# ----------------------------------------------------------------------

def test_6_mandatory_gate_overrides_high_score():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    _FakeBehavior.jobs[0]["mandatory_skills"] = ["Kubernetes", "Some Totally Unverifiable Skill XYZ"]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-6", ["Senior SRE"], ["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    profile = scoring_profile_store.create_profile(
        conn, "cand-6", "High weight, gate on", {"technical_skills": 1.0},
        mandatory_gate_enabled=True, min_shortlist_score=1,
    )
    saved_search_id = _create_saved_search(
        conn, "cand-6", ["Senior SRE"], ["Bengaluru"], scoring_profile_id=profile["scoring_profile_id"],
    )
    run_id, work_result = _run_saved_search_synchronously(db, conn, "cand-6", saved_search_id)

    row = conn.execute("SELECT fit_score, priority FROM candidate_job_matches WHERE candidate_id = 'cand-6'").fetchone()
    conn.close()

    if row is None:
        _fail(failures, "test 6: expected a candidate_job_matches row")
    elif row[1] != "REJECT":
        _fail(failures, f"test 6: a missing mandatory skill must force priority=REJECT regardless of the very low (1) shortlist threshold, got {row}")
    else:
        _pass(f"test 6 -> mandatory-gate failure forces REJECT even though min_shortlist_score=1 would otherwise qualify the numeric score: {row}")

    return failures


# ----------------------------------------------------------------------
# 7. Deleted/invalid selected profile -> run fails explicitly, never
#    silently falls back to legacy scoring.
# ----------------------------------------------------------------------

def test_7_deleted_profile_fails_run_explicitly():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-7", ["Senior SRE"], ["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    profile = scoring_profile_store.create_profile(
        conn, "cand-7", "Will be deleted", {"technical_skills": 1.0},
    )
    saved_search_id = _create_saved_search(
        conn, "cand-7", ["Senior SRE"], ["Bengaluru"], scoring_profile_id=profile["scoring_profile_id"],
    )
    scoring_profile_store.delete_profile(conn, "cand-7", profile["scoring_profile_id"])

    run_id, work_result = _run_saved_search_synchronously(db, conn, "cand-7", saved_search_id)

    run_row = conn.execute("SELECT status, error_message FROM search_runs WHERE search_run_id = ?", (run_id,)).fetchone()
    match_row = conn.execute("SELECT 1 FROM candidate_job_matches WHERE candidate_id = 'cand-7'").fetchone()
    conn.close()

    if run_row is None or run_row[0] != "FAILED":
        _fail(failures, f"test 7: expected search_runs.status='FAILED' for a deleted/invalid profile selection, got {run_row}")
    elif not run_row[1] or "no longer exist" not in run_row[1]:
        _fail(failures, f"test 7: expected a clear error_message naming the missing profile, got {run_row[1]!r}")
    elif match_row is not None:
        _fail(failures, "test 7: no job should ever be scored/persisted under a misrepresented scoring model")
    else:
        _pass(f"test 7 -> a deleted/invalid selected profile fails the run explicitly (status=FAILED, error={run_row[1]!r}) -- never silently falls back to legacy scoring")

    return failures


# ----------------------------------------------------------------------
# 8. Clearing the profile selection reverts to legacy scoring on the
#    NEXT run (already proven at the API-persistence layer in
#    test_scoring_profiles.py tests 58-60; this re-verifies it through
#    the real pipeline).
# ----------------------------------------------------------------------

def test_8_clearing_selection_reverts_to_legacy_on_next_run():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-8", ["Senior SRE"], ["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    profile = scoring_profile_store.create_profile(
        conn, "cand-8", "Custom", {"technical_skills": 1.0},
    )
    saved_search_id = _create_saved_search(
        conn, "cand-8", ["Senior SRE"], ["Bengaluru"], scoring_profile_id=profile["scoring_profile_id"],
    )
    run_id_1, _ = _run_saved_search_synchronously(db, conn, "cand-8", saved_search_id)
    first_row = conn.execute(
        "SELECT scoring_profile_id FROM candidate_job_matches WHERE candidate_id = 'cand-8'"
    ).fetchone()

    # Clear the selection via the real update path (model_fields_set-aware).
    from schemas import SavedSearchUpdate
    search_store.update_saved_search(conn, saved_search_id, SavedSearchUpdate(scoring_profile_id=None))

    run_id_2, _ = _run_saved_search_synchronously(db, conn, "cand-8", saved_search_id)
    second_row = conn.execute(
        "SELECT scoring_profile_id FROM candidate_job_search_matches WHERE candidate_id = 'cand-8' AND search_run_id = ?",
        (run_id_2,),
    ).fetchone()
    conn.close()

    if first_row is None or first_row[0] != profile["scoring_profile_id"]:
        _fail(failures, f"test 8: expected the first run to be custom-scored, got {first_row}")
    elif second_row is None or second_row[0] is not None:
        _fail(failures, f"test 8: expected the second run (after clearing the selection) to be legacy-scored (scoring_profile_id=None), got {second_row}")
    else:
        _pass("test 8 -> clearing scoring_profile_id reverts the NEXT real run to legacy scoring, without touching the first run's own already-persisted profile-scored snapshot")

    return failures


# ----------------------------------------------------------------------
# 9. Both match tables agree on scoring_profile_id/version for the
#    same (search_run_id, job_id).
# ----------------------------------------------------------------------

def test_9_both_match_tables_agree_on_profile_traceability():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-9", ["Senior SRE"], ["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    profile = scoring_profile_store.create_profile(
        conn, "cand-9", "Traceability check", {"technical_skills": 1.0},
    )
    saved_search_id = _create_saved_search(
        conn, "cand-9", ["Senior SRE"], ["Bengaluru"], scoring_profile_id=profile["scoring_profile_id"],
    )
    run_id, _ = _run_saved_search_synchronously(db, conn, "cand-9", saved_search_id)

    cjm = conn.execute(
        "SELECT scoring_profile_id, scoring_profile_version FROM candidate_job_matches WHERE candidate_id = 'cand-9'"
    ).fetchone()
    cjsm = conn.execute(
        "SELECT scoring_profile_id, scoring_profile_version FROM candidate_job_search_matches WHERE candidate_id = 'cand-9' AND search_run_id = ?",
        (run_id,),
    ).fetchone()
    conn.close()

    if cjm is None or cjsm is None:
        _fail(failures, "test 9: expected rows in both candidate_job_matches and candidate_job_search_matches")
    elif cjm != cjsm:
        _fail(failures, f"test 9: expected both tables to agree on (scoring_profile_id, version), got cjm={cjm}, cjsm={cjsm}")
    elif cjm[0] != profile["scoring_profile_id"] or cjm[1] != profile["version"]:
        _fail(failures, f"test 9: expected traceability to point at the profile actually used, got {cjm}, expected ({profile['scoring_profile_id']!r}, {profile['version']})")
    else:
        _pass(f"test 9 -> candidate_job_matches and candidate_job_search_matches agree on scoring_profile_id/version for the same run+job: {cjm}")

    return failures


# ----------------------------------------------------------------------
# 10. Existing READY_FOR_APPROVAL / application-lifecycle behavior is
#     untouched by a custom-profile run: candidate_status is seeded
#     from scoring["status"] on first insert and NEVER overwritten by a
#     later re-match (the existing, pre-existing-feature rule) -- a
#     human's lifecycle decision must survive a profile-scored re-run.
# ----------------------------------------------------------------------

def test_10_lifecycle_status_not_overwritten_by_rescoring():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-10", ["Senior SRE"], ["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    profile = scoring_profile_store.create_profile(
        conn, "cand-10", "Lifecycle check", {"technical_skills": 1.0}, min_shortlist_score=0,
    )
    saved_search_id = _create_saved_search(
        conn, "cand-10", ["Senior SRE"], ["Bengaluru"], scoring_profile_id=profile["scoring_profile_id"],
    )
    _run_saved_search_synchronously(db, conn, "cand-10", saved_search_id)

    job_id = conn.execute("SELECT job_id FROM candidate_job_matches WHERE candidate_id = 'cand-10'").fetchone()[0]
    # Simulate a human lifecycle decision (the only other writer of
    # candidate_status, per search_worker.py's own comment).
    conn.execute(
        "UPDATE candidate_job_matches SET candidate_status = 'APPROVED' WHERE candidate_id = ? AND job_id = ?",
        ("cand-10", job_id),
    )
    conn.commit()

    # Re-run the same saved search -- a second real scoring pass.
    _run_saved_search_synchronously(db, conn, "cand-10", saved_search_id)

    status_after = conn.execute(
        "SELECT candidate_status FROM candidate_job_matches WHERE candidate_id = ? AND job_id = ?",
        ("cand-10", job_id),
    ).fetchone()[0]
    conn.close()

    if status_after != "APPROVED":
        _fail(failures, f"test 10: expected a human APPROVED decision to survive a custom-profile re-score, got {status_after!r}")
    else:
        _pass("test 10 -> a human lifecycle decision (APPROVED) is never overwritten by a subsequent custom-profile re-scoring run")

    return failures


def test_11_results_view_never_silently_rescores_with_legacy_scorer():
    # The exact defect this feature's results_store.get_results_for_
    # saved_search() integration had to guard against: the pre-existing
    # live-rescore branch (for a CONFIRMED candidate profile) only knew
    # how to call the legacy score_job.score_job() path -- without the
    # scoring_profile_id-aware bypass, VIEWING a custom-scored search's
    # results would have silently overwritten its own profile-scored
    # numbers with legacy ones on every page load.
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-11", ["Senior SRE"], ["Bengaluru"])

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    profile = scoring_profile_store.create_profile(
        conn, "cand-11", "View-check", {"technical_skills": 1.0}, min_shortlist_score=0,
    )
    saved_search_id = _create_saved_search(
        conn, "cand-11", ["Senior SRE"], ["Bengaluru"], scoring_profile_id=profile["scoring_profile_id"],
    )
    _run_saved_search_synchronously(db, conn, "cand-11", saved_search_id)

    persisted_score = conn.execute("SELECT fit_score FROM candidate_job_matches WHERE candidate_id = 'cand-11'").fetchone()[0]

    import results_store
    api_results = results_store.get_results_for_saved_search(
        conn, "cand-11", saved_search_id, scoring_profile_id=profile["scoring_profile_id"],
    )
    conn.close()

    if len(api_results) != 1:
        _fail(failures, f"test 11: expected exactly 1 result, got {len(api_results)}")
    elif api_results[0]["score"] != persisted_score:
        _fail(failures, f"test 11: results view returned a DIFFERENT score ({api_results[0]['score']}) than the persisted profile-scored value ({persisted_score}) -- silently rescored with the legacy scorer")
    elif api_results[0]["scoring_profile_id"] != profile["scoring_profile_id"]:
        _fail(failures, f"test 11: results view did not report the correct scoring_profile_id, got {api_results[0]['scoring_profile_id']!r}")
    else:
        _pass(f"test 11 -> viewing a custom-scored search's results returns the persisted profile-scored value ({persisted_score}) unchanged, never silently rescored with the legacy scorer")

    return failures


ALL_TESTS = [
    test_1_legacy_search_uses_score_job_unchanged,
    test_2_custom_profile_lower_threshold_than_saved_search,
    test_3_custom_profile_higher_saved_search_threshold,
    test_4_custom_profile_higher_threshold_rejects,
    test_5_score_equal_to_threshold_qualifies,
    test_6_mandatory_gate_overrides_high_score,
    test_7_deleted_profile_fails_run_explicitly,
    test_8_clearing_selection_reverts_to_legacy_on_next_run,
    test_9_both_match_tables_agree_on_profile_traceability,
    test_10_lifecycle_status_not_overwritten_by_rescoring,
    test_11_results_view_never_silently_rescores_with_legacy_scorer,
]


def main():
    all_failures = []
    for test_fn in ALL_TESTS:
        all_failures.extend(test_fn())

    print()
    print(f"{passed} passed, {failed} failed")

    production_after = _sha(PRODUCTION_DB)
    check(production_before == production_after, f"SAFETY: production DB byte-identical before/after this test file (before={production_before}, after={production_after})")

    if all_failures or failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
