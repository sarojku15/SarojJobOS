#!/usr/bin/env python3
"""
Disposable end-to-end acceptance test for the full Phase 1-6 workflow,
run through the REAL API (FastAPI TestClient) against a fake adapter --
no live network/browser call anywhere.

Covers the two workflows the master integration task explicitly asked
to be exercised end-to-end:

  1. saved search -> schedule enabled -> scheduler becomes due ->
     run-due -> triggers the existing search -> run recorded.
  2. job -> tailored resume -> company research -> interview prep ->
     follow-up date.

And confirms the full documented candidate journey is genuinely wired,
not just individually testable in isolation:

  discover -> score/match -> shortlist -> company research ->
  tailored resume -> approval -> application tracking -> follow-up ->
  interview preparation -> interview outcome

Fully offline, isolated temp DB. Production DB verified byte-identical
before/after.
"""
import hashlib
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)

import source_registry
from source_adapter import JobSourceAdapter, AdapterHealth, AdapterStatus, BlockReason

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


class _E2EFakeAdapter(JobSourceAdapter):
    name = "FAKE_E2E_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [{
            "source": self.name, "job_id": "FAKE-E2E-JOB", "company": "Acme Corp",
            "title": "Senior Site Reliability Engineer", "location": query.location, "work_model": "Remote",
            "job_url": "https://example.com/jobs/e2e", "application_url": "https://example.com/apply/e2e",
            "posted_date": "2026-09-26",
            "jd_text": "Kubernetes, AWS, and Terraform experience required for production reliability engineering.",
            "experience_required": "5+ years", "mandatory_skills": [], "preferred_skills": [],
        }]


source_registry.ADAPTERS["FAKE_E2E_SOURCE"] = _E2EFakeAdapter
source_registry.list_sources = lambda: ["FAKE_E2E_SOURCE"]

tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_e2e_"))
tmp_db = tmp_dir / "jobos.db"
import init_dev_db
init_dev_db.init_dev_db(tmp_db)
import db as db_mod
db_mod.DEV_DB = tmp_db
from fastapi.testclient import TestClient
import main as api_main
api_main.db_mod.DEV_DB = tmp_db
import scheduler
import resume_store
import company_research


class _NoProviderManager:
    """Forces the SAME deterministic, offline NOT_ATTEMPTED path
    test_company_research.py already exercises -- this dev environment's
    real .env may contain real search-provider keys (search_provider.py
    loads .env at import time), and without this override this E2E test
    would silently make a REAL network call to a paid external search
    API, contradicting its own "fully offline" contract and the
    project's "never depend on live external services in the normal
    suite" testing principle. Caught by actually running this test, not
    assumed."""

    def eligible_providers(self):
        return []


company_research.get_manager = lambda: _NoProviderManager()

client = TestClient(api_main.app)

# --------------------------------------------------------------- setup

resp = client.post("/api/candidates", json={"name": "E2E Candidate"})
candidate_id = resp.json()["candidate_id"]
resp = client.put(f"/api/candidates/{candidate_id}/profile", json={
    "current_title": "Senior SRE", "total_experience_years": 9,
    "summary": "Senior SRE with deep Kubernetes and AWS production experience.",
    "skills": {
        "containers_orchestration": [{"name": "Kubernetes"}],
        "cloud": [{"name": "AWS"}],
        "infrastructure_iac": [{"name": "Terraform"}],
    },
    "employment_history": [{
        "employer": "Acme", "title": "Senior SRE", "start_date": "Jan 2020", "end_date": "Present",
        "description": "Ran Kubernetes clusters on AWS and automated infra with Terraform for production reliability.",
        "skills": ["Kubernetes", "AWS", "Terraform"],
    }],
    "job_preferences": {"target_roles": ["SRE"], "target_locations": ["Remote"]},
})
check(resp.status_code == 200, f"setup: profile saved, got {resp.status_code}")
resp = client.post(f"/api/candidates/{candidate_id}/profile/confirm")
check(resp.status_code == 200, f"setup: profile confirmed, got {resp.status_code}")

conn = db_mod.get_conn()
resume_id = resume_store.record_uploaded_resume(
    conn, candidate_id, "e2e_resume.pdf", b"fake pdf bytes for e2e", "/fake/e2e.pdf", status="PARSED"
)
conn.close()

