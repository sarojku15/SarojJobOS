#!/usr/bin/env python3
"""
External-user-readiness multi-user isolation test: USER_A and USER_B,
completely separate resumes/roles/locations, isolated temp DB, fake
adapters (deterministic, fast). Never opens production/dev DB.

Complements (does not duplicate) scripts/test_candidate_ownership_
isolation.py (which proves ID-guessing can't cross candidates) by also
proving: different resumes stay separate, dashboards are candidate-
scoped, and Excel exports are candidate-scoped -- the exact "can a
friend use this without seeing my data" acceptance criteria.
"""
import hashlib
import json
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


class _AdapterA(JobSourceAdapter):
    name = "FAKE_MULTIUSER_SOURCE"
    status = AdapterStatus.ENABLED
    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)
    def search(self, query):
        return [{
            "source": self.name, "job_id": "FAKE-MULTIUSER-JOB-A", "company": "CloudCo",
            "title": query.role, "location": query.location, "work_model": "Remote",
            "job_url": "https://example.com/jobs/multiuser-a", "application_url": "https://example.com/apply/multiuser-a",
            "posted_date": "2026-09-20", "jd_text": "AWS kubernetes terraform devops role.",
            "experience_required": "3+ years", "mandatory_skills": [], "preferred_skills": [],
        }]


class _AdapterB(JobSourceAdapter):
    name = "FAKE_MULTIUSER_SOURCE"
    status = AdapterStatus.ENABLED
    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)
    def search(self, query):
        return [{
            "source": self.name, "job_id": "FAKE-MULTIUSER-JOB-B", "company": "DataCo",
            "title": query.role, "location": query.location, "work_model": "Hybrid",
            "job_url": "https://example.com/jobs/multiuser-b", "application_url": "https://example.com/apply/multiuser-b",
            "posted_date": "2026-09-20", "jd_text": "Azure data engineering pipeline role.",
            "experience_required": "5+ years", "mandatory_skills": [], "preferred_skills": [],
        }]


source_registry.ADAPTERS["FAKE_MULTIUSER_SOURCE"] = _AdapterA
_orig = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_MULTIUSER_SOURCE"]

