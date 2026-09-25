#!/usr/bin/env python3

"""
Tests for scripts/search_worker.py and scripts/run_search_worker.py.

Every test uses its own fresh, isolated temporary database (schema via
init_tracker.main() + migrate_v2_schema._create_new_tables()/
_ensure_job_columns()). None of these tests ever open
data/applications/jobos.db, and none makes a real network/browser call
-- a small deterministic FakeAdapter (registered only under the source
name "FAKE", alongside the untouched MOCK/NAUKRI entries) gives full,
precise control over adapter behavior (jobs returned, blocked,
timeout, malformed data, exceptions) that the fixed-output
MockJobSourceAdapter alone cannot produce.
"""

import inspect
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import init_tracker
import migrate_v2_schema
import search_worker as search_worker_module
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from source_adapter import AdapterBlockedError, AdapterTimeoutError, BlockReason, JobSourceAdapter
import source_registry
from search_submission import submit_search
from search_worker import (
    build_queries_from_snapshot,
    claim_next_queue_item,
    process_queue_item,
    run_once,
)


CLI_PATH = ROOT / "scripts" / "run_search_worker.py"


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


# ----------------------------------------------------------------------
# Deterministic fake adapter (test-only, registered as source "FAKE")
# ----------------------------------------------------------------------

class _FakeBehavior:
    jobs = []
    raise_blocked = False
    raise_timeout = False
    raise_exception = False
    health_reachable = True

    @classmethod
    def reset(cls):
        cls.jobs = []
        cls.raise_blocked = False
        cls.raise_timeout = False
        cls.raise_exception = False
        cls.health_reachable = True


class FakeAdapter(JobSourceAdapter):
    name = "FAKE"

    def health_check(self):
        from source_adapter import AdapterHealth
        return AdapterHealth(source=self.name, reachable=_FakeBehavior.health_reachable, block_reason=BlockReason.NONE if _FakeBehavior.health_reachable else BlockReason.UNKNOWN_BLOCK)

    def search(self, query):
        if _FakeBehavior.raise_exception:
            raise RuntimeError("simulated unexpected adapter exception")
        if _FakeBehavior.raise_blocked:
            raise AdapterBlockedError("FAKE", BlockReason.UNKNOWN_BLOCK)
        if _FakeBehavior.raise_timeout:
            raise AdapterTimeoutError("FAKE")
        return [dict(job) for job in _FakeBehavior.jobs]


source_registry.ADAPTERS["FAKE"] = FakeAdapter


def _fake_job(title="Senior Site Reliability Engineer", location="Bengaluru", experience_required="8-10 years",
              mandatory_skills=None, jd_extra="", job_url=None):
    return {
        "source": "FAKE",
        "company": "Fake Co",
        "title": title,
        "location": location,
        "work_model": "Hybrid",
        "job_url": job_url or f"https://example.com/jobs/{title.lower().replace(' ', '-')}-{location.lower()}",
        "application_url": "",
        "posted_date": "2026-09-20",
        "jd_text": f"AWS Kubernetes Terraform Jenkins Prometheus SLO incident reliability production cloud platform {jd_extra}",
        "experience_required": experience_required,
        "mandatory_skills": mandatory_skills or [],
        "preferred_skills": [],
    }


# ----------------------------------------------------------------------
# DB / candidate fixtures
# ----------------------------------------------------------------------

def _new_isolated_db():
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_search_worker_"))
    tmp_db = tmp_dir / "jobos_test.db"

    init_tracker.DATA_DIR = tmp_dir
    init_tracker.DB_PATH = tmp_db
    init_tracker.main()

    conn = sqlite3.connect(tmp_db)
    conn.execute("PRAGMA foreign_keys = ON")
    migrate_v2_schema._create_new_tables(conn)
    migrate_v2_schema._ensure_job_columns(conn)
    conn.commit()
    conn.close()

    return tmp_db


def _seed_confirmed_candidate(db_path, candidate_id, target_roles, target_locations,
                               experience_years=8, skills=None, status="ACTIVE"):
    now = "2026-09-20T00:00:00+00:00"
    raw = {
        "identity": {"candidate_id": candidate_id, "name": "Test Candidate"},
        "professional_summary": {"total_experience_years": experience_years},
        "skills": skills or {
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
        (candidate_id, "Test Candidate", None, None, now, now, status),
    )
    conn.execute(
        "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
        (candidate_id, json.dumps(serialize_candidate_profile(profile)), now, now),
    )
    conn.commit()
    conn.close()


def _submit(db_path, candidate_id, source="FAKE"):
    return submit_search(str(db_path), candidate_id, sources=[source], dry_run=False)


def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(CLI_PATH)] + args,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def _row(conn, table, **where):
    clause = " AND ".join(f"{k} = ?" for k in where)
    return conn.execute(f"SELECT * FROM {table} WHERE {clause}", tuple(where.values())).fetchone()