resp = client.post(f"/api/candidates/{candidate_id}/searches", json={
    "name": "E2E search", "target_roles": ["SRE"], "target_locations": ["Remote"],
    "minimum_match_score": 0, "sources": ["FAKE_E2E_SOURCE"],
})
search_id = resp.json()["saved_search_id"]
check(resp.status_code == 200 and bool(search_id), f"setup: saved search created, got {resp.status_code}")

# ============================================================ WORKFLOW 1
# saved search -> schedule enabled -> scheduler due -> run-due ->
# trigger existing search -> run recorded

resp = client.put(f"/api/searches/{search_id}/schedule?candidate_id={candidate_id}", json={"enabled": True, "frequency": "daily"})
check(resp.status_code == 200 and resp.json()["enabled"] == 1, f"W1: schedule enabled, got {resp.json()}")

conn = db_mod.get_conn()
conn.execute("UPDATE search_schedules SET next_run_at = '2020-01-01T00:00:00+00:00' WHERE saved_search_id = ?", (search_id,))
conn.commit()
conn.close()

resp = client.post("/api/scheduler/run-due")
check(resp.status_code == 200, f"W1: POST /api/scheduler/run-due succeeds, got {resp.status_code}")
outcomes = resp.json()["outcomes"]
matching = [o for o in outcomes if o["saved_search_id"] == search_id]
check(len(matching) == 1, f"W1: exactly one outcome for our saved search, got {matching}")
run_id = matching[0]["run_id"]
check(bool(run_id), f"W1: run-due genuinely triggered a real run via the EXISTING trigger_run(), got run_id={run_id}")

deadline = time.time() + 15
final_status = None
while time.time() < deadline:
    r = client.get(f"/api/runs/{run_id}?candidate_id={candidate_id}")
    final_status = r.json()["status"]
    if final_status in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED"):
        break
    time.sleep(0.1)
check(final_status == "COMPLETED", f"W1: the scheduler-triggered run reaches COMPLETED, got {final_status}")

resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
results = resp.json()["results"]
check(len(results) >= 1, f"W1: the run genuinely discovered/scored/persisted a real job, got {len(results)} results")
job = next((r for r in results if r["job_id"] == "FAKE-E2E-JOB"), None)
check(job is not None, "W1: our fake job made it through discover -> score -> match -> persist")
check(job["score"] is not None and job["priority"] in ("A", "B", "C", "REJECT"), f"W1: the job was scored and assigned a real priority by the EXISTING scoring engine (never a scoring bypass), got score={job['score']} priority={job['priority']}")
job_id = job["job_id"]

# ============================================================ WORKFLOW 2
# discover -> score/match -> shortlist -> company research ->
# tailored resume -> approval -> application tracking -> follow-up ->
# interview preparation -> interview outcome

# shortlist (application lifecycle)
resp = client.patch(f"/api/candidates/{candidate_id}/jobs/{job_id}/status", json={"status": "SHORTLISTED"})
check(resp.status_code == 200, f"W2: job shortlisted, got {resp.status_code} {resp.text[:150]}")

# company research
resp = client.post(f"/api/candidates/{candidate_id}/company-research", json={"company_name": "Acme Corp", "job_id": job_id})
check(resp.status_code == 200, f"W2: company research ran (real status, no provider configured -> NOT_ATTEMPTED expected), got {resp.status_code}")
research = resp.json()
check(research["status"] == "NOT_ATTEMPTED", f"W2: honest NOT_ATTEMPTED status since no search-provider key is configured in this test environment, got {research['status']}")

# tailored resume
resp = client.post(f"/api/candidates/{candidate_id}/jobs/{job_id}/tailor-resume", json={"base_resume_id": resume_id, "job_id": job_id})
check(resp.status_code == 200, f"W2: tailored resume generated, got {resp.status_code} {resp.text[:200]}")
tailored = resp.json()
check(tailored["status"] == "READY" and tailored["factual_safety_status"] == "PASS", f"W2: tailored resume READY with factual safety PASS, got {tailored['status']}/{tailored['factual_safety_status']}")

