#!/usr/bin/env python3

"""
Regression test for item 5's exact required scenario: the SAME global
job independently matched by TWO DIFFERENT searches for the SAME
candidate (Candidate A / Search 1 -> Job X -> Resume A / Search 2 ->
Job X -> Resume B) must never let one search's results silently lose
that job, or show the other search's resume/score, once the schema
fix (migrate_v9_candidate_job_search_matches.py) is in place.

This is the exact scenario a prior turn's audit found BROKEN before
this fix (documented in this project's own history): Search 2's write
to the single candidate_job_matches row overwrote Search 1's
search_run_id, so Search 1's OWN results view lost Job X entirely.

Expected (per the master task's explicit acceptance criteria):
  - Search 1 still shows Job X associated with Search 1 (in its
    results, with run_id == Search 1's own run).
  - Search 2 still shows Job X associated with Search 2.
  - Both retain correct profile/resume traceability (different scores,
    different resume_variant, matching each search's own pin).

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


_SHARED_JD = "Senior Cloud Platform Engineer role. kubernetes terraform ci/cd monitoring observability."


class _SameJobAdapter(JobSourceAdapter):
    """Both Search 1 and Search 2 use THIS SAME adapter/source, and it
    always returns the SAME job_id -- the exact collision scenario."""

    name = "FAKE_SAMEJOB_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [
            {
                "source": self.name, "job_id": "FAKE-SAMEJOB-X", "company": "SameJobCo",
                "title": "Job X", "location": query.location, "work_model": "Remote",
                "job_url": "https://example.com/jobs/same-job-x", "application_url": "https://example.com/apply/same-job-x",
                "posted_date": "2026-09-20", "jd_text": _SHARED_JD, "experience_required": "3+ years",
                "mandatory_skills": [], "preferred_skills": [],
            }
        ]


source_registry.ADAPTERS["FAKE_SAMEJOB_SOURCE"] = _SameJobAdapter
_original_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_SAMEJOB_SOURCE"]

try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_samejob_"))
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

    resp = client.post("/api/candidates", json={"name": "Same-Job Isolation Candidate"})
    candidate_id = resp.json()["candidate_id"]

    def _upload_and_confirm(filename, skills, resume_bytes):
        conn = db_mod.get_conn()
        resume_id = resume_store.record_uploaded_resume(conn, candidate_id, filename, resume_bytes, f"/fake/{filename}", status="PARSED")
        conn.close()
        client.put(
            f"/api/candidates/{candidate_id}/profile",
            json={
                "current_title": "Senior Cloud Platform Engineer", "total_experience_years": 6, "skills": skills,
                "job_preferences": {"target_roles": ["Senior Cloud Platform Engineer"], "target_locations": ["Remote"]},
            },
        )
        client.post(f"/api/candidates/{candidate_id}/profile/confirm")
        conn = db_mod.get_conn()
        version = conn.execute("SELECT version FROM candidate_search_profile WHERE candidate_id=? AND is_active=1", (candidate_id,)).fetchone()[0]
        conn.execute("UPDATE candidate_search_profile SET source_resume_id=? WHERE candidate_id=? AND version=?", (resume_id, candidate_id, version))
        conn.commit(); conn.close()
        return resume_id, version

    resume_id_a, version_a = _upload_and_confirm(
        "SameJobResumeA.pdf",
        {"containers_orchestration": [{"name": "Kubernetes"}], "infrastructure_iac": [{"name": "Terraform"}]},
        b"resume A -- strong k8s/iac match",
    )
    resume_id_b, version_b = _upload_and_confirm(
        "SameJobResumeB.pdf",
        {},
        b"resume B -- no matching skills at all",
    )

    def _create_search(name, profile_version):
        resp = client.post(
            f"/api/candidates/{candidate_id}/searches",
            json={
                "name": name, "target_roles": ["Senior Cloud Platform Engineer"], "target_locations": ["Remote"],
                "minimum_match_score": 0, "sources": ["FAKE_SAMEJOB_SOURCE"], "profile_version": profile_version,
            },
        )
        return resp.json()["saved_search_id"]

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

    # --- Search 1 -> Job X -> Resume A ---
    search_1_id = _create_search("Search 1 (Resume A)", version_a)
    run_1_id, status_1 = _run_and_wait(search_1_id)
    check(status_1 == "COMPLETED", f"Search 1 run completes, got {status_1}")

    # --- Search 2 -> Job X (SAME job) -> Resume B ---
    search_2_id = _create_search("Search 2 (Resume B)", version_b)
    run_2_id, status_2 = _run_and_wait(search_2_id)
    check(status_2 == "COMPLETED", f"Search 2 run completes, got {status_2}")
    check(run_1_id != run_2_id, "setup: Search 1 and Search 2 have genuinely different run_ids")

    # --- Expected: Search 1 still shows Job X, associated with Search 1 ---
    resp_1 = client.get(f"/api/searches/{search_1_id}/results?candidate_id={candidate_id}")
    body_1 = resp_1.json()
    check(len(body_1["results"]) == 1, f"Search 1 STILL shows Job X in its own results (not lost to Search 2's later write), got {len(body_1['results'])} results")
    check(body_1["run_id"] == run_1_id, f"Search 1's results correctly report run_id == Search 1's own run ({run_1_id}), got {body_1.get('run_id')}")

    # --- Expected: Search 2 still shows Job X, associated with Search 2 ---
    resp_2 = client.get(f"/api/searches/{search_2_id}/results?candidate_id={candidate_id}")
    body_2 = resp_2.json()
    check(len(body_2["results"]) == 1, f"Search 2 shows Job X in its own results, got {len(body_2['results'])} results")
    check(body_2["run_id"] == run_2_id, f"Search 2's results correctly report run_id == Search 2's own run ({run_2_id}), got {body_2.get('run_id')}")

    check(body_1["results"][0]["job_id"] == body_2["results"][0]["job_id"], "setup: confirmed -- it's genuinely the SAME global job_id in both searches' results")

    # --- Expected: both retain correct, DIFFERENT profile/resume traceability ---
    score_1 = body_1["results"][0]["score"]
    score_2 = body_2["results"][0]["score"]
    variant_1 = body_1["results"][0]["resume_variant"]
    variant_2 = body_2["results"][0]["resume_variant"]

    check(score_1 > score_2, f"SAFETY: Search 1 (Resume A, real skills) scores the SAME job HIGHER than Search 2 (Resume B, no skills) -- got Search1={score_1}, Search2={score_2}")
    check(variant_1 == "SameJobResumeA", f"Search 1's resume_variant correctly shows Resume A's own filename, got {variant_1!r}")
    check(variant_2 == "SameJobResumeB", f"Search 2's resume_variant correctly shows Resume B's own filename, got {variant_2!r}")
    check(variant_1 != variant_2, "SAFETY: the SAME job shows DIFFERENT resume_variant depending on which search's own pin is viewed")

    # --- Direct DB check: the new per-run table holds BOTH rows independently ---
    conn = db_mod.get_conn()
    conn.row_factory = __import__("sqlite3").Row
    scoped_rows = conn.execute(
        "SELECT search_run_id, resume_id, profile_version, fit_score FROM candidate_job_search_matches "
        "WHERE candidate_id = ? AND job_id = ? ORDER BY search_run_id",
        (candidate_id, body_1["results"][0]["job_id"]),
    ).fetchall()
    conn.close()
    check(len(scoped_rows) == 2, f"DB: candidate_job_search_matches holds 2 INDEPENDENT rows for this (candidate, job) -- one per run, got {len(scoped_rows)}")
    scoped_by_run = {r["search_run_id"]: dict(r) for r in scoped_rows}
    check(scoped_by_run.get(run_1_id, {}).get("resume_id") == resume_id_a, "DB: Search 1's own row in the new table still correctly shows Resume A")
    check(scoped_by_run.get(run_2_id, {}).get("resume_id") == resume_id_b, "DB: Search 2's own row in the new table still correctly shows Resume B")

    # --- The candidate-wide single-row table (dashboard/Excel source)
    #     correctly reflects the MOST RECENT write (Search 2, run 2) --
    #     this is intentional, unchanged "latest snapshot" behavior,
    #     never claimed to be per-search. ---
    conn = db_mod.get_conn()
    conn.row_factory = __import__("sqlite3").Row
    global_row = conn.execute(
        "SELECT search_run_id, resume_id FROM candidate_job_matches WHERE candidate_id = ? AND job_id = ?",
        (candidate_id, body_1["results"][0]["job_id"]),
    ).fetchone()
    conn.close()
    check(global_row["search_run_id"] == run_2_id, "DB: the candidate-wide 'latest snapshot' table (dashboard/Excel) shows the most recent run (Search 2), as intended -- unchanged behavior")

finally:
    source_registry.list_sources = _original_list_sources

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