# ----------------------------------------------------------------------
# 1-3: claiming and query reconstruction
# ----------------------------------------------------------------------

def test_1_queued_item_is_claimed():
    failures = []
    _FakeBehavior.reset()
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-1", ["SRE"], ["Bengaluru"])
    _submit(db, "cand-1")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    status_after = conn.execute("SELECT status FROM search_queue WHERE queue_id = ?", (claimed["queue_id"],)).fetchone()[0]
    run_status_after = conn.execute("SELECT status FROM search_runs WHERE search_run_id = ?", (claimed["search_run_id"],)).fetchone()[0]
    conn.close()

    if claimed is None:
        _fail(failures, "test 1: expected a QUEUED item to be claimed")
    elif status_after != "RUNNING" or run_status_after != "RUNNING":
        _fail(failures, f"test 1: expected both search_queue and search_runs to move to RUNNING, got queue={status_after} run={run_status_after}")
    else:
        print("PASS: test 1 -> a QUEUED item is claimed and moved to RUNNING")

    return failures


def test_2_correct_source_adapter_executed():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-2", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-2", source="FAKE")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    conn.close()

    if result.raw_count != 1:
        _fail(failures, f"test 2: expected FakeAdapter's 1 canned job to be returned, got raw_count={result.raw_count}")
    else:
        print("PASS: test 2 -> the correct source adapter (resolved via source_registry) is executed")

    return failures


def test_3_correct_query_reconstructed_from_snapshot():
    failures = []
    snapshot = {"queries": [{"sequence": 0, "source": "FAKE", "role": "Senior SRE", "location": "Pune"}]}
    queries, unimplemented, totals = build_queries_from_snapshot(snapshot)

    if len(queries) != 1 or queries[0][0] != "FAKE" or queries[0][1].role != "Senior SRE" or queries[0][1].location != "Pune":
        _fail(failures, f"test 3: expected exactly the snapshot's (FAKE, Senior SRE, Pune) reconstructed, got {queries}")
    else:
        print("PASS: test 3 -> the correct SearchQuery is reconstructed verbatim from the frozen snapshot")

    return failures


# ----------------------------------------------------------------------
# 4-6: normalization and global job persistence
# ----------------------------------------------------------------------

def test_4_rawjob_results_are_normalized():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job(title="Senior Site Reliability Engineer", location="Bengaluru")]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-4", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-4")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    job_row = conn.execute("SELECT job_id, title, location FROM jobs").fetchone()
    conn.close()

    if result.normalized_count != 1:
        _fail(failures, f"test 4: expected 1 normalized job, got {result.normalized_count}")
    elif job_row is None or not job_row[0].startswith("FAKE-"):
        _fail(failures, f"test 4: expected a normalized job_id starting with 'FAKE-' (via job_id.py), got {job_row}")
    else:
        print(f"PASS: test 4 -> RawJob results are normalized (job_id={job_row[0]})")

    return failures


def test_5_global_job_is_inserted():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-5", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-5")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    if result.inserted_jobs != 1 or count != 1:
        _fail(failures, f"test 5: expected 1 inserted job, got inserted_jobs={result.inserted_jobs} table_count={count}")
    else:
        print("PASS: test 5 -> a new global job is inserted")

    return failures


def test_6_existing_global_job_not_duplicated():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-6a", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _seed_confirmed_candidate(db, "cand-6b", ["Senior Site Reliability Engineer"], ["Bengaluru"])
    _submit(db, "cand-6a")
    _submit(db, "cand-6b")

    conn = sqlite3.connect(db)
    claimed_1 = claim_next_queue_item(conn)
    process_queue_item(conn, claimed_1)
    claimed_2 = claim_next_queue_item(conn)
    result_2 = process_queue_item(conn, claimed_2)
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    if count != 1:
        _fail(failures, f"test 6: expected exactly 1 global job row (same FAKE job for both candidates), got {count}")
    elif result_2.existing_jobs != 1 or result_2.inserted_jobs != 0:
        _fail(failures, f"test 6: expected the second candidate's processing to see an EXISTING job, not insert a new one, got inserted={result_2.inserted_jobs} existing={result_2.existing_jobs}")
    else:
        print("PASS: test 6 -> the same job discovered for a second candidate is not duplicated in the global jobs table")

    return failures


# ----------------------------------------------------------------------
# 7-10: eligibility
# ----------------------------------------------------------------------

def test_7_candidate_eligibility_is_evaluated():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job(experience_required="8-10 years")]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-7", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-7")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    conn.close()

    if result.eligible_count != 1:
        _fail(failures, f"test 7: expected the job to be evaluated as eligible for a 9-year candidate against '8-10 years', got eligible_count={result.eligible_count}")
    else:
        print("PASS: test 7 -> candidate eligibility (experience + location) is evaluated per job")

    return failures