# approval -> application tracking (the APPROVED-before-APPLIED gate)
resp = client.patch(f"/api/candidates/{candidate_id}/jobs/{job_id}/status", json={"status": "READY_FOR_APPROVAL"})
check(resp.status_code == 200, f"W2: moved to READY_FOR_APPROVAL, got {resp.status_code}")
resp = client.patch(f"/api/candidates/{candidate_id}/jobs/{job_id}/status", json={"status": "APPROVED"})
check(resp.status_code == 200, f"W2: moved to APPROVED, got {resp.status_code}")
resp = client.patch(f"/api/candidates/{candidate_id}/jobs/{job_id}/status", json={"status": "APPLIED"})
check(resp.status_code == 200, f"W2: APPLIED reachable only AFTER APPROVED, got {resp.status_code} {resp.text[:150]}")

# follow-up date
resp = client.patch(f"/api/candidates/{candidate_id}/jobs/{job_id}/follow-up", json={"follow_up_date": "2026-10-15"})
check(resp.status_code == 200 and resp.json()["follow_up_date"] == "2026-10-15", f"W2: follow-up date set, got {resp.json()}")
resp = client.get(f"/api/candidates/{candidate_id}/follow-ups")
check(any(f["job_id"] == job_id and f["follow_up_date"] == "2026-10-15" for f in resp.json()["follow_ups"]), f"W2: follow-up appears in the candidate's follow-ups list, got {resp.json()}")

# interview preparation (using the tailored resume + company research just created)
resp = client.post(f"/api/candidates/{candidate_id}/interview-prep", json={
    "job_id": job_id, "resume_id": resume_id, "company_research_id": research["company_research_id"],
})
check(resp.status_code == 200, f"W2: interview preparation generated, got {resp.status_code} {resp.text[:200]}")
prep = resp.json()
check(prep["status"] == "READY" and len(prep["questions"]) > 0, f"W2: interview prep READY with real questions, got status={prep['status']} n_questions={len(prep['questions'])}")
k8s_q = next(q for q in prep["questions"] if q["category"] == "kubernetes")
check(k8s_q["has_demonstrated_experience"] and "Acme" in k8s_q["model_answer_text"], "W2: Kubernetes question correctly evidence-backed from the real resume")

# interview outcome
resp = client.patch(f"/api/candidates/{candidate_id}/interview-prep/{prep['interview_prep_id']}/outcome", json={"outcome_status": "PASSED", "outcome_notes": "Great conversation."})
check(resp.status_code == 200, f"W2: interview outcome recorded, got {resp.status_code}")
resp = client.get(f"/api/candidates/{candidate_id}/interview-prep/{prep['interview_prep_id']}")
check(resp.json()["outcome_status"] == "PASSED", f"W2: outcome persisted and readable back, got {resp.json()['outcome_status']}")

# lifecycle invariant re-confirmed: a DIFFERENT job cannot skip straight to APPLIED without APPROVED
conn = db_mod.get_conn()
conn.execute(
    "INSERT INTO jobs (job_id, source, company, title, location, work_model, jd_text, experience_required, mandatory_skills, preferred_skills, discovered_at, last_updated) "
    "VALUES ('e2e-lifecycle-guard-job', 'TEST', 'TestCo', 'SRE', 'Remote', 'Remote', 'test', '3+ years', '[]', '[]', datetime('now'), datetime('now'))"
)
conn.execute(
    "INSERT INTO candidate_job_matches (candidate_id, job_id, fit_score, priority, candidate_status, created_at, updated_at) "
    "VALUES (?, 'e2e-lifecycle-guard-job', 80, 'B', 'FOUND', datetime('now'), datetime('now'))",
    (candidate_id,),
)
conn.commit()
conn.close()
resp = client.patch(f"/api/candidates/{candidate_id}/jobs/e2e-lifecycle-guard-job/status", json={"status": "APPLIED"})
check(resp.status_code == 400, f"lifecycle invariant: APPLIED without APPROVED first is still rejected, got {resp.status_code}")

# safety: never an automatic application submission anywhere in this path
submission_like_routes = [
    getattr(r, "path", "") for r in api_main.app.routes
    if "submit" in getattr(r, "path", "").lower() or "apply" in getattr(r, "path", "").lower()
]
check(submission_like_routes == [], f"safety: no application-submission route exists anywhere in this API, got {submission_like_routes}")

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
