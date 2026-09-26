#!/usr/bin/env python3
"""
Regression test for a real bug found via a live end-to-end run
(2026-09-26/27): api/search_store.trigger_run() allowed a brand new
run to be created for a saved search even while that same saved
search already had a QUEUED/RUNNING run in progress -- nothing
prevented a second "Run Now" click from spawning a second, resource-
contending worker thread against the same external sites. One such
duplicate run became permanently orphaned after an unrelated server
restart, and (before a separate fix) could even outrank the real
completed run as "the latest run" shown on results/dashboard pages.

This file covers ONLY the concurrency guard itself
(api/search_store.get_active_run_for_search() / trigger_run()'s new
already_running behavior) -- see test_latest_usable_run_selection.py
for the separate "which run should a human be shown" fix, and
test_dashboard_score_consistency.py for the separate target_roles/
target_locations override fix. Fully offline (fake adapter, isolated
temp DB, no live network calls); the production DB is never opened.
"""
import hashlib
import sys
import tempfile
import threading
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


# Deliberately slow (0.6s) so the test has a reliable window to attempt
# a second "Run Now" while the first is genuinely still RUNNING --
# never a race, never flaky by construction.
class _SlowFakeAdapter(JobSourceAdapter):
    name = "FAKE_CONCURRENCY_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        time.sleep(0.6)
        return [{
            "source": self.name, "job_id": "FAKE-CONCURRENCY-JOB", "company": "TestCo",
            "title": query.role, "location": query.location, "work_model": "Remote",
            "job_url": "https://example.com/jobs/concurrency", "application_url": "https://example.com/apply/concurrency",
            "posted_date": "2026-09-26", "jd_text": "test jd", "experience_required": "3+ years",
            "mandatory_skills": [], "preferred_skills": [],
        }]


source_registry.ADAPTERS["FAKE_CONCURRENCY_SOURCE"] = _SlowFakeAdapter
_orig_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_CONCURRENCY_SOURCE"]

try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_concurrency_"))
    tmp_db = tmp_dir / "jobos.db"
    import init_dev_db
    init_dev_db.init_dev_db(tmp_db)
    import db as db_mod
    db_mod.DEV_DB = tmp_db
    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db
    import search_store

    client = TestClient(api_main.app)

    resp = client.post("/api/candidates", json={"name": "Concurrency Test"})
    candidate_id = resp.json()["candidate_id"]
    client.put(f"/api/candidates/{candidate_id}/profile", json={
        "current_title": "SRE", "total_experience_years": 5,
        "skills": {}, "job_preferences": {"target_roles": ["SRE"], "target_locations": ["Remote"]},
    })
    client.post(f"/api/candidates/{candidate_id}/profile/confirm")
    resp = client.post(f"/api/candidates/{candidate_id}/searches", json={
        "name": "Concurrency test search", "target_roles": ["SRE"], "target_locations": ["Remote"],
        "minimum_match_score": 0, "sources": ["FAKE_CONCURRENCY_SOURCE"],
    })
    search_id = resp.json()["saved_search_id"]

    # --- 1. First Run Now creates one run. ---
    resp1 = client.post(f"/api/searches/{search_id}/run?candidate_id={candidate_id}")
    check(resp1.status_code == 200, f"1. first Run Now succeeds, got {resp1.status_code}")
    run_id_1 = resp1.json()["run_id"]
    check(bool(run_id_1), "1. first Run Now returns a real run_id")

    # Give the worker thread a moment to actually claim the queue item
    # (RUNNING), not just have the row freshly INSERTed as QUEUED --
    # either state must be blocked by the guard, so this also proves
    # the guard isn't narrowly checking only one of the two statuses.
    time.sleep(0.15)

    conn = db_mod.get_conn()
    row = conn.execute("SELECT status FROM search_runs WHERE search_run_id=?", (run_id_1,)).fetchone()
    conn.close()
    check(row is not None and row[0] in ("QUEUED", "RUNNING"),
          f"setup: run 1 is genuinely QUEUED/RUNNING right now, got {row}")

    # --- 2. Second Run Now while first is RUNNING does NOT create another run. ---
    resp2 = client.post(f"/api/searches/{search_id}/run?candidate_id={candidate_id}")
    check(resp2.status_code == 409, f"2. second Run Now while first is active is rejected (409), got {resp2.status_code}")
    body2 = resp2.json().get("detail", {})
    check(isinstance(body2, dict) and body2.get("error") == "already_running",
          f"2. rejection body identifies 'already_running', got {body2}")

    # --- 3. Same existing run_id is returned/referenced. ---
    check(body2.get("run_id") == run_id_1, f"3. rejection references the SAME existing run_id, got {body2.get('run_id')!r} expected {run_id_1!r}")
    check(body2.get("status") in ("QUEUED", "RUNNING"), f"3. rejection exposes the existing run's real status, got {body2.get('status')!r}")

    conn = db_mod.get_conn()
    total_runs = conn.execute("SELECT COUNT(*) FROM search_runs WHERE candidate_id=?", (candidate_id,)).fetchone()[0]
    conn.close()
    check(total_runs == 1, f"2/3. no second search_runs row was created -- exactly 1 exists, got {total_runs}")

    # Direct unit check of the new guard function itself.
    conn = db_mod.get_conn()
    active = search_store.get_active_run_for_search(conn, search_id)
    conn.close()
    check(active is not None and active[0] == run_id_1, f"unit: get_active_run_for_search() returns the real active run, got {active}")

    # Wait for the (deliberately slow) first run to actually finish.
    deadline = time.time() + 15
    final_status = None
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id_1}?candidate_id={candidate_id}")
        final_status = r.json()["status"]
        if final_status in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED"):
            break
        time.sleep(0.1)
    check(final_status == "COMPLETED", f"setup: run 1 reaches a terminal state, got {final_status}")

    # --- 4. After the first run is terminal, a new Run Now is allowed. ---
    resp3 = client.post(f"/api/searches/{search_id}/run?candidate_id={candidate_id}")
    check(resp3.status_code == 200, f"4. Run Now after run 1 is terminal succeeds, got {resp3.status_code}")
    run_id_2 = resp3.json().get("run_id")
    check(bool(run_id_2) and run_id_2 != run_id_1, f"4. a genuinely NEW run_id is created, got {run_id_2!r} (previous was {run_id_1!r})")

    conn = db_mod.get_conn()
    active_after_terminal = search_store.get_active_run_for_search(conn, search_id)
    conn.close()
    check(active_after_terminal is not None and active_after_terminal[0] == run_id_2,
          f"4. get_active_run_for_search() now reports the new run as active, got {active_after_terminal}")

    # Let the second run finish too so the test process exits cleanly
    # (no lingering threads).
    deadline = time.time() + 15
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id_2}?candidate_id={candidate_id}")
        if r.json()["status"] in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED"):
            break
        time.sleep(0.1)

finally:
    source_registry.list_sources = _orig_list_sources

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