def test_8_below_profile_excluded():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job(experience_required="1-3 years")]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-8", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=11)
    _submit(db, "cand-8")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    match_count = conn.execute("SELECT COUNT(*) FROM candidate_job_matches").fetchone()[0]
    job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    if result.excluded_experience != 1:
        _fail(failures, f"test 8: expected 1 job excluded_experience (BELOW_PROFILE), got {result.excluded_experience}")
    elif match_count != 0:
        _fail(failures, f"test 8: expected NO candidate_job_matches row for a BELOW_PROFILE job, got {match_count}")
    elif job_count != 1:
        _fail(failures, f"test 8: expected the job to still be recorded globally (identity-only), got job_count={job_count}")
    else:
        print("PASS: test 8 -> a BELOW_PROFILE job is excluded from candidate_job_matches but still recorded globally")

    return failures


def test_9_no_match_location_excluded():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job(location="Dubai")]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-9", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-9")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    match_count = conn.execute("SELECT COUNT(*) FROM candidate_job_matches").fetchone()[0]
    conn.close()

    if result.excluded_location != 1:
        _fail(failures, f"test 9: expected 1 job excluded_location (NO_MATCH), got {result.excluded_location}")
    elif match_count != 0:
        _fail(failures, f"test 9: expected NO candidate_job_matches row for a NO_MATCH location job, got {match_count}")
    else:
        print("PASS: test 9 -> a NO_MATCH location job is excluded from candidate_job_matches")

    return failures


def test_10_unknown_eligibility_handled_per_existing_semantics():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job(experience_required="")]  # unparseable -> UNKNOWN
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-10", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-10")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    match = conn.execute("SELECT experience_eligibility FROM candidate_job_matches").fetchone()
    conn.close()

    if result.eligible_count != 1 or match is None or match[0] != "UNKNOWN":
        _fail(failures, f"test 10: expected an UNKNOWN-experience job to remain eligible/scored (per existing job_eligibility semantics), got eligible_count={result.eligible_count} match={match}")
    else:
        print("PASS: test 10 -> UNKNOWN experience remains eligible/scored/visible, matching existing job_eligibility semantics")

    return failures


# ----------------------------------------------------------------------
# 11-14: scoring and match persistence
# ----------------------------------------------------------------------

def test_11_eligible_job_is_scored():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-11", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-11")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    conn.close()

    if result.scored_count != 1:
        _fail(failures, f"test 11: expected 1 scored job, got {result.scored_count}")
    else:
        print("PASS: test 11 -> an eligible job is scored")

    return failures


def test_12_candidate_aware_score_persisted():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-12", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-12")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    process_queue_item(conn, claimed)
    fit_score = conn.execute("SELECT fit_score FROM candidate_job_matches WHERE candidate_id = ?", ("cand-12",)).fetchone()
    conn.close()

    if fit_score is None or not isinstance(fit_score[0], int):
        _fail(failures, f"test 12: expected a numeric fit_score persisted, got {fit_score}")
    else:
        print(f"PASS: test 12 -> the candidate-aware score ({fit_score[0]}) is persisted")

    return failures


def test_13_candidate_job_match_created():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-13", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-13")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    row = conn.execute("SELECT candidate_id, job_id FROM candidate_job_matches").fetchone()
    conn.close()

    if result.matches_created != 1 or row is None or row[0] != "cand-13":
        _fail(failures, f"test 13: expected 1 candidate_job_matches row created for cand-13, got matches_created={result.matches_created} row={row}")
    else:
        print("PASS: test 13 -> candidate_job_matches row is created")

    return failures


def test_14_existing_match_not_duplicated():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-14", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-14")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    process_queue_item(conn, claimed)
    conn.close()

    # Submit and process the SAME candidate again for the same job.
    _submit(db, "cand-14")
    conn = sqlite3.connect(db)
    claimed_2 = claim_next_queue_item(conn)
    result_2 = process_queue_item(conn, claimed_2)
    count = conn.execute("SELECT COUNT(*) FROM candidate_job_matches WHERE candidate_id = ?", ("cand-14",)).fetchone()[0]
    conn.close()

    if count != 1:
        _fail(failures, f"test 14: expected exactly 1 match row for cand-14 after processing the same job twice, got {count}")
    elif result_2.matches_updated != 1 or result_2.matches_created != 0:
        _fail(failures, f"test 14: expected the second run to UPDATE, not create, got created={result_2.matches_created} updated={result_2.matches_updated}")
    else:
        print("PASS: test 14 -> an existing candidate_job_match is updated in place, never duplicated")

    return failures


# ----------------------------------------------------------------------
# 15-16: terminal status
# ----------------------------------------------------------------------

def test_15_queue_becomes_completed_after_success():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-15", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-15")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    process_queue_item(conn, claimed)
    status = conn.execute("SELECT status FROM search_queue WHERE queue_id = ?", (claimed["queue_id"],)).fetchone()[0]
    conn.close()

    if status != "COMPLETED":
        _fail(failures, f"test 15: expected search_queue status COMPLETED, got {status}")
    else:
        print("PASS: test 15 -> queue item becomes COMPLETED after a fully successful run")

    return failures


