#!/usr/bin/env python3

"""
Regression test for editing an existing saved search (item 4: Edit
Existing Search UI) via PUT /api/searches/{id} -- the backend
endpoint already existed (created in an earlier turn's traceability
work); this covers the newly-added ability to change the pinned
profile_version specifically, and confirms editing never rewrites
history.

Covers:
  - editing a search's criteria persists across a reload.
  - changing the pinned profile_version persists.
  - a run started BEFORE the edit keeps its own original
    resume/profile lineage on its matches -- editing a search never
    retroactively touches an already-persisted run's own results.
  - a run started AFTER the edit uses the newly-selected profile_version.
  - explicitly un-pinning (setting profile_version back to null) via
    an edit persists correctly (not silently ignored -- see
    search_store.update_saved_search()'s model_fields_set handling).
  - omitting profile_version entirely from an edit leaves the existing
    pin (or lack of one) untouched.

Fully offline (fake adapters, no real network call). Never opens
data/applications/jobos.db.
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
from source_adapter import JobSourceAdapter, AdapterHealth, AdapterStatus, BlockReason

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


class _EditTestAdapter(JobSourceAdapter):
    name = "FAKE_EDIT_TEST_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [
            {
                "source": self.name, "job_id": "FAKE-EDIT-TEST-JOB", "company": "EditTestCo",
                "title": query.role, "location": query.location, "work_model": "Remote",
                "job_url": "https://example.com/jobs/edit-test", "application_url": "https://example.com/apply/edit-test",
                "posted_date": "2026-09-20",
                "jd_text": "Senior Cloud Platform Engineer role. kubernetes terraform ci/cd monitoring.",
                "experience_required": "3+ years",
                "mandatory_skills": [], "preferred_skills": [],
            }
        ]


source_registry.ADAPTERS["FAKE_EDIT_TEST_SOURCE"] = _EditTestAdapter
_original_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_EDIT_TEST_SOURCE"]

try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_edit_search_"))
    tmp_db = tmp_dir / "jobos_test.db"

    import init_dev_db
    init_dev_db.init_dev_db(tmp_db)

    import db as db_mod
    db_mod.DEV_DB = tmp_db

    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db
    import resume_store

    client = TestClient(api_main.app)

    resp = client.post("/api/candidates", json={"name": "Edit Test Candidate"})
    candidate_id = resp.json()["candidate_id"]

    def _set_profile_and_confirm(skills):
        client.put(
            f"/api/candidates/{candidate_id}/profile",
            json={
                "current_title": "Senior Cloud Platform Engineer", "total_experience_years": 6, "skills": skills,
                "job_preferences": {"target_roles": ["Senior Cloud Platform Engineer"], "target_locations": ["Remote"]},
            },
        )
        client.post(f"/api/candidates/{candidate_id}/profile/confirm")
        conn = db_mod.get_conn()
        v = conn.execute("SELECT version FROM candidate_search_profile WHERE candidate_id=? AND is_active=1", (candidate_id,)).fetchone()[0]
        conn.close()
        return v

    # Resume A
    conn = db_mod.get_conn()
    resume_id_a = resume_store.record_uploaded_resume(conn, candidate_id, "EditTestResumeA.pdf", b"resume A", "/fake/a.pdf", status="PARSED")
    conn.close()
    version_a = _set_profile_and_confirm({"containers_orchestration": [{"name": "Kubernetes"}]})
    conn = db_mod.get_conn()
    conn.execute("UPDATE candidate_search_profile SET source_resume_id=? WHERE candidate_id=? AND version=?", (resume_id_a, candidate_id, version_a))
    conn.commit(); conn.close()

    # Resume B (uploaded later)
    conn = db_mod.get_conn()
    resume_id_b = resume_store.record_uploaded_resume(conn, candidate_id, "EditTestResumeB.pdf", b"resume B", "/fake/b.pdf", status="PARSED")
    conn.close()
    version_b = _set_profile_and_confirm({"infrastructure_iac": [{"name": "Terraform"}]})
    conn = db_mod.get_conn()
    conn.execute("UPDATE candidate_search_profile SET source_resume_id=? WHERE candidate_id=? AND version=?", (resume_id_b, candidate_id, version_b))
    conn.commit(); conn.close()

    # --- create a search pinned to Resume A ---
    resp = client.post(
        f"/api/candidates/{candidate_id}/searches",
        json={
            "name": "Edit test search", "target_roles": ["Senior Cloud Platform Engineer"], "target_locations": ["Remote"],
            "minimum_match_score": 0, "sources": ["FAKE_EDIT_TEST_SOURCE"], "profile_version": version_a,
        },
    )
    search_id = resp.json()["saved_search_id"]
    check(resp.json()["profile_version"] == version_a, f"setup: search created pinned to version_a={version_a}")

    def _run_and_wait():
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
        return run_id, status

    # --- run BEFORE any edit -- uses Resume A ---
    run_before_edit_id, status = _run_and_wait()
    check(status == "COMPLETED", f"setup: pre-edit run completes, got {status}")

    resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
    results_before = resp.json()["results"]
    check(len(results_before) == 1, f"setup: pre-edit run produced 1 result, got {len(results_before)}")
    shared_job_id = results_before[0]["job_id"]

    conn = db_mod.get_conn()
    conn.row_factory = __import__("sqlite3").Row
    match_before = conn.execute(
        "SELECT resume_id, profile_version FROM candidate_job_matches WHERE candidate_id=? AND job_id=?",
        (candidate_id, shared_job_id),
    ).fetchone()
    conn.close()
    check(match_before["resume_id"] == resume_id_a, f"pre-edit run's match uses Resume A, got {match_before['resume_id']}")
    check(match_before["profile_version"] == version_a, f"pre-edit run's match uses profile_version={version_a}, got {match_before['profile_version']}")

    # --- EDIT: change name + pin to Resume B ---
    resp = client.put(
        f"/api/searches/{search_id}?candidate_id={candidate_id}",
        json={"name": "Edit test search (renamed)", "profile_version": version_b},
    )
    check(resp.status_code == 200, f"edit: PUT succeeds, got {resp.status_code}: {resp.text}")
    check(resp.json()["profile_version"] == version_b, f"edit: response reflects new pin={version_b}, got {resp.json()['profile_version']}")

    # --- reload: persistence check ---
    resp = client.get(f"/api/searches/{search_id}?candidate_id={candidate_id}")
    reloaded = resp.json()
    check(reloaded["name"] == "Edit test search (renamed)", f"reload: name change persisted, got {reloaded['name']!r}")
    check(reloaded["profile_version"] == version_b, f"reload: profile_version change persisted, got {reloaded['profile_version']}")

    # --- the ALREADY-PERSISTED pre-edit run/match is untouched by the edit ---
    conn = db_mod.get_conn()
    conn.row_factory = __import__("sqlite3").Row
    match_still = conn.execute(
        "SELECT resume_id, profile_version, search_run_id FROM candidate_job_matches WHERE candidate_id=? AND job_id=?",
        (candidate_id, shared_job_id),
    ).fetchone()
    conn.close()
    # NOTE: the match row is keyed (candidate_id, job_id) -- editing the
    # SAVED SEARCH itself writes nothing to candidate_job_matches at
    # all (confirmed: still resume_id_a/version_a here, unchanged by
    # the PUT above). It only changes on a NEW run, checked next.
    check(match_still["resume_id"] == resume_id_a, "edit does not retroactively rewrite the pre-edit run's own match row (still Resume A)")
    check(match_still["profile_version"] == version_a, "edit does not retroactively rewrite the pre-edit run's own profile_version (still version_a)")
    check(match_still["search_run_id"] == run_before_edit_id, "edit does not retroactively change which run the pre-edit match is attributed to")

    resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
    check(resp.json()["run_id"] == run_before_edit_id, "results view (before any new run) still correctly reports the pre-edit run_id")

    # --- run AFTER the edit -- must use Resume B ---
    run_after_edit_id, status = _run_and_wait()
    check(status == "COMPLETED", f"post-edit run completes, got {status}")
    check(run_after_edit_id != run_before_edit_id, "post-edit run has a genuinely new run_id")

    conn = db_mod.get_conn()
    conn.row_factory = __import__("sqlite3").Row
    match_after = conn.execute(
        "SELECT resume_id, profile_version FROM candidate_job_matches WHERE candidate_id=? AND job_id=?",
        (candidate_id, shared_job_id),
    ).fetchone()
    conn.close()
    check(match_after["resume_id"] == resume_id_b, f"post-edit run's (re-matched) row now uses Resume B, got {match_after['resume_id']}")
    check(match_after["profile_version"] == version_b, f"post-edit run's (re-matched) row now uses profile_version={version_b}, got {match_after['profile_version']}")

    resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
    check(resp.json()["run_id"] == run_after_edit_id, "results view now correctly reports the NEW (post-edit) run_id")

    # --- explicitly un-pin back to "Current profile" ---
    resp = client.put(f"/api/searches/{search_id}?candidate_id={candidate_id}", json={"profile_version": None})
    check(resp.status_code == 200, f"un-pin: PUT with profile_version=null succeeds, got {resp.status_code}")
    check(resp.json()["profile_version"] is None, f"un-pin: response shows profile_version=None, got {resp.json()['profile_version']}")
    resp = client.get(f"/api/searches/{search_id}?candidate_id={candidate_id}")
    check(resp.json()["profile_version"] is None, "un-pin: persists across reload (profile_version=None, not silently ignored)")

    # --- omitting profile_version entirely from an edit leaves it untouched ---
    resp = client.put(f"/api/searches/{search_id}?candidate_id={candidate_id}", json={"name": "yet another rename"})
    check(resp.json()["profile_version"] is None, "omitting profile_version from an edit payload leaves the existing (un-pinned) state untouched")

    resp = client.put(f"/api/searches/{search_id}?candidate_id={candidate_id}", json={"profile_version": version_a})
    resp2 = client.put(f"/api/searches/{search_id}?candidate_id={candidate_id}", json={"name": "another rename, pin should survive"})
    check(resp2.json()["profile_version"] == version_a, f"omitting profile_version from a later edit preserves a previously-set pin, got {resp2.json()['profile_version']}")

finally:
    source_registry.list_sources = _original_list_sources

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