try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_friend_multiuser_"))
    tmp_db = tmp_dir / "jobos_multiuser.db"
    import init_dev_db
    init_dev_db.init_dev_db(tmp_db)
    import db as db_mod
    db_mod.DEV_DB = tmp_db
    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db
    import resume_store

    client = TestClient(api_main.app)

    # --- USER_A: different resume, role, location ---
    resp = client.post("/api/candidates", json={"name": "User A"})
    cand_a = resp.json()["candidate_id"]
    conn = db_mod.get_conn()
    resume_a = resume_store.record_uploaded_resume(conn, cand_a, "UserA_AWS_DevOps.pdf", b"User A resume content -- AWS/Kubernetes/Terraform", "/fake/a.pdf", status="PARSED")
    conn.close()
    client.put(f"/api/candidates/{cand_a}/profile", json={
        "current_title": "DevOps Engineer", "total_experience_years": 4,
        "skills": {"containers_orchestration": [{"name": "Kubernetes"}], "infrastructure_iac": [{"name": "Terraform"}]},
        "job_preferences": {"target_roles": ["DevOps Engineer"], "target_locations": ["Remote"]},
    })
    client.post(f"/api/candidates/{cand_a}/profile/confirm")

    # --- USER_B: different resume, role, location ---
    resp = client.post("/api/candidates", json={"name": "User B"})
    cand_b = resp.json()["candidate_id"]
    conn = db_mod.get_conn()
    resume_b = resume_store.record_uploaded_resume(conn, cand_b, "UserB_Data_Engineer.pdf", b"User B resume content -- completely different, Azure data engineering", "/fake/b.pdf", status="PARSED")
    conn.close()
    client.put(f"/api/candidates/{cand_b}/profile", json={
        "current_title": "Data Engineer", "total_experience_years": 6,
        "skills": {"cloud": [{"name": "Azure"}]},
        "job_preferences": {"target_roles": ["Data Engineer"], "target_locations": ["Hyderabad"]},
    })
    client.post(f"/api/candidates/{cand_b}/profile/confirm")

    check(cand_a != cand_b, "setup: two genuinely distinct candidates created")
    check(resume_a != resume_b, "setup: two genuinely distinct resumes")

    def run_and_wait(client, search_id, candidate_id):
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

    # Search A (User A's own)
    resp = client.post(f"/api/candidates/{cand_a}/searches", json={
        "name": "User A's search", "target_roles": ["DevOps Engineer"], "target_locations": ["Remote"],
        "minimum_match_score": 0, "sources": ["FAKE_MULTIUSER_SOURCE"],
    })
    search_a = resp.json()["saved_search_id"]
    source_registry.ADAPTERS["FAKE_MULTIUSER_SOURCE"] = _AdapterA
    run_a, status_a = run_and_wait(client, search_a, cand_a)
    check(status_a == "COMPLETED", f"User A's search run completes, got {status_a}")

    # Search B (User B's own)
    resp = client.post(f"/api/candidates/{cand_b}/searches", json={
        "name": "User B's search", "target_roles": ["Data Engineer"], "target_locations": ["Hyderabad"],
        "minimum_match_score": 0, "sources": ["FAKE_MULTIUSER_SOURCE"],
    })
    search_b = resp.json()["saved_search_id"]
    source_registry.ADAPTERS["FAKE_MULTIUSER_SOURCE"] = _AdapterB
    run_b, status_b = run_and_wait(client, search_b, cand_b)
    check(status_b == "COMPLETED", f"User B's search run completes, got {status_b}")

    # --- Candidate A cannot see candidate B's searches ---
    resp = client.get(f"/api/candidates/{cand_a}/searches")
    a_search_names = {s["name"] for s in resp.json()["searches"]}
    check("User A's search" in a_search_names, "A sees A's own search")
    check("User B's search" not in a_search_names, "A CANNOT see B's search in A's own list")

    resp = client.get(f"/api/candidates/{cand_b}/searches")
    b_search_names = {s["name"] for s in resp.json()["searches"]}
    check("User B's search" in b_search_names, "B sees B's own search")
    check("User A's search" not in b_search_names, "B CANNOT see A's search in B's own list")

    # --- Candidate A cannot access B's search/results/report by ID (ownership) ---
    resp = client.get(f"/api/searches/{search_b}?candidate_id={cand_a}")
    check(resp.status_code == 404, f"A cannot GET B's search by ID (guessing search_b's id), got {resp.status_code}")
    resp = client.get(f"/api/searches/{search_b}/results?candidate_id={cand_a}")
    check(resp.status_code == 404, f"A cannot read B's results by ID, got {resp.status_code}")
    resp = client.get(f"/api/searches/{search_b}/report?candidate_id={cand_a}")
    check(resp.status_code == 404, f"A cannot download B's export by ID, got {resp.status_code}")

    # --- Candidate A cannot see candidate B's resumes ---
    resp = client.get(f"/api/candidates/{cand_a}/resumes")
    a_resume_ids = {r["resume_id"] for r in resp.json()["resumes"]}
    check(resume_a in a_resume_ids, "A sees A's own resume")
    check(resume_b not in a_resume_ids, "A CANNOT see B's resume in A's own resume list")

    # --- Candidate A cannot see candidate B's application status ---
    resp = client.get(f"/api/searches/{search_a}/results?candidate_id={cand_a}")
    job_id_a = resp.json()["results"][0]["job_id"]
    client.patch(f"/api/candidates/{cand_a}/jobs/{job_id_a}/status", json={"status": "SHORTLISTED"})

    resp = client.get(f"/api/searches/{search_b}/results?candidate_id={cand_b}")
    job_id_b = resp.json()["results"][0]["job_id"]
    resp = client.patch(f"/api/candidates/{cand_a}/jobs/{job_id_b}/status", json={"status": "SHORTLISTED"})
    check(resp.status_code == 404, f"A cannot change B's job status even by guessing B's own job_id, got {resp.status_code}")

    # --- Dashboard totals are candidate-scoped ---
    resp = client.get(f"/api/candidates/{cand_a}/dashboard")
    dash_a = resp.json()
    resp = client.get(f"/api/candidates/{cand_b}/dashboard")
    dash_b = resp.json()
    check(dash_a["summary"]["total_jobs_in_scope"] == 1, f"A's dashboard shows exactly A's own 1 job, got {dash_a['summary']['total_jobs_in_scope']}")
    check(dash_b["summary"]["total_jobs_in_scope"] == 1, f"B's dashboard shows exactly B's own 1 job, got {dash_b['summary']['total_jobs_in_scope']}")
    a_dash_search_names = {s["name"] for s in dash_a["searches"]}
    check("User B's search" not in a_dash_search_names, "A's dashboard never shows B's search")

    # --- Exports are candidate-scoped ---
    resp = client.get(f"/api/searches/{search_a}/report?candidate_id={cand_a}")
    check(resp.status_code == 200, f"A's own export succeeds, got {resp.status_code}")
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(resp.content))
    exported_ids = set()
    for sheet_name in ["ALL_MATCHING_JOBS", "REJECTED_EXCLUDED", "ALREADY_APPLIED"]:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        jid_col = next((i for i, h in enumerate(header) if h and "job" in str(h).lower() and "id" in str(h).lower()), None)
        if jid_col is None:
            continue
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[jid_col]:
                exported_ids.add(row[jid_col])
    check(exported_ids == {job_id_a}, f"A's export contains ONLY A's own job, never B's, got {exported_ids}")

finally:
    source_registry.list_sources = _orig

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