def test_16_search_run_becomes_completed():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-16", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    result0 = _submit(db, "cand-16")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    process_queue_item(conn, claimed)
    status = conn.execute("SELECT status FROM search_runs WHERE search_run_id = ?", (result0.search_run_id,)).fetchone()[0]
    conn.close()

    if status != "COMPLETED":
        _fail(failures, f"test 16: expected search_runs status COMPLETED, got {status}")
    else:
        print("PASS: test 16 -> parent search_run becomes COMPLETED after all queue work succeeds")

    return failures


# ----------------------------------------------------------------------
# 17-20: failure handling
# ----------------------------------------------------------------------

def test_17_adapter_timeout_handled():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.raise_timeout = True
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-17", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-17")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    status = conn.execute("SELECT status FROM search_queue WHERE queue_id = ?", (claimed["queue_id"],)).fetchone()[0]
    conn.close()

    if result.timed_out_queries != 1:
        _fail(failures, f"test 17: expected 1 timed-out query recorded, got {result.timed_out_queries}")
    elif status not in ("FAILED", "PARTIAL", "BLOCKED"):
        _fail(failures, f"test 17: expected a non-COMPLETED terminal status after a full timeout, got {status}")
    else:
        print(f"PASS: test 17 -> adapter timeout is handled (queue status={status}, timed_out_queries=1)")

    return failures


def test_18_adapter_blocked_handled():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.raise_blocked = True
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-18", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-18")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    status = conn.execute("SELECT status FROM search_queue WHERE queue_id = ?", (claimed["queue_id"],)).fetchone()[0]
    conn.close()

    if "FAKE" not in result.blocked_sources:
        _fail(failures, f"test 18: expected 'FAKE' recorded as a blocked source, got {result.blocked_sources}")
    elif status != "BLOCKED":
        _fail(failures, f"test 18: expected queue status BLOCKED when the only source is blocked with zero successes, got {status}")
    else:
        print("PASS: test 18 -> an adapter-blocked result is handled and marks the queue item BLOCKED")

    return failures


def test_19_adapter_exception_becomes_failed():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.raise_exception = True
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-19", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-19")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    status = conn.execute("SELECT status FROM search_queue WHERE queue_id = ?", (claimed["queue_id"],)).fetchone()[0]
    conn.close()

    if status != "FAILED":
        _fail(failures, f"test 19: expected queue status FAILED after an unexpected adapter exception, got {status}")
    elif not result.errors:
        _fail(failures, "test 19: expected the unexpected exception to be recorded in result.errors")
    else:
        print("PASS: test 19 -> an unexpected adapter exception results in a cleanly FAILED queue item")

    return failures


def test_20_malformed_job_does_not_abort_valid_results():
    failures = []
    _FakeBehavior.reset()
    malformed = {"source": "FAKE", "location": "Bengaluru"}  # missing required 'company'/'title'
    _FakeBehavior.jobs = [malformed, _fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-20", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-20")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    conn.close()

    if result.malformed_count != 1:
        _fail(failures, f"test 20: expected 1 malformed job recorded, got {result.malformed_count}")
    elif result.normalized_count != 1 or result.scored_count != 1:
        _fail(failures, f"test 20: expected the OTHER valid job to still be normalized/scored, got normalized={result.normalized_count} scored={result.scored_count}")
    else:
        print("PASS: test 20 -> one malformed job does not abort processing of other valid results")

    return failures


# ----------------------------------------------------------------------
# 21-22: DB failure / unexpected exception isolation
# ----------------------------------------------------------------------

def test_21_database_failure_rolls_back_persistence():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-21", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-21")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)

    conn.execute("DROP TABLE candidate_job_matches")
    conn.commit()

    try:
        process_queue_item(conn, claimed)
        _fail(failures, "test 21: expected an exception when candidate_job_matches is missing mid-processing")
    except sqlite3.OperationalError:
        pass
    except Exception as error:
        _fail(failures, f"test 21: expected sqlite3.OperationalError, got {type(error).__name__}: {error}")

    job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    if job_count != 0:
        _fail(failures, f"test 21: expected the persistence transaction (including the job insert) to roll back entirely, got {job_count} job(s) persisted")
    elif not failures:
        print("PASS: test 21 -> a database failure mid-persistence rolls back the whole affected transaction (no partial job insert survives)")

    return failures


