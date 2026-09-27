#!/usr/bin/env python3
"""
Phase 1 regression tests: resume tailoring (scripts/resume_tailoring.py,
migrate_v11_tailored_resumes.py, api/main.py's tailor-resume routes).

Covers (Phase 13 checklist):
  A. resume tailoring works end-to-end via the real API
  B. resume version preservation (base resume + earlier tailored
     versions are never overwritten)
  C. no fabricated facts (factual_safety_status is a real, automated
     PASS, and every generated word traces back to the original
     profile)
  D. tailored resume <-> job association (job_id/base_resume_id
     persisted and queryable)
  T. candidate isolation (candidate B cannot read/download candidate
     A's tailored resume by ID)

Fully offline (fake adapter not even needed -- this module never
touches a source adapter), isolated temp DB, no live network calls.
Production DB is verified byte-identical before/after.
"""
import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

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


tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_tailoring_"))
tmp_db = tmp_dir / "jobos.db"
import init_dev_db
init_dev_db.init_dev_db(tmp_db)
import db as db_mod
db_mod.DEV_DB = tmp_db
from fastapi.testclient import TestClient
import main as api_main
api_main.db_mod.DEV_DB = tmp_db
import resume_store

client = TestClient(api_main.app)

resp = client.post("/api/candidates", json={"name": "Tailoring Test A"})
candidate_a = resp.json()["candidate_id"]
resp = client.post("/api/candidates", json={"name": "Tailoring Test B"})
candidate_b = resp.json()["candidate_id"]

client.put(f"/api/candidates/{candidate_a}/profile", json={
    "current_title": "SRE", "total_experience_years": 9,
    "summary": "Backend engineer with strong Python skills. Experienced in Kubernetes and Terraform deployments.",
    "skills": {
        "containers_orchestration": [{"name": "Kubernetes"}],
        "infrastructure_iac": [{"name": "Terraform"}],
        "cloud": [{"name": "AWS"}],
        "programming_scripting": [{"name": "Python"}],
    },
    "employment_history": [
        {"employer": "Acme", "title": "SRE", "start_date": "Jan 2020", "end_date": "Present",
         "description": "Deployed Kubernetes clusters across AWS. Automated infra with Terraform.",
         "skills": ["Kubernetes", "AWS", "Terraform"]},
    ],
    "job_preferences": {"target_roles": ["SRE"], "target_locations": ["Remote"]},
})
client.post(f"/api/candidates/{candidate_a}/profile/confirm")

conn = db_mod.get_conn()
resume_a = resume_store.record_uploaded_resume(
    conn, candidate_a, "resume_a.pdf", b"fake pdf bytes for candidate A", "/fake/a.pdf", status="PARSED"
)
conn.execute(
    "INSERT INTO jobs (job_id, source, company, title, location, work_model, jd_text, experience_required, mandatory_skills, preferred_skills, discovered_at, last_updated) "
    "VALUES ('job-tailor-1', 'TEST', 'TestCo', 'Senior SRE', 'Remote', 'Remote', 'Kubernetes and Terraform and AWS experience needed for production reliability.', '5+ years', '[]', '[]', datetime('now'), datetime('now'))"
)
conn.commit()
conn.close()

# --- A. end-to-end tailoring via the real API ---
resp = client.post(f"/api/candidates/{candidate_a}/jobs/job-tailor-1/tailor-resume", json={"base_resume_id": resume_a, "job_id": "job-tailor-1"})
check(resp.status_code == 200, f"A: tailor-resume API succeeds, got {resp.status_code} {resp.text[:200]}")
tailored = resp.json()
check(tailored["status"] == "READY", f"A: tailored resume status is READY, got {tailored['status']}")
check("Kubernetes" in tailored["keywords_emphasized"], f"A: real matched keyword emphasized, got {tailored['keywords_emphasized']}")

# --- C. factual safety: no fabricated facts ---
check(tailored["factual_safety_status"] == "PASS", f"C: factual_safety_status is PASS (reorder-only, never fabricated), got {tailored['factual_safety_status']}")
check(tailored["factual_safety_notes"] == [], f"C: no unsupported-word notes, got {tailored['factual_safety_notes']}")
original_employer_names = {"Acme"}
check(all(h["employer"] in original_employer_names for h in tailored["content"]["relevant_experience_highlights"]),
      "C: every highlighted employer is a real one from the original profile")

# --- D. job/base-resume association persisted ---
check(tailored["job_id"] == "job-tailor-1" and tailored["base_resume_id"] == resume_a, "D: tailored resume correctly associated with job_id and base_resume_id")

resp = client.get(f"/api/candidates/{candidate_a}/jobs/job-tailor-1/tailored-resumes")
check(len(resp.json()["tailored_resumes"]) == 1, f"D: listing by job_id returns exactly this one tailored resume, got {len(resp.json()['tailored_resumes'])}")

# --- B. version preservation: original resume never overwritten, regenerate creates v2 ---
conn = db_mod.get_conn()
original_resume_row = conn.execute("SELECT filename, content_hash FROM resumes WHERE resume_id = ?", (resume_a,)).fetchone()
conn.close()
check(original_resume_row is not None, "B: original base resume row still exists, untouched")

resp2 = client.post(f"/api/candidates/{candidate_a}/jobs/job-tailor-1/tailor-resume", json={"base_resume_id": resume_a, "job_id": "job-tailor-1"})
tailored_v2 = resp2.json()
check(tailored_v2["tailoring_version"] == 2, f"B: regenerating creates version 2 (never overwrites v1), got {tailored_v2['tailoring_version']}")
check(tailored_v2["tailored_resume_id"] != tailored["tailored_resume_id"], "B: v2 is a genuinely NEW row, not an update to v1")

resp_v1_still_there = client.get(f"/api/candidates/{candidate_a}/tailored-resumes/{tailored['tailored_resume_id']}")
check(resp_v1_still_there.status_code == 200, "B: the original v1 tailored resume is still independently readable after v2 was created")

# --- download / plain-text export ---
resp = client.get(f"/api/candidates/{candidate_a}/tailored-resumes/{tailored['tailored_resume_id']}/download")
check(resp.status_code == 200 and "Kubernetes" in resp.text, "export: plain-text download contains real, expected content")

# --- T. candidate isolation ---
resp = client.get(f"/api/candidates/{candidate_b}/tailored-resumes/{tailored['tailored_resume_id']}")
check(resp.status_code == 404, f"T: candidate B cannot read candidate A's tailored resume by ID, got {resp.status_code}")

# --- eligibility gate: cannot tailor for an ineligible job ---
conn = db_mod.get_conn()
conn.execute(
    "INSERT INTO jobs (job_id, source, company, title, location, work_model, jd_text, experience_required, mandatory_skills, preferred_skills, discovered_at, last_updated) "
    "VALUES ('job-tailor-ineligible', 'TEST', 'TestCo', 'Intern SRE', 'Antarctica', 'Onsite', 'Entry level only.', '0-1 years', '[]', '[]', datetime('now'), datetime('now'))"
)
conn.commit()
conn.close()
resp = client.post(f"/api/candidates/{candidate_a}/jobs/job-tailor-ineligible/tailor-resume", json={"base_resume_id": resume_a, "job_id": "job-tailor-ineligible"})
check(resp.status_code == 404, f"eligibility gate: tailoring refused for a job this candidate isn't eligible for, got {resp.status_code} {resp.text[:150]}")

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
