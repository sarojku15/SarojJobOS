#!/usr/bin/env python3

"""
Regression test for the genuinely-missing application-lifecycle write
path this session's audit found: config/application_schema.json
already defined a full status lifecycle and candidate_job_matches.
candidate_status already existed to hold it, but nothing ever wrote to
it after initial discovery.

Covers:
  1. A newly-matched job starts at whatever score_job() computed
     (READY_FOR_APPROVAL / FOUND / NOT_QUALIFIED).
  2. A human can move it forward (SHORTLISTED, then APPROVED) via the
     new PATCH /api/candidates/{id}/jobs/{job_id}/status endpoint.
  3. SAFETY GATE: APPLICATION_STARTED/APPLIED is unreachable unless
     already APPROVED -- the system never lets a caller skip the
     human-approval step, even via a direct status-update call.
  4. Once APPROVED, APPLIED is reachable (recording a human's own
     manual application, never triggered automatically by this system).
  5. An unrecognized status value is rejected (400), never silently
     accepted.
  6. candidate_job_matches.candidate_status survives a job being
     RE-MATCHED by a later search run -- the pre-existing bug this
     session's audit found (upsert_candidate_job_match() used to
     silently overwrite candidate_status on every re-match) is fixed.
  7. Ownership: candidate B cannot change candidate A's job status.

Exercises api/main.py's FastAPI app in-process via TestClient. Fully
offline, isolated temp DB (never jobos.db/jobos_dev.db). No network.
"""

import hashlib
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
API_DIR = ROOT / "api"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(API_DIR))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)

import source_registry
from source_adapter import JobSourceAdapter, AdapterHealth, BlockReason, AdapterStatus


class FakeAdapterEnabled(JobSourceAdapter):
    name = "FAKE_LIFECYCLE_ENABLED"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [
            {
                "source": self.name,
                "company": "LifecycleCo",
                "title": query.role,
                "location": query.location,
                "work_model": "Hybrid",
                "job_url": "https://example.com/jobs/lifecycle-1",
                "application_url": "https://example.com/apply/lifecycle-1",
                "posted_date": "2026-09-19",
                "jd_text": f"{query.role} role requiring strong skills.",
                "experience_required": "5+ years",
                "mandatory_skills": [],
                "preferred_skills": [],
            }
        ]


source_registry.ADAPTERS["FAKE_LIFECYCLE_ENABLED"] = FakeAdapterEnabled
_original_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_LIFECYCLE_ENABLED"]

passed = 0
failed = 0


def check(condition, message):
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS: {message}")
    else:
        failed += 1
        print(f"FAIL: {message}")


