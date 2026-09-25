#!/usr/bin/env python3

"""
Regression test for the results/source-audit run-consistency contract
requested after a real reported inconsistency ("Jobs Found: 332,
Eligible: 332" next to "every source = NOT_ATTEMPTED, Raw=0").

The ACTUAL root cause of that specific report was a migration-drift
bug (see test_dev_db_migration_drift.py / api/db.py's ensure_dev_db()
fix): search_run_sources silently didn't exist on the real dev DB, so
every source fell back to a synthesized NOT_ATTEMPTED placeholder on
every run, while results kept populating fine via the unrelated
candidate_job_matches path.

This file covers the SEPARATE, complementary contract fix requested
alongside that root-cause fix: GET /api/searches/{id}/results now
returns an explicit run_id (api/search_store.get_latest_run_id_for_
search()), and the "sources" audit in that same response is always
for that EXACT run_id -- never a different run's audit silently
sitting next to this run's (or an earlier run's) results.

Note on intentional, NOT changed, semantics: a saved search's
`results` are cumulative across every run ever tied to it (this is
what powers the existing "New Jobs" tracking -- see
generate_run_report.py's `since`/previously_seen handling) -- this is
pre-existing, tested, intentional behavior, not something this fix
redesigns. What changed is that the response now says EXACTLY which
run's source audit is shown, so a caller can never mistake one run's
audit for another's.

Covers:
  A. Run 1 (an older run) produces real results.
  B. Run 2 (the latest run for the same saved search) is BLOCKED/fails
     -- produces zero NEW matches.
  C/D. The results page's returned "sources" always reflects Run 2 (the
     latest run) -- Run 1's results (still legitimately present in the
     cumulative result set) are never silently paired with Run 1's own
     audit instead of the current run's.
  E. The returned per-source counts in "sources" exactly equal what is
     actually stored in search_run_sources for that same run_id --
     never a stale/independently-recomputed number.
  F. The API's returned run_id equals search_store.
     get_latest_run_id_for_search()'s own independent result -- the
     single canonical resolution point, never two computations that
     could drift.

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
from source_adapter import JobSourceAdapter, AdapterHealth, AdapterStatus, BlockReason, AdapterBlockedError

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


# Run 1: succeeds, returns 2 real jobs.
class _RunOneAdapter(JobSourceAdapter):
    name = "FAKE_RUNCONSISTENCY_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [
            {
                "source": self.name, "job_id": f"FAKE-RUNCONSISTENCY-{i}", "company": "RunConsistencyCo",
                "title": query.role, "location": query.location, "work_model": "Hybrid",
                "job_url": f"https://example.com/jobs/runconsistency-{i}",
                "application_url": f"https://example.com/apply/runconsistency-{i}",
                "posted_date": "2026-09-20",
                "jd_text": f"{query.role} role requiring strong skills.",
                "experience_required": "5+ years",
                "mandatory_skills": [], "preferred_skills": [],
            }
            for i in (1, 2)
        ]


# Run 2: blocked -- produces zero new jobs, its own audit row is BLOCKED.
class _RunTwoBlockedAdapter(JobSourceAdapter):
    name = "FAKE_RUNCONSISTENCY_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        raise AdapterBlockedError(self.name, BlockReason.CAPTCHA, detail="simulated CAPTCHA for run 2")


source_registry.ADAPTERS["FAKE_RUNCONSISTENCY_SOURCE"] = _RunOneAdapter
_original_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_RUNCONSISTENCY_SOURCE"]

try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_run_consistency_"))
    tmp_db = tmp_dir / "jobos_test.db"

    import init_dev_db
    init_dev_db.init_dev_db(tmp_db)

    import db as db_mod
    db_mod.DEV_DB = tmp_db

    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db
    import search_store

    client = TestClient(api_main.app)

    resp = client.post("/api/candidates", json={"name": "Run Consistency Candidate"})
    candidate_id = resp.json()["candidate_id"]
    client.put(
        f"/api/candidates/{candidate_id}/profile",
        json={
            "current_title": "Run Consistency Role",
            "total_experience_years": 5,
            "skills": {"other": [{"name": "Generic Skill"}]},
            "job_preferences": {"target_roles": ["Run Consistency Role"], "target_locations": ["Remote"]},
        },
    )
    client.post(f"/api/candidates/{candidate_id}/profile/confirm")

    resp = client.post(
        f"/api/candidates/{candidate_id}/searches",
        json={
            "name": "run consistency search",
            "target_roles": ["Run Consistency Role"],
            "target_locations": ["Remote"],
            "minimum_match_score": 0,
            "sources": ["FAKE_RUNCONSISTENCY_SOURCE"],
        },
    )
    search_id = resp.json()["saved_search_id"]

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

    # --- A. Run 1 succeeds, produces real results ---
    run_1_id, run_1_status = _run_and_wait()
    check(run_1_status in ("COMPLETED", "PARTIAL"), f"A. Run 1 completes successfully, got {run_1_status}")

    resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
    body_after_run1 = resp.json()
    check(len(body_after_run1["results"]) == 2, f"A. Run 1 produced 2 real results, got {len(body_after_run1['results'])}")
    check(body_after_run1["run_id"] == run_1_id, f"A. results API returns run_id == Run 1's id right after Run 1, got {body_after_run1['run_id']}")
    run1_source_entry = next(s for s in body_after_run1["sources"] if s["source"] == "FAKE_RUNCONSISTENCY_SOURCE")
    check(run1_source_entry["status"] == "SUCCESS", f"A. Run 1's own source audit shows SUCCESS, got {run1_source_entry['status']}")

    # --- B. Run 2 (same saved search) is BLOCKED, produces zero NEW matches ---
    source_registry.ADAPTERS["FAKE_RUNCONSISTENCY_SOURCE"] = _RunTwoBlockedAdapter
    run_2_id, run_2_status = _run_and_wait()
    check(run_2_id != run_1_id, "B. setup: Run 2 has a genuinely different run_id than Run 1")
    check(run_2_status in ("BLOCKED", "PARTIAL", "FAILED"), f"B. Run 2 is correctly BLOCKED/failed/empty, got {run_2_status}")

    # --- C/D. results page's sources MUST reflect Run 2 (the latest
    #     run), never Run 1's SUCCESS silently reused ---
    resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
    body_after_run2 = resp.json()
    check(body_after_run2["run_id"] == run_2_id, f"C/D. results API returns run_id == Run 2's id (the latest run) after Run 2, got {body_after_run2['run_id']}")
    run2_source_entry = next(s for s in body_after_run2["sources"] if s["source"] == "FAKE_RUNCONSISTENCY_SOURCE")
    check(run2_source_entry["status"] == "BLOCKED", f"C/D. SAFETY: after Run 2 (blocked), the source audit shows BLOCKED (Run 2's own truth), NOT Run 1's stale SUCCESS, got {run2_source_entry['status']}")
    check(run2_source_entry["raw_count"] == 0, f"C/D. SAFETY: Run 2's own source audit shows raw_count=0 (Run 2's truth), not Run 1's raw_count, got {run2_source_entry['raw_count']}")

    # Run 1's 2 jobs are still legitimately present in the cumulative
    # result set (intentional "New Jobs" cumulative-tracking behavior,
    # NOT a bug) -- but they must never be silently re-labeled as
    # belonging to Run 2, and Run 2's own (BLOCKED) audit must be what
    # is shown, not Run 1's.
    check(len(body_after_run2["results"]) == 2, f"D. Run 1's 2 results remain visible (cumulative tracking, intentional) even though Run 2 itself found nothing new, got {len(body_after_run2['results'])}")

    # --- E. returned source counts exactly equal search_run_sources
    #     for that exact run_id -- never independently recomputed ---
    conn = db_mod.get_conn()
    real_row = search_store.get_run_sources(conn, run_2_id)
    real_entry = next(s for s in real_row if s["source"] == "FAKE_RUNCONSISTENCY_SOURCE")
    conn.close()
    check(
        run2_source_entry["raw_count"] == real_entry["raw_count"] and run2_source_entry["status"] == real_entry["status"],
        f"E. API-returned source counts exactly match search_run_sources for run_id={run_2_id}: API={run2_source_entry}, DB={real_entry}",
    )

    # --- F. API's run_id matches the single canonical resolver's own
    #     independent result -- no drift between two computations ---
    conn = db_mod.get_conn()
    canonical_run_id = search_store.get_latest_run_id_for_search(conn, search_id)
    conn.close()
    check(body_after_run2["run_id"] == canonical_run_id, f"F. API's run_id ({body_after_run2['run_id']}) matches get_latest_run_id_for_search()'s own independent result ({canonical_run_id})")

finally:
    source_registry.list_sources = _original_list_sources

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
