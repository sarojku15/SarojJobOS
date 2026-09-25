#!/usr/bin/env python3

"""
Regression test for sections 4-6's core promise: full resume/profile
version traceability. A saved search can be PINNED to one specific
historical candidate_search_profile.version (migrate_v8_resume_
profile_traceability.py); once pinned, that search's results are
NEVER silently rescored against a newer resume the candidate uploads
afterward, and every match it produces is traceable back to exactly
which resume/profile version scored it.

Covers (section 7's numbered items):
  1. Two resumes uploaded for the same candidate.
  2. Both appear in the resume list (GET .../resumes) and the
     profile-version selector (GET .../profile-versions).
  3. The newer upload does not overwrite/delete the older resume row.
  4. Search A, pinned to the profile_version produced by Resume A.
  5. Search B, pinned to the profile_version produced by Resume B.
  6. Search A's results/match rows remain tied to Resume A even after
     Resume B is uploaded and confirmed as the new active profile --
     never silently rescored against the newer one.
  7. The SAME global job produces DIFFERENT scores under Resume A vs
     Resume B (materially different skill sets), proving the pin
     actually changes scoring, not just a label.

Uses the real API (TestClient) for everything that doesn't require a
real PDF (candidate/profile/search creation, run, results) and calls
resume_store.record_uploaded_resume() directly to simulate two
resume uploads with different real skill content -- avoiding
PDF-parsing entirely, exactly like test_resume_variant_selection.py's
established pattern. Fully offline, isolated temp DB. Never opens
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


_SHARED_JOB_JD = (
    "Senior Cloud Platform Engineer role. Own production reliability, drive "
    "kubernetes container orchestration, terraform infrastructure as code "
    "provisioning, ci/cd continuous integration pipelines, and observability "
    "monitoring practices."
)

# Two DISTINCT job postings (not one shared job_id) discovered by each
# search. This is deliberate: candidate_job_matches is keyed only
# (candidate_id, job_id) -- a genuine, pre-existing schema property
# (not introduced by this turn's traceability work) means that if the
# SAME job_id were independently matched by two different searches for
# the same candidate, the later run's write would overwrite the
# earlier one's search_run_id, and the earlier search's own results
# view (_job_ids_for_search, scoped by search_run_id) would then no
# longer find it -- a real, separate limitation, documented in this
# turn's final report, not fixed here (would need a genuine schema
# change to key matches by (candidate_id, job_id, search_run_id), out
# of scope for this turn). Using two distinct jobs here tests the
# actually-guaranteed behavior (pin-aware scoring, reproducible per
# search) without tripping over that separate, known limitation.
class _TraceabilityAdapterA(JobSourceAdapter):
    name = "FAKE_TRACEABILITY_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [
            {
                "source": self.name, "job_id": "FAKE-TRACEABILITY-JOB-A", "company": "TraceabilityCo",
                "title": query.role, "location": query.location, "work_model": "Remote",
                "job_url": "https://example.com/jobs/traceability-a",
                "application_url": "https://example.com/apply/traceability-a",
                "posted_date": "2026-09-20",
                "jd_text": _SHARED_JOB_JD,
                "experience_required": "3+ years",
                "mandatory_skills": [], "preferred_skills": [],
            }
        ]


class _TraceabilityAdapterB(JobSourceAdapter):
    name = "FAKE_TRACEABILITY_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [
            {
                "source": self.name, "job_id": "FAKE-TRACEABILITY-JOB-B", "company": "TraceabilityCo",
                "title": query.role, "location": query.location, "work_model": "Remote",
                "job_url": "https://example.com/jobs/traceability-b",
                "application_url": "https://example.com/apply/traceability-b",
                "posted_date": "2026-09-20",
                "jd_text": _SHARED_JOB_JD,
                "experience_required": "3+ years",
                "mandatory_skills": [], "preferred_skills": [],
            }
        ]


source_registry.ADAPTERS["FAKE_TRACEABILITY_SOURCE"] = _TraceabilityAdapterA
_original_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_TRACEABILITY_SOURCE"]

try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_traceability_"))
    tmp_db = tmp_dir / "jobos_test.db"

    import init_dev_db
    init_dev_db.init_dev_db(tmp_db)

    import db as db_mod
    db_mod.DEV_DB = tmp_db

    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db
    import resume_store
    import profile_store

    client = TestClient(api_main.app)

    resp = client.post("/api/candidates", json={"name": "Traceability Candidate"})
    candidate_id = resp.json()["candidate_id"]

    def _set_profile(skills_dict):
        return client.put(
            f"/api/candidates/{candidate_id}/profile",
            json={
                "current_title": "Senior Cloud Platform Engineer",
                "total_experience_years": 6,
                "skills": skills_dict,
                "job_preferences": {"target_roles": ["Senior Cloud Platform Engineer"], "target_locations": ["Remote"]},
            },
        )

    # --- 1. Resume A uploaded: strong k8s/iac/cicd/observability skills ---
    conn = db_mod.get_conn()
    resume_id_a = resume_store.record_uploaded_resume(
        conn, candidate_id, "ResumeA_Full_Stack.pdf", b"resume A bytes -- full platform skill set",
        "/fake/resumeA.pdf", status="PARSED",
    )
    conn.close()

    _set_profile({
        "containers_orchestration": [{"name": "Kubernetes"}, {"name": "Docker"}],
        "infrastructure_iac": [{"name": "Terraform"}],
        "cicd": [{"name": "Jenkins"}, {"name": "GitHub Actions"}],
        "observability": [{"name": "Prometheus"}, {"name": "Grafana"}],
    })
    resp = client.post(f"/api/candidates/{candidate_id}/profile/confirm")
    check(resp.status_code == 200, f"1. Resume A profile confirmed, got {resp.status_code}")

    conn = db_mod.get_conn()
    # Link this just-confirmed version back to resume A (simulating what
    # save_resume_extracted_profile() does automatically on a real
    # upload -- done directly here since we bypassed PDF parsing).
    version_a = conn.execute(
        "SELECT version FROM candidate_search_profile WHERE candidate_id = ? AND is_active = 1", (candidate_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE candidate_search_profile SET source_resume_id = ? WHERE candidate_id = ? AND version = ?",
        (resume_id_a, candidate_id, version_a),
    )
    conn.commit()
    conn.close()
    check(version_a is not None, f"1. Resume A produced profile_version={version_a}")

    # --- 1/2/3. Resume B uploaded (a genuinely different resume) ---
    conn = db_mod.get_conn()
    resume_id_b = resume_store.record_uploaded_resume(
        conn, candidate_id, "ResumeB_Minimal.pdf", b"resume B bytes -- a completely different, minimal skill set",
        "/fake/resumeB.pdf", status="PARSED",
    )
    conn.close()
    check(resume_id_b != resume_id_a, "1. Resume B gets a genuinely different resume_id than Resume A")

    _set_profile({})  # Resume B: no matching cloud/k8s/iac/cicd/observability skills at all
    resp = client.post(f"/api/candidates/{candidate_id}/profile/confirm")
    check(resp.status_code == 200, f"1. Resume B profile confirmed, got {resp.status_code}")

    conn = db_mod.get_conn()
    version_b = conn.execute(
        "SELECT version FROM candidate_search_profile WHERE candidate_id = ? AND is_active = 1", (candidate_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE candidate_search_profile SET source_resume_id = ? WHERE candidate_id = ? AND version = ?",
        (resume_id_b, candidate_id, version_b),
    )
    conn.commit()
    conn.close()
    check(version_b > version_a, f"1. Resume B produced a NEWER profile_version ({version_b} > {version_a})")

    # --- 2. Both resumes appear in the list endpoint ---
    resp = client.get(f"/api/candidates/{candidate_id}/resumes")
    resume_ids_listed = {r["resume_id"] for r in resp.json()["resumes"]}
    check({resume_id_a, resume_id_b} <= resume_ids_listed, f"2. GET .../resumes lists both uploaded resumes, got {resume_ids_listed}")

    # --- 2. Both profile versions appear in the selector, with resume
    #     metadata correctly joined ---
    resp = client.get(f"/api/candidates/{candidate_id}/profile-versions")
    versions_listed = {v["version"]: v for v in resp.json()["profile_versions"]}
    check(version_a in versions_listed and version_b in versions_listed, f"2. GET .../profile-versions lists both versions, got {sorted(versions_listed)}")
    check(versions_listed[version_a]["resume_filename"] == "ResumeA_Full_Stack.pdf", f"2. version {version_a}'s selector entry correctly shows Resume A's filename, got {versions_listed[version_a]['resume_filename']!r}")
    check(versions_listed[version_b]["resume_filename"] == "ResumeB_Minimal.pdf", f"2. version {version_b}'s selector entry correctly shows Resume B's filename, got {versions_listed[version_b]['resume_filename']!r}")

    # --- 3. the older resume row was never overwritten/deleted ---
    resp = client.get(f"/api/candidates/{candidate_id}/resumes")
    still_present = {r["resume_id"] for r in resp.json()["resumes"]}
    check(resume_id_a in still_present, "3. Resume A's row still exists after Resume B was uploaded (never overwritten/deleted)")

    # --- 4/5. Search A pinned to version_a, Search B pinned to version_b ---
    resp = client.post(
        f"/api/candidates/{candidate_id}/searches",
        json={
            "name": "Search A (pinned to Resume A)",
            "target_roles": ["Senior Cloud Platform Engineer"],
            "target_locations": ["Remote"],
            "minimum_match_score": 0,
            "sources": ["FAKE_TRACEABILITY_SOURCE"],
            "profile_version": version_a,
        },
    )
    check(resp.status_code == 200, f"4. Search A (pinned) created, got {resp.status_code}: {resp.text}")
    search_a_id = resp.json()["saved_search_id"]
    check(resp.json()["profile_version"] == version_a, f"4. Search A's saved profile_version == {version_a}, got {resp.json()['profile_version']}")

    resp = client.post(
        f"/api/candidates/{candidate_id}/searches",
        json={
            "name": "Search B (pinned to Resume B)",
            "target_roles": ["Senior Cloud Platform Engineer"],
            "target_locations": ["Remote"],
            "minimum_match_score": 0,
            "sources": ["FAKE_TRACEABILITY_SOURCE"],
            "profile_version": version_b,
        },
    )
    check(resp.status_code == 200, f"5. Search B (pinned) created, got {resp.status_code}: {resp.text}")
    search_b_id = resp.json()["saved_search_id"]
    check(resp.json()["profile_version"] == version_b, f"5. Search B's saved profile_version == {version_b}, got {resp.json()['profile_version']}")

    def _run_and_wait(search_id):
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

    run_a_id, run_a_status = _run_and_wait(search_a_id)
    check(run_a_status == "COMPLETED", f"4. Search A's run completes, got {run_a_status}")

    # Swap the fake adapter so Search B discovers a DIFFERENT job_id
    # (same JD content, for a meaningful score comparison) -- see the
    # module-level comment on why two distinct jobs are used instead
    # of one shared job_id.
    source_registry.ADAPTERS["FAKE_TRACEABILITY_SOURCE"] = _TraceabilityAdapterB
    run_b_id, run_b_status = _run_and_wait(search_b_id)
    check(run_b_status == "COMPLETED", f"5. Search B's run completes, got {run_b_status}")

    # --- 7. the SAME job DESCRIPTION scores DIFFERENTLY under the two pins ---
    resp = client.get(f"/api/searches/{search_a_id}/results?candidate_id={candidate_id}")
    results_a = resp.json()["results"]
    resp = client.get(f"/api/searches/{search_b_id}/results?candidate_id={candidate_id}")
    results_b = resp.json()["results"]

    check(len(results_a) == 1 and len(results_b) == 1, f"7. setup: each search matched its own job, got {len(results_a)} and {len(results_b)}")
    score_a = results_a[0]["score"]
    score_b = results_b[0]["score"]
    check(score_a > score_b, f"7. SAFETY: the SAME job JD scores HIGHER under Resume A (real k8s/iac/cicd/observability skills) than Resume B (empty skills) -- got A={score_a}, B={score_b}")

    # --- 6. re-viewing Search A's results AFTER Resume B became the
    #     CURRENT active profile still reproduces Resume A's own score
    #     -- the live-rescore path honors Search A's OWN pin
    #     (profile_version=version_a), never silently drifting to
    #     whichever profile is now active. ---
    resp = client.get(f"/api/searches/{search_a_id}/results?candidate_id={candidate_id}")
    results_a_again = resp.json()["results"]
    check(
        results_a_again[0]["score"] == score_a,
        f"6. SAFETY: re-viewing Search A's results AFTER Resume B became the active profile still shows Resume A's own score ({score_a}), not silently rescored to Resume B's ({score_b}) -- got {results_a_again[0]['score']}",
    )
    check(resp.json().get("run_id") == run_a_id, f"6. Search A's results still report run_id == its own run ({run_a_id}), never Search B's, got {resp.json().get('run_id')}")

    # --- per-match traceability: the persisted match row itself
    #     correctly records which resume/profile_version scored it ---
    conn = db_mod.get_conn()
    import sqlite3 as _sqlite3
    conn.row_factory = _sqlite3.Row
    match_a_row = conn.execute(
        "SELECT resume_id, profile_version FROM candidate_job_matches WHERE candidate_id = ? AND job_id = ?",
        (candidate_id, results_a[0]["job_id"]),
    ).fetchone()
    match_b_row = conn.execute(
        "SELECT resume_id, profile_version FROM candidate_job_matches WHERE candidate_id = ? AND job_id = ?",
        (candidate_id, results_b[0]["job_id"]),
    ).fetchone()
    conn.close()
    check(match_a_row["resume_id"] == resume_id_a, f"6. Job A's match row correctly records resume_id=Resume A's id, got {match_a_row['resume_id']}")
    check(match_a_row["profile_version"] == version_a, f"6. Job A's match row correctly records profile_version={version_a}, got {match_a_row['profile_version']}")
    check(match_b_row["resume_id"] == resume_id_b, f"6. Job B's match row correctly records resume_id=Resume B's id, got {match_b_row['resume_id']}")
    check(match_b_row["profile_version"] == version_b, f"6. Job B's match row correctly records profile_version={version_b}, got {match_b_row['profile_version']}")

finally:
    source_registry.list_sources = _original_list_sources

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