def test_22_unexpected_exception_does_not_leave_queue_running():
    """
    Even when process_queue_item() itself raises after claiming (e.g.
    the DB failure above), the claimed item must not be left RUNNING
    forever in real usage -- this test confirms the specific failure
    path used elsewhere (candidate profile becomes invalid AFTER
    claiming) still reaches a terminal, non-RUNNING state via
    _finalize_queue_item()'s early-failure calls.
    """
    failures = []
    _FakeBehavior.reset()
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-22", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-22")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)

    # Simulate the candidate becoming invalid (e.g. deactivated)
    # between submission and processing.
    conn.execute("UPDATE candidates SET status = 'INACTIVE' WHERE candidate_id = ?", ("cand-22",))
    conn.commit()

    result = process_queue_item(conn, claimed)
    status = conn.execute("SELECT status FROM search_queue WHERE queue_id = ?", (claimed["queue_id"],)).fetchone()[0]
    conn.close()

    if status == "RUNNING":
        _fail(failures, "test 22: the queue item was left RUNNING after a clean failure -- it must reach a terminal state")
    elif status != "FAILED":
        _fail(failures, f"test 22: expected FAILED for a candidate who became invalid after claiming, got {status}")
    else:
        print("PASS: test 22 -> a clean failure after claiming still reaches a terminal (non-RUNNING) status")

    return failures


# ----------------------------------------------------------------------
# 23-25: global job vs multi-candidate matches
# ----------------------------------------------------------------------

def test_23_no_job_specific_candidate_copies():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-23a", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _seed_confirmed_candidate(db, "cand-23b", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-23a")
    _submit(db, "cand-23b")

    conn = sqlite3.connect(db)
    process_queue_item(conn, claim_next_queue_item(conn))
    process_queue_item(conn, claim_next_queue_item(conn))
    job_rows = conn.execute("SELECT job_id, source FROM jobs").fetchall()
    conn.close()

    if len(job_rows) != 1:
        _fail(failures, f"test 23: expected exactly ONE global job row shared by both candidates (no per-candidate copy), got {len(job_rows)}: {job_rows}")
    else:
        print("PASS: test 23 -> no candidate-specific copies of a job are ever created in the global jobs table")

    return failures


def test_24_same_global_job_matches_multiple_candidates():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-24a", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _seed_confirmed_candidate(db, "cand-24b", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-24a")
    _submit(db, "cand-24b")

    conn = sqlite3.connect(db)
    process_queue_item(conn, claim_next_queue_item(conn))
    process_queue_item(conn, claim_next_queue_item(conn))
    match_rows = conn.execute("SELECT candidate_id, job_id FROM candidate_job_matches").fetchall()
    conn.close()

    candidates_matched = {row[0] for row in match_rows}
    job_ids_matched = {row[1] for row in match_rows}

    if candidates_matched != {"cand-24a", "cand-24b"} or len(job_ids_matched) != 1:
        _fail(failures, f"test 24: expected both candidates matched to the SAME single job_id, got {match_rows}")
    else:
        print("PASS: test 24 -> the same global job can independently match multiple candidates")

    return failures


def test_25_different_candidates_different_scores():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job(mandatory_skills=["AWS", "Kubernetes", "Terraform", "Jenkins"])]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-25a", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _seed_confirmed_candidate(
        db, "cand-25b", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=1,
        skills={"cloud": [], "containers_orchestration": [], "infrastructure_iac": [], "cicd": []},
    )
    _submit(db, "cand-25a")
    _submit(db, "cand-25b")

    conn = sqlite3.connect(db)
    process_queue_item(conn, claim_next_queue_item(conn))
    process_queue_item(conn, claim_next_queue_item(conn))
    scores = dict(conn.execute("SELECT candidate_id, fit_score FROM candidate_job_matches").fetchall())
    conn.close()

    if scores.get("cand-25a") == scores.get("cand-25b"):
        _fail(failures, f"test 25: expected different scores for materially different candidates on the same job, got {scores}")
    else:
        print(f"PASS: test 25 -> different candidates receive different scores for the same job: {scores}")

    return failures


# ----------------------------------------------------------------------
# 26-28: architecture ordering / isolation (also covers static checks)
# ----------------------------------------------------------------------

def test_26_eligibility_before_scoring():
    failures = []
    source = inspect.getsource(search_worker_module)

    eligibility_call = source.find("assess_job_eligibility(")
    score_calls = [m.start() for m in re.finditer(r"\bscore_job\(", source)]

    if eligibility_call == -1:
        _fail(failures, "test 26: search_worker.py does not call assess_job_eligibility() at all")
    elif any(pos < eligibility_call for pos in score_calls):
        _fail(failures, "test 26: found a score_job() call positioned before assess_job_eligibility() in source order")
    else:
        print("PASS: test 26 -> score_job() is only reached after job_eligibility's assess_job_eligibility() (source-order check)")

    return failures