try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_lifecycle_"))
    tmp_db = tmp_dir / "jobos_test.db"

    import init_dev_db
    init_dev_db.init_dev_db(tmp_db)

    import db as db_mod
    db_mod.DEV_DB = tmp_db

    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db

    client = TestClient(api_main.app)

    def _make_confirmed_candidate(name, role, location):
        resp = client.post("/api/candidates", json={"name": name})
        candidate_id = resp.json()["candidate_id"]
        client.put(
            f"/api/candidates/{candidate_id}/profile",
            json={
                "current_title": role,
                "total_experience_years": 5,
                "skills": {"other": [{"name": "Generic Skill"}]},
                "job_preferences": {"target_roles": [role], "target_locations": [location]},
            },
        )
        client.post(f"/api/candidates/{candidate_id}/profile/confirm")
        return candidate_id

    def _run_and_wait(candidate_id, role, location):
        resp = client.post(
            f"/api/candidates/{candidate_id}/searches",
            json={
                "name": "lifecycle search",
                "target_roles": [role],
                "target_locations": [location],
                "minimum_match_score": 0,
                "sources": ["FAKE_LIFECYCLE_ENABLED"],
            },
        )
        search_id = resp.json()["saved_search_id"]
        resp = client.post(f"/api/searches/{search_id}/run?candidate_id={candidate_id}")
        run_id = resp.json()["run_id"]
        deadline = time.time() + 20
        status = None
        while time.time() < deadline:
            r = client.get(f"/api/runs/{run_id}?candidate_id={candidate_id}")
            status = r.json()["status"]
            if status in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED"):
                break
            time.sleep(0.2)
        assert status == "COMPLETED", f"setup run did not complete: {status}"
        resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
        return resp.json()["results"], search_id

    candidate_a = _make_confirmed_candidate("Lifecycle Candidate A", "Role A", "City A")
    results, search_id = _run_and_wait(candidate_a, "Role A", "City A")
    check(len(results) >= 1, "setup: search produced at least 1 result")
    job_id = results[0]["job_id"]

    # --- 1. starting status is whatever score_job() computed (real,
    #     not fabricated by this test -- any of the 3 real score-bucket
    #     outcomes is a valid starting point) ---
    check(results[0]["status"] in ("READY_FOR_APPROVAL", "FOUND", "NOT_QUALIFIED"), f"1. newly-matched job starts at a real score-bucket status, got {results[0]['status']}")

    # --- 2. human moves it forward: SHORTLISTED -> APPROVED ---
    resp = client.patch(f"/api/candidates/{candidate_a}/jobs/{job_id}/status", json={"status": "SHORTLISTED"})
    check(resp.status_code == 200 and resp.json()["candidate_status"] == "SHORTLISTED", f"2. human can shortlist a job (got {resp.status_code}, {resp.json() if resp.status_code == 200 else ''})")

    resp = client.patch(f"/api/candidates/{candidate_a}/jobs/{job_id}/status", json={"status": "APPROVED"})
    check(resp.status_code == 200 and resp.json()["candidate_status"] == "APPROVED", "2. human can approve a shortlisted job")

    # --- 3. SAFETY GATE: cannot reach APPLIED without being APPROVED first ---
    resp = client.patch(f"/api/candidates/{candidate_a}/jobs/{job_id}/status", json={"status": "SHORTLISTED"})
    check(resp.status_code == 200, "setup: move back to SHORTLISTED (not yet approved) to test the gate")
    resp = client.patch(f"/api/candidates/{candidate_a}/jobs/{job_id}/status", json={"status": "APPLIED"})
    check(resp.status_code == 400, f"3. SAFETY VIOLATION CHECK -- APPLIED from SHORTLISTED (not APPROVED) must be rejected, got {resp.status_code}")
    resp = client.patch(f"/api/candidates/{candidate_a}/jobs/{job_id}/status", json={"status": "APPLICATION_STARTED"})
    check(resp.status_code == 400, f"3. SAFETY VIOLATION CHECK -- APPLICATION_STARTED from SHORTLISTED must also be rejected, got {resp.status_code}")

    # --- 4. once APPROVED, APPLIED is reachable (human recording their own action) ---
    client.patch(f"/api/candidates/{candidate_a}/jobs/{job_id}/status", json={"status": "APPROVED"})
    resp = client.patch(f"/api/candidates/{candidate_a}/jobs/{job_id}/status", json={"status": "APPLIED"})
    check(resp.status_code == 200 and resp.json()["candidate_status"] == "APPLIED", f"4. once APPROVED, APPLIED is reachable (recording a human's own manual action), got {resp.status_code}")

    # --- 5. unrecognized status rejected ---
    resp = client.patch(f"/api/candidates/{candidate_a}/jobs/{job_id}/status", json={"status": "TOTALLY_MADE_UP_STATUS"})
    check(resp.status_code == 400, f"5. an unrecognized status value is rejected (400), got {resp.status_code}")

    # --- 6. candidate_status survives a re-match (the pre-existing bug this session fixed) ---
    # Re-running the SAME saved search re-discovers and re-matches the
    # identical fake job -- exercising upsert_candidate_job_match()'s
    # ON CONFLICT path, not a fresh INSERT.
    resp = client.post(f"/api/searches/{search_id}/run?candidate_id={candidate_a}")
    rerun_id = resp.json()["run_id"]
    deadline = time.time() + 20
    rerun_status = None
    while time.time() < deadline:
        r = client.get(f"/api/runs/{rerun_id}?candidate_id={candidate_a}")
        rerun_status = r.json()["status"]
        if rerun_status in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED"):
            break
        time.sleep(0.2)
    check(rerun_status == "COMPLETED", f"6. setup: the re-run (same saved search) completes, got {rerun_status}")

    resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_a}")
    rematched = next((r for r in resp.json()["results"] if r["job_id"] == job_id), None)
    check(rematched is not None, "6. setup: the re-matched job is still present")
    check(rematched["status"] == "APPLIED", f"6. SAFETY VIOLATION CHECK -- candidate_status must survive a re-match (was APPLIED), got {rematched['status'] if rematched else None}")

    # --- 7. ownership: candidate B cannot change candidate A's job status ---
    candidate_b = _make_confirmed_candidate("Lifecycle Candidate B", "Role B", "City B")
    resp = client.patch(f"/api/candidates/{candidate_b}/jobs/{job_id}/status", json={"status": "SHORTLISTED"})
    check(resp.status_code == 404, f"7. candidate B cannot change candidate A's job status (no match record for B), got {resp.status_code}")

    # Unknown candidate / unknown job.
    resp = client.patch(f"/api/candidates/does-not-exist/jobs/{job_id}/status", json={"status": "SHORTLISTED"})
    check(resp.status_code == 404, "8. unknown candidate_id -> 404")

    resp = client.patch(f"/api/candidates/{candidate_a}/jobs/does-not-exist/status", json={"status": "SHORTLISTED"})
    check(resp.status_code == 404, "8. unknown job_id for a real candidate -> 404")

    # --- 9. transition history: one row per transition, in order, with
    #     truthful from/to values -- see migrate_v6_status_history.py ---
    resp = client.get(f"/api/candidates/{candidate_a}/jobs/{job_id}/status-history")
    check(resp.status_code == 200, f"9. status-history endpoint reachable, got {resp.status_code}")
    body = resp.json()
    history = body["history"]
    # From the sequence above on job_id: SHORTLISTED, APPROVED,
    # SHORTLISTED, APPROVED (2 rejected attempts wrote no rows), APPLIED.
    # from_status on the first transition is whatever real score-bucket
    # status search_worker.py originally seeded (step 1 above) -- never
    # assumed to be None, since every match starts with a real bucket.
    initial_status = results[0]["status"]
    expected_transitions = [
        (initial_status, "SHORTLISTED"),
        ("SHORTLISTED", "APPROVED"),
        ("APPROVED", "SHORTLISTED"),
        ("SHORTLISTED", "APPROVED"),
        ("APPROVED", "APPLIED"),
    ]
    got_transitions = [(h["from_status"], h["to_status"]) for h in history]
    check(got_transitions == expected_transitions, f"9. history has exactly the real transitions in order, expected {expected_transitions}, got {got_transitions}")
    check(body["current_status"] == "APPLIED", f"9. status-history reports the correct current_status, got {body['current_status']}")

    # Rejected transitions (the two 400s in step 3) must NOT have written
    # a history row -- history only ever reflects transitions that
    # actually happened.
    to_statuses = [h["to_status"] for h in history]
    check("APPLICATION_STARTED" not in to_statuses or to_statuses.count("APPLIED") == 1, "9. a rejected (400) transition attempt did not corrupt the history")

    # Ownership: candidate B cannot read candidate A's history for the
    # same global job.
    resp = client.get(f"/api/candidates/{candidate_b}/jobs/{job_id}/status-history")
    check(resp.status_code == 404, f"9. candidate B cannot read candidate A's status history for the same job, got {resp.status_code}")

finally:
    source_registry.list_sources = _original_list_sources

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
