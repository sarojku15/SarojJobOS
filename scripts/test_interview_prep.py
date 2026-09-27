#!/usr/bin/env python3
"""
Phase 3 regression tests: interview preparation
(scripts/interview_prep.py, migrate_v13_interview_prep.py,
api/main.py's interview-prep routes).

Covers (Phase 13 checklist):
  H. generation/storage/regeneration (a real question bank persisted,
     categories present, regeneration replaces questions in place
     rather than creating a duplicate row)
  I. candidate isolation
  plus the "never fabricate" honesty rule specific to this feature:
  real resume evidence produces a real quoted model answer; no
  evidence produces the literal "Preparation required" string --
  never an invented claim about the candidate.

Fully offline, isolated temp DB, no live network/search-provider call
(company-research linkage is exercised via a directly-inserted DB row,
matching this module's own "only if company_research already exists"
contract). Production DB verified byte-identical before/after.
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


tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_interview_prep_"))
tmp_db = tmp_dir / "jobos.db"
import init_dev_db
init_dev_db.init_dev_db(tmp_db)
import db as db_mod
db_mod.DEV_DB = tmp_db
from fastapi.testclient import TestClient
import main as api_main
api_main.db_mod.DEV_DB = tmp_db

client = TestClient(api_main.app)

resp = client.post("/api/candidates", json={"name": "Interview Prep Test A"})
candidate_a = resp.json()["candidate_id"]
resp = client.post("/api/candidates", json={"name": "Interview Prep Test B"})
candidate_b = resp.json()["candidate_id"]

client.put(f"/api/candidates/{candidate_a}/profile", json={
    "current_title": "SRE", "total_experience_years": 9,
    "skills": {
        "containers_orchestration": [{"name": "Kubernetes"}],
        "cloud": [{"name": "AWS"}],
    },
    "employment_history": [
        {"employer": "Acme", "title": "Senior SRE", "start_date": "Jan 2020", "end_date": "Present",
         "description": "Deployed and scaled Kubernetes clusters across AWS for production workloads.",
         "skills": ["Kubernetes", "AWS"]},
    ],
    "job_preferences": {"target_roles": ["SRE"], "target_locations": ["Remote"]},
})
client.post(f"/api/candidates/{candidate_a}/profile/confirm")

conn = db_mod.get_conn()
conn.execute(
    "INSERT INTO jobs (job_id, source, company, title, location, work_model, jd_text, experience_required, mandatory_skills, preferred_skills, discovered_at, last_updated) "
    "VALUES ('job-prep-1', 'TEST', 'Acme Corp', 'Senior SRE', 'Remote', 'Remote', 'Kubernetes, AWS, and Terraform experience required.', '5+ years', '[]', '[]', datetime('now'), datetime('now'))"
)
company_research_id = "research_fake_prep_1"
conn.execute(
    "INSERT INTO company_research (company_research_id, candidate_id, job_id, company_name, version, status, website, description, recent_info_json, source_urls_json, researched_at) "
    "VALUES (?, ?, 'job-prep-1', 'Acme Corp', 1, 'SUCCESS', 'https://acmecorp.com', 'Acme Corp builds reliable infrastructure.', '[]', '[]', datetime('now'))",
    (company_research_id, candidate_a),
)
conn.commit()
conn.close()

import resume_store
conn = db_mod.get_conn()
resume_b = resume_store.record_uploaded_resume(
    conn, candidate_b, "resume_b.pdf", b"fake pdf bytes for candidate B", "/fake/b.pdf", status="PARSED"
)
conn.close()

# --- resume ownership: candidate A cannot attach candidate B's resume_id ---
resp = client.post(f"/api/candidates/{candidate_a}/interview-prep", json={"job_id": "job-prep-1", "resume_id": resume_b})
check(resp.status_code == 404, f"resume ownership: attaching another candidate's resume_id is refused, got {resp.status_code} {resp.text[:150]}")

# --- H: generation/storage via the real API ---
resp = client.post(f"/api/candidates/{candidate_a}/interview-prep", json={"job_id": "job-prep-1", "company_research_id": company_research_id})
check(resp.status_code == 200, f"H: interview-prep API succeeds, got {resp.status_code} {resp.text[:200]}")
prep = resp.json()
check(prep["status"] == "READY", f"H: status is READY, got {prep['status']}")
categories = {q["category"] for q in prep["questions"]}
check({"kubernetes", "behavioral", "leadership", "resume"}.issubset(categories), f"H: expected question categories present, got {categories}")
check("company_specific" in categories, "H: company_specific questions generated since a real company_research record was linked")

# --- honesty: real evidence -> real quoted answer; no evidence -> honest fallback ---
k8s_questions = [q for q in prep["questions"] if q["category"] == "kubernetes"]
check(all(q["has_demonstrated_experience"] for q in k8s_questions), "honesty: Kubernetes questions correctly flagged as having demonstrated evidence")
check(all("Acme" in q["model_answer_text"] for q in k8s_questions), "honesty: Kubernetes model answers quote the real employer (Acme) from the resume")

terraform_questions = [q for q in prep["questions"] if q["category"] == "terraform_iac"]
check(len(terraform_questions) > 0, "sanity: terraform_iac category exists in the question bank")
check(all(not q["has_demonstrated_experience"] for q in terraform_questions), "honesty: Terraform questions correctly flagged as NOT demonstrated (candidate has no Terraform experience)")
check(all(q["model_answer_text"] == "Preparation required -- no demonstrated experience found." for q in terraform_questions), f"honesty: Terraform model answer is the literal honest fallback string, got {terraform_questions[0]['model_answer_text']!r}")

company_questions = [q for q in prep["questions"] if q["category"] == "company_specific"]
check(any("Acme Corp builds reliable infrastructure." in q["model_answer_text"] for q in company_questions), "honesty: company-specific answer quotes the real company_research description verbatim")

# --- association: job_id / GET-by-job ---
resp = client.get(f"/api/candidates/{candidate_a}/jobs/job-prep-1/interview-prep")
check(resp.status_code == 200 and resp.json()["interview_prep_id"] == prep["interview_prep_id"], "association: GET by job_id returns the same prep record")

# --- H: regeneration replaces questions in place (same row, not a duplicate) ---
first_question_ids = {q["question_id"] for q in prep["questions"]}
resp2 = client.post(f"/api/candidates/{candidate_a}/interview-prep", json={"job_id": "job-prep-1", "company_research_id": company_research_id})
prep2 = resp2.json()
check(prep2["interview_prep_id"] == prep["interview_prep_id"], "H: regenerating for the same (candidate, job) reuses the SAME interview_prep_id, never a duplicate")
second_question_ids = {q["question_id"] for q in prep2["questions"]}
check(first_question_ids.isdisjoint(second_question_ids), "H: regeneration replaces the question set (old question rows deleted, fresh ones inserted)")
check(len(prep2["questions"]) == len(prep["questions"]), f"H: regenerated question count matches original, got {len(prep2['questions'])} vs {len(prep['questions'])}")

# --- candidate answers: update + persist ---
target_question_id = prep2["questions"][0]["question_id"]
resp = client.patch(f"/api/candidates/{candidate_a}/interview-prep-questions/{target_question_id}", json={"candidate_answer": "My real answer.", "confidence": 4, "notes": "felt good"})
check(resp.status_code == 200, f"answers: PATCH candidate answer succeeds, got {resp.status_code}")
resp = client.get(f"/api/candidates/{candidate_a}/interview-prep/{prep2['interview_prep_id']}")
saved_q = next(q for q in resp.json()["questions"] if q["question_id"] == target_question_id)
check(saved_q["candidate_answer_text"] == "My real answer." and saved_q["confidence"] == 4, "answers: candidate's own answer/confidence persisted correctly")
check(saved_q["model_answer_text"] not in (None, ""), "answers: model_answer_text untouched by the candidate-answer PATCH")

# --- outcome tracking ---
resp = client.patch(f"/api/candidates/{candidate_a}/interview-prep/{prep2['interview_prep_id']}/outcome", json={"outcome_status": "SCHEDULED", "outcome_notes": "Loop on Monday"})
check(resp.status_code == 200, f"outcome: PATCH outcome succeeds, got {resp.status_code}")
resp = client.get(f"/api/candidates/{candidate_a}/interview-prep/{prep2['interview_prep_id']}")
check(resp.json()["outcome_status"] == "SCHEDULED", "outcome: outcome_status persisted correctly")

# --- I: candidate isolation ---
resp = client.get(f"/api/candidates/{candidate_b}/interview-prep/{prep2['interview_prep_id']}")
check(resp.status_code == 404, f"I: candidate B cannot read candidate A's interview prep by ID, got {resp.status_code}")
resp = client.patch(f"/api/candidates/{candidate_b}/interview-prep-questions/{target_question_id}", json={"candidate_answer": "hijacked"})
check(resp.status_code == 404, f"I: candidate B cannot write an answer onto candidate A's question via a guessed question_id, got {resp.status_code}")
resp = client.get(f"/api/candidates/{candidate_b}/jobs/job-prep-1/interview-prep")
check(resp.status_code == 404, f"I: candidate B has no interview prep of their own for this job, got {resp.status_code}")

# --- eligibility gate ---
conn = db_mod.get_conn()
conn.execute(
    "INSERT INTO jobs (job_id, source, company, title, location, work_model, jd_text, experience_required, mandatory_skills, preferred_skills, discovered_at, last_updated) "
    "VALUES ('job-prep-ineligible', 'TEST', 'TestCo', 'Intern SRE', 'Antarctica', 'Onsite', 'Entry level only.', '0-1 years', '[]', '[]', datetime('now'), datetime('now'))"
)
conn.commit()
conn.close()
resp = client.post(f"/api/candidates/{candidate_a}/interview-prep", json={"job_id": "job-prep-ineligible"})
check(resp.status_code == 404, f"eligibility gate: interview prep refused for an ineligible job, got {resp.status_code} {resp.text[:150]}")

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