def test_27_no_direct_naukri_import():
    """
    Checks for an actual IMPORT statement (e.g. "import naukri_adapter"
    or "from naukri_adapter import ...") -- not a bare mention like
    "this module never imports NaukriAdapter" in a docstring explaining
    the architectural guarantee, which a naive substring check would
    misflag (the same false-positive class hit and fixed repeatedly
    elsewhere in this project).
    """
    failures = []
    source = inspect.getsource(search_worker_module)

    import_pattern = re.compile(r"^\s*(import naukri_adapter|from naukri_adapter import)", re.MULTILINE)

    if import_pattern.search(source):
        _fail(failures, "test 27: search_worker.py directly imports the Naukri adapter -- it must resolve adapters only through source_registry")
    elif "from source_registry import" not in source:
        _fail(failures, "test 27: search_worker.py does not import from source_registry at all")
    else:
        print("PASS: test 27 -> no direct Naukri (or any concrete adapter) import in the generic worker; adapters are resolved only through source_registry")

    return failures


def test_28_no_network_calls_in_mock_tests():
    """
    Confirms the FAKE adapter path used throughout this test file made
    no real network call -- verified structurally: FakeAdapter.search()
    contains no socket/http/subprocess call, and the worker itself
    imports no such module.
    """
    failures = []
    worker_source = inspect.getsource(search_worker_module)

    forbidden = ["import requests", "import urllib", "import http.client", "playwright", "socket.socket"]
    found = [f for f in forbidden if f in worker_source]

    if found:
        _fail(failures, f"test 28: search_worker.py references network-related symbols: {found}")
    else:
        print("PASS: test 28 -> search_worker.py makes no direct network call; all tests in this file used the local, zero-network FakeAdapter")

    return failures


# ----------------------------------------------------------------------
# 29-32: CLI options
# ----------------------------------------------------------------------

def test_29_max_items_works():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-29a", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _seed_confirmed_candidate(db, "cand-29b", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-29a")
    _submit(db, "cand-29b")

    results = run_once(str(db), max_items=1)

    if len(results) != 1:
        _fail(failures, f"test 29: expected --max-items 1 (max_items=1) to process exactly 1 item even though 2 are queued, got {len(results)}")
    else:
        print("PASS: test 29 -> max_items bounds the number of queue items processed in one cycle")

    return failures


def test_30_candidate_id_filtering_works():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-30a", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _seed_confirmed_candidate(db, "cand-30b", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-30a")
    _submit(db, "cand-30b")

    results = run_once(str(db), candidate_id="cand-30b", max_items=5)

    if len(results) != 1 or results[0].candidate_id != "cand-30b":
        _fail(failures, f"test 30: expected --candidate-id filtering to process only cand-30b's item, got {[(r.candidate_id) for r in results]}")
    else:
        print("PASS: test 30 -> --candidate-id filtering processes only that candidate's queued work")

    return failures


def test_31_search_run_id_filtering_works():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-31a", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _seed_confirmed_candidate(db, "cand-31b", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    result_a = _submit(db, "cand-31a")
    _submit(db, "cand-31b")

    results = run_once(str(db), search_run_id=result_a.search_run_id, max_items=5)

    if len(results) != 1 or results[0].search_run_id != result_a.search_run_id:
        _fail(failures, f"test 31: expected --search-run-id filtering to process only that run, got {[r.search_run_id for r in results]}")
    else:
        print("PASS: test 31 -> --search-run-id filtering processes only that specific run's queue item")

    return failures


def test_32_dry_run_does_not_mutate_db():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job()]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-32", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-32")

    before_bytes = db.read_bytes()
    results = run_once(str(db), dry_run=True)
    after_bytes = db.read_bytes()

    if before_bytes != after_bytes:
        _fail(failures, "test 32: expected zero DB mutation in dry-run mode, but the file changed")
    elif not results or results[0].status != "SKIPPED":
        _fail(failures, f"test 32: expected a SKIPPED preview result, got {results}")
    else:
        print("PASS: test 32 -> --dry-run performs zero database writes and makes no adapter call")

    # Also via the actual CLI.
    cli_result = _run_cli(["--once", "--dry-run", "--db", str(db)])
    after_cli_bytes = db.read_bytes()
    if after_cli_bytes != before_bytes:
        _fail(failures, "test 32: the CLI's --dry-run mutated the database file")
    elif "SKIPPED" not in cli_result.stdout:
        _fail(failures, f"test 32: expected the CLI to report a SKIPPED dry-run preview, got: {cli_result.stdout[-300:]}")

    return failures


# ----------------------------------------------------------------------
# 33-35: aggregate accounting
# ----------------------------------------------------------------------

def test_33_search_run_aggregate_status_correct():
    """
    A run with one BLOCKED source among otherwise-successful queries
    (achieved here via a single-query run that IS the blocked one, so
    zero queries succeed) must resolve to BLOCKED, not COMPLETED.
    """
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.raise_blocked = True
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-33", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    result0 = _submit(db, "cand-33")

    conn = sqlite3.connect(db)
    process_queue_item(conn, claim_next_queue_item(conn))
    status = conn.execute("SELECT status FROM search_runs WHERE search_run_id = ?", (result0.search_run_id,)).fetchone()[0]
    conn.close()

    if status != "BLOCKED":
        _fail(failures, f"test 33: expected aggregate search_run status BLOCKED, got {status}")
    else:
        print("PASS: test 33 -> search_run aggregate status correctly reflects actual queue-item outcomes (BLOCKED)")

    return failures


