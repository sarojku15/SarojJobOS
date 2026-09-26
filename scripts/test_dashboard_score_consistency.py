#!/usr/bin/env python3
"""
Regression test for a real bug found via a live end-to-end run
(2026-09-26/27): a saved search's own target_roles/target_locations
(frozen into its query-plan snapshot at submission time) are what
actually discovered a job, but THREE separate code paths independently
re-derive the "legacy matching profile" used to score/explain that job
-- and only two of them applied candidate_profile.apply_search_target_
override() to make that search's own target_roles/locations reach
scoring:

  (a) scripts/search_worker.py's process_queue_item() -- the initial
      run that persists candidate_job_matches/candidate_job_search_
      matches.
  (b) api/results_store.py's get_results_for_saved_search() -- the
      live-rescore-on-view path GET /api/searches/{id}/results uses.
  (c) api/results_store.py's get_dashboard_summary() -- was MISSING
      the override entirely until this fix, silently scoring every
      job against the candidate's PROFILE-level job_preferences.
      target_roles/target_locations instead (commonly empty), losing
      "Core role alignment" (20pts) + "Location" (5pts) = 25 points
      on the dashboard's own aggregate numbers while the results page
      for the exact same search/job showed the correct, higher score.

This test proves all three paths now agree, using a candidate whose
PROFILE-level job_preferences.target_roles/target_locations are
deliberately left empty (the common, real-world case) and a single
saved search whose OWN target_roles/target_locations are what actually
match the fake-discovered job -- exactly the scenario that exposed the
bug. Fully offline (fake adapter, isolated temp DB, no live network
calls); the production DB is never opened.
"""
import hashlib
import sys
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


class _FakeAdapter(JobSourceAdapter):
    name = "FAKE_DASHBOARD_CONSISTENCY_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [{
            "source": self.name,
            "job_id": "FAKE-DASHBOARD-CONSISTENCY-JOB",
            "company": "Nexthink",
            "title": "Senior Site Reliability Engineer",
            "location": "Pune",
            "work_model": "Onsite",
            "job_url": "https://example.com/jobs/dashboard-consistency",
            "application_url": "https://example.com/apply/dashboard-consistency",
            "posted_date": "2026-09-26",
            "jd_text": "AWS Kubernetes Terraform Jenkins Prometheus SLO incident management production reliability",
            "experience_required": "8+ years",
            "mandatory_skills": [],
            "preferred_skills": [],
        }]


source_registry.ADAPTERS["FAKE_DASHBOARD_CONSISTENCY_SOURCE"] = _FakeAdapter
_orig_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_DASHBOARD_CONSISTENCY_SOURCE"]

try:
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_dashboard_consistency_"))
    tmp_db = tmp_dir / "jobos.db"
    import init_dev_db
    init_dev_db.init_dev_db(tmp_db)
    import db as db_mod
    db_mod.DEV_DB = tmp_db
    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db

    client = TestClient(api_main.app)

    resp = client.post("/api/candidates", json={"name": "Dashboard Consistency Test"})
    candidate_id = resp.json()["candidate_id"]

    # Deliberately NO target_roles/target_locations at the profile
    # level -- the common real-world case (these are normally a
    # per-search choice, not resume content) and exactly what exposed
    # the bug: with an empty profile-level target_roles, role_alignment_
    # score() cannot match ANY job, and an empty target_locations can't
    # match any non-remote job, unless the search's own override reaches
    # scoring.
    client.put(f"/api/candidates/{candidate_id}/profile", json={
        "current_title": "Senior Site Reliability Engineer",
        "total_experience_years": 9,
        "skills": {
            "cloud": [{"name": "AWS"}],
            "containers_orchestration": [{"name": "Kubernetes"}],
            "infrastructure_iac": [{"name": "Terraform"}],
            "cicd": [{"name": "Jenkins"}],
            "observability": [{"name": "Prometheus"}],
        },
        "job_preferences": {"target_roles": [], "target_locations": []},
    })
    client.post(f"/api/candidates/{candidate_id}/profile/confirm")

    resp = client.post(f"/api/candidates/{candidate_id}/searches", json={
        "name": "Dashboard consistency search",
        "target_roles": ["Senior Site Reliability Engineer"],
        "target_locations": ["Pune"],
        "minimum_match_score": 0,
        "sources": ["FAKE_DASHBOARD_CONSISTENCY_SOURCE"],
    })
    search_id = resp.json()["saved_search_id"]

    resp = client.post(f"/api/searches/{search_id}/run?candidate_id={candidate_id}")
    run_id = resp.json()["run_id"]
    deadline = time.time() + 20
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}?candidate_id={candidate_id}")
        if r.json()["status"] in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED"):
            break
        time.sleep(0.2)
    check(r.json()["status"] == "COMPLETED", f"setup: run completes, got {r.json()['status']}")

    # (a) the score search_worker.py actually persisted
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    row = conn.execute(
        "SELECT fit_score FROM candidate_job_matches WHERE candidate_id=? AND job_id=?",
        (candidate_id, "FAKE-DASHBOARD-CONSISTENCY-JOB"),
    ).fetchone()
    conn.close()
    check(row is not None, "setup: persisted candidate_job_matches row exists")
    persisted_score = row[0] if row else None

    # (b) the results endpoint's live rescore
    resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
    results = resp.json()["results"]
    check(len(results) == 1, f"setup: exactly one result, got {len(results)}")
    results_score = results[0]["score"] if results else None

    # (c) the dashboard's own aggregate
    resp = client.get(f"/api/candidates/{candidate_id}/dashboard")
    dash = resp.json()["summary"]
    check(dash["total_jobs_in_scope"] == 1, f"setup: dashboard shows exactly 1 job in scope, got {dash['total_jobs_in_scope']}")
    dashboard_score = dash.get("score_max")

    print(f"persisted (search_worker)={persisted_score}  results_endpoint={results_score}  dashboard={dashboard_score}")

    check(persisted_score is not None and results_score is not None and dashboard_score is not None,
          "setup: all three scores were actually retrieved (not None)")

    check(results_score == persisted_score,
          f"results endpoint agrees with what search_worker persisted: {results_score} == {persisted_score}")
    check(dashboard_score == persisted_score,
          f"dashboard summary agrees with what search_worker persisted: {dashboard_score} == {persisted_score}")
    check(abs((dashboard_score or 0) - (results_score or 0)) < 25,
          f"the three paths must not disagree by 25 points (the exact size of the bug this regresses): "
          f"dashboard={dashboard_score} results={results_score}")

    # Both role alignment and location must have actually been credited
    # -- proving the override reached scoring, not just that scores
    # happen to match by coincidence.
    check("Core role alignment" in results[0]["matched_skills"],
          f"results: 'Core role alignment' credited despite empty profile-level target_roles, matched={results[0]['matched_skills']}")
    check("Location" in results[0]["matched_skills"],
          f"results: 'Location' credited despite empty profile-level target_locations, matched={results[0]['matched_skills']}")

finally:
    source_registry.list_sources = _orig_list_sources

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