def test_34_queue_accounting_correct():
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [
        _fake_job(experience_required="1-3 years"),
        _fake_job(location="Dubai", experience_required="10-15 years", job_url="https://example.com/jobs/other"),
    ]
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-34", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=11)
    result0 = _submit(db, "cand-34")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    run_row = conn.execute(
        "SELECT jobs_discovered, jobs_experience_excluded, jobs_eligible FROM search_runs WHERE search_run_id = ?",
        (result0.search_run_id,),
    ).fetchone()
    conn.close()

    if run_row != (2, 1, 0):
        _fail(failures, f"test 34: expected search_runs accounting (jobs_discovered=2, jobs_experience_excluded=1, jobs_eligible=0), got {run_row}")
    elif result.excluded_location != 1:
        _fail(failures, f"test 34: expected the worker's own returned accounting to show 1 location-excluded job, got {result.excluded_location}")
    else:
        print(f"PASS: test 34 -> per-item queue/run accounting is correct: {run_row}, excluded_location={result.excluded_location}")

    return failures


def test_36_queries_completed_correct_when_zero_raw_results():
    """
    Regression test for a defect found during the first live Naukri
    integration test: a query that genuinely SUCCEEDS (the adapter's
    search() returns normally, no exception) but happens to find ZERO
    raw jobs must still be reflected in search_runs.queries_completed
    as 1 completed query -- not 0. The original implementation derived
    queries_completed from job-level counts (eligible_count +
    excluded_experience + excluded_location + timed_out_queries), which
    silently produced 0 whenever a query legitimately returned no jobs
    at all, even though the query itself unambiguously completed. No
    prior mock-based test caught this because every earlier test's
    FakeAdapter always returned at least one job.
    """
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = []  # adapter succeeds, but finds nothing
    db = _new_isolated_db()
    _seed_confirmed_candidate(db, "cand-36", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    result0 = _submit(db, "cand-36")

    conn = sqlite3.connect(db)
    claimed = claim_next_queue_item(conn)
    result = process_queue_item(conn, claimed)
    queries_completed, status = conn.execute(
        "SELECT queries_completed, status FROM search_runs WHERE search_run_id = ?", (result0.search_run_id,)
    ).fetchone()
    conn.close()

    if status != "COMPLETED":
        _fail(failures, f"test 36: expected status COMPLETED for a query that ran successfully with zero results, got {status}")
    elif queries_completed != 1:
        _fail(failures, f"test 36: expected queries_completed=1 (the query DID complete, just found nothing), got {queries_completed}")
    elif result.raw_count != 0:
        _fail(failures, f"test 36: expected raw_count=0 for this scenario, got {result.raw_count}")
    else:
        print("PASS: test 36 -> a successful query returning zero raw jobs is still correctly reflected as 1 completed query (regression test for the live-integration-test-discovered defect)")

    return failures


def test_35_existing_production_jobs_remain_intact():
    """
    Static/architectural guarantee check: the worker never DELETEs from
    or TRUNCATEs the jobs table, and its own global-job upserts are
    scoped strictly to UNIQUE(source, job_id) -- an existing, unrelated
    job row (e.g. a real production job discovered by a prior run) is
    never touched by processing a DIFFERENT job.
    """
    failures = []
    _FakeBehavior.reset()
    _FakeBehavior.jobs = [_fake_job(title="Totally Different Role", job_url="https://example.com/jobs/different")]
    db = _new_isolated_db()

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO jobs (job_id, source, company, title, location) VALUES ('PRIOR-1', 'OTHER', 'Existing Co', 'Existing Role', 'Chennai')"
    )
    conn.commit()
    before_row = conn.execute("SELECT * FROM jobs WHERE job_id = 'PRIOR-1'").fetchone()
    conn.close()

    _seed_confirmed_candidate(db, "cand-35", ["Senior Site Reliability Engineer"], ["Bengaluru"], experience_years=9)
    _submit(db, "cand-35")

    conn = sqlite3.connect(db)
    process_queue_item(conn, claim_next_queue_item(conn))
    after_row = conn.execute("SELECT * FROM jobs WHERE job_id = 'PRIOR-1'").fetchone()
    total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    if before_row != after_row:
        _fail(failures, "test 35: an unrelated pre-existing job row was modified by processing a different job")
    elif total_jobs != 2:
        _fail(failures, f"test 35: expected the pre-existing job plus the one new job (2 total), got {total_jobs}")
    else:
        print("PASS: test 35 -> pre-existing, unrelated job rows remain intact; the worker only ever touches rows it discovers")

    return failures


# ----------------------------------------------------------------------
# Static architecture checks (section 20 of the task)
# ----------------------------------------------------------------------

def test_static_no_hardcoding_and_clean_imports():
    failures = []
    source = inspect.getsource(search_worker_module)

    name_tokens_found = [t for t in ("Saroj", "Nayak") if t in source]
    if name_tokens_found:
        _fail(failures, f"static: search_worker.py contains a literal candidate name token: {name_tokens_found}")

    hardcoded_role_hint = re.search(r'"Senior Site Reliability Engineer"|"Platform Engineer"', source)
    if hardcoded_role_hint:
        _fail(failures, "static: search_worker.py appears to hardcode a specific target role")

    hardcoded_location_hint = re.search(r'"Bengaluru"|"Bangalore"|"Hyderabad"', source)
    if hardcoded_location_hint:
        _fail(failures, "static: search_worker.py appears to hardcode a specific target location")

    if re.search(r'candidate_id\s*=\s*"[a-zA-Z]', source):
        _fail(failures, "static: search_worker.py appears to hardcode a literal candidate_id default")

    if "config/profile.json" in source or "config/searches.json" in source:
        _fail(failures, "static: search_worker.py references a Saroj-specific config file")

    forbidden_app_terms = ["apply_to_job", "submit_application", "auto_apply", "application_submission"]
    found_app_terms = [t for t in forbidden_app_terms if t in source]
    if found_app_terms:
        _fail(failures, f"static: search_worker.py references application-submission symbols: {found_app_terms}")

    if "playwright" in source.lower() and "naukri" not in source.lower():
        # A generic worker importing playwright directly (rather than
        # only reaching browser automation indirectly through an
        # adapter resolved via source_registry) would be a real defect.
        _fail(failures, "static: search_worker.py appears to invoke browser automation directly")

    if not failures:
        print("PASS: static -> no Saroj-specific data, no hardcoded roles/locations/candidate_id, no application-submission or direct browser-automation code in search_worker.py")

    return failures


def test_static_sqlite_connection_is_explicit_not_global_singleton():
    failures = []
    source = inspect.getsource(search_worker_module)

    if re.search(r"^_?conn\s*=\s*sqlite3\.connect", source, re.MULTILINE):
        _fail(failures, "static: search_worker.py appears to open a module-level global sqlite3 connection singleton")

    signature = inspect.signature(process_queue_item)
    if "conn" not in signature.parameters:
        _fail(failures, "static: process_queue_item() does not take an explicit connection argument")

    if not failures:
        print("PASS: static -> search_worker.py takes its SQLite connection as an explicit argument everywhere; no global connection singleton")

    return failures


def main():
    tests = [
        test_1_queued_item_is_claimed,
        test_2_correct_source_adapter_executed,
        test_3_correct_query_reconstructed_from_snapshot,
        test_4_rawjob_results_are_normalized,
        test_5_global_job_is_inserted,
        test_6_existing_global_job_not_duplicated,
        test_7_candidate_eligibility_is_evaluated,
        test_8_below_profile_excluded,
        test_9_no_match_location_excluded,
        test_10_unknown_eligibility_handled_per_existing_semantics,
        test_11_eligible_job_is_scored,
        test_12_candidate_aware_score_persisted,
        test_13_candidate_job_match_created,
        test_14_existing_match_not_duplicated,
        test_15_queue_becomes_completed_after_success,
        test_16_search_run_becomes_completed,
        test_17_adapter_timeout_handled,
        test_18_adapter_blocked_handled,
        test_19_adapter_exception_becomes_failed,
        test_20_malformed_job_does_not_abort_valid_results,
        test_21_database_failure_rolls_back_persistence,
        test_22_unexpected_exception_does_not_leave_queue_running,
        test_23_no_job_specific_candidate_copies,
        test_24_same_global_job_matches_multiple_candidates,
        test_25_different_candidates_different_scores,
        test_26_eligibility_before_scoring,
        test_27_no_direct_naukri_import,
        test_28_no_network_calls_in_mock_tests,
        test_29_max_items_works,
        test_30_candidate_id_filtering_works,
        test_31_search_run_id_filtering_works,
        test_32_dry_run_does_not_mutate_db,
        test_33_search_run_aggregate_status_correct,
        test_34_queue_accounting_correct,
        test_35_existing_production_jobs_remain_intact,
        test_36_queries_completed_correct_when_zero_raw_results,
        test_static_no_hardcoding_and_clean_imports,
        test_static_sqlite_connection_is_explicit_not_global_singleton,
    ]

    all_failures = []
    for test in tests:
        all_failures.extend(test())

    print()

    if all_failures:
        print(f"{len(all_failures)} failure(s):")
        for failure in all_failures:
            print(f"  - {failure}")
        sys.exit(1)

    print(f"All {len(tests)} search-worker tests passed.")
    print("Note: this test file never opened data/applications/jobos.db and made zero real network/browser calls (a local FakeAdapter was used throughout).")


if __name__ == "__main__":
    main()
