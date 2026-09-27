#!/usr/bin/env python3
"""
Regression test for a real bug found via a live end-to-end run
(2026-09-26/27): api/search_store.get_latest_run_id_for_search()
(chronologically newest run, regardless of status) was used to decide
which run's source-audit table to show on the results/dashboard/export
pages. When a newer run for the same saved search became orphaned
(its worker thread died -- e.g. an API restart -- leaving it stuck
RUNNING forever, with zero search_run_sources rows), that orphaned
run's empty audit was shown INSTEAD OF an older, real, completed run's
populated one: "Results = <completed run>, but Source Audit =
<orphaned run>".

Covers:
  C. get_latest_usable_run_id_for_search() prefers the newest run that
     has reached ANY terminal state over a newer orphaned RUNNING one.
  D. A RUNNING/orphaned run never hides an older completed run's
     results.
  E. The results API's own displayed run_id and its "sources" audit
     always refer to the SAME run.
  Also: api/search_store.recover_orphaned_runs() (the startup-recovery
  half of this same fix) correctly marks only the genuinely orphaned
  run FAILED, never touches the real completed run, and never touches
  another candidate's data.

Fully offline (fake adapter, isolated temp DB, no live network calls);
the production DB is never opened. The "orphaned run" is simulated by
directly inserting a RUNNING search_runs/search_queue row with no
worker behind it -- exactly the real-world scenario (a run whose
process died mid-flight), reproduced deterministically rather than by
actually killing a server process.
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


class _FakeAdapter(JobSourceAdapter):
    name = "FAKE_LATESTRUN_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [{
            "source": self.name, "job_id": "FAKE-LATESTRUN-JOB", "company": "TestCo",
            "title": query.role, "location": query.location, "work_model": "Remote",
            "job_url": "https://example.com/jobs/latestrun", "application_url": "https://example.com/apply/latestrun",
            "posted_date": "2026-09-26", "jd_text": "test jd", "experience_required": "3+ years",
            "mandatory_skills": [], "preferred_skills": [],
        }]


source_registry.ADAPTERS["FAKE_LATESTRUN_SOURCE"] = _FakeAdapter
_orig_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_LATESTRUN_SOURCE"]

try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_latestrun_"))
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

    resp = client.post("/api/candidates", json={"name": "Latest Run Test"})
    candidate_id = resp.json()["candidate_id"]
    client.put(f"/api/candidates/{candidate_id}/profile", json={
        "current_title": "SRE", "total_experience_years": 5,
        "skills": {}, "job_preferences": {"target_roles": ["SRE"], "target_locations": ["Remote"]},
    })
    client.post(f"/api/candidates/{candidate_id}/profile/confirm")
    resp = client.post(f"/api/candidates/{candidate_id}/searches", json={
        "name": "Latest run test search", "target_roles": ["SRE"], "target_locations": ["Remote"],
        "minimum_match_score": 0, "sources": ["FAKE_LATESTRUN_SOURCE"],
    })
    search_id = resp.json()["saved_search_id"]

    # --- Run 1: real, completed, populated. ---
    resp = client.post(f"/api/searches/{search_id}/run?candidate_id={candidate_id}")
    run_1 = resp.json()["run_id"]
    deadline = time.time() + 15
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_1}?candidate_id={candidate_id}")
        if r.json()["status"] in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED"):
            break
        time.sleep(0.1)
    check(r.json()["status"] == "COMPLETED", f"setup: run 1 completes, got {r.json()['status']}")

    # --- Simulate an orphaned Run 2: a real search_runs/search_queue
    # row in RUNNING state, created chronologically AFTER run 1, with
    # NO worker thread behind it and NO search_run_sources rows --
    # exactly what a process-killed-mid-run leaves behind. ---
    conn = db_mod.get_conn()
    run_2 = "orphaned-run-2-simulated"
    # A fixed literal timestamp here would silently rely on wall-clock
    # time staying "earlier" than it forever -- computed relative to
    # the real current time instead, so this is never flaky no matter
    # what day/time this test actually runs.
    import datetime
    now = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=1)).isoformat()
    conn.execute(
        """
        INSERT INTO search_runs (search_run_id, candidate_id, status, source, query, started_at, created_at, updated_at)
        VALUES (?, ?, 'RUNNING', 'FAKE_LATESTRUN_SOURCE', '{"queries":[]}', ?, ?, ?)
        """,
        (run_2, candidate_id, now, now, now),
    )
    conn.execute(
        "INSERT INTO search_queue (search_run_id, candidate_id, status, created_at, claimed_at) VALUES (?, ?, 'RUNNING', ?, ?)",
        (run_2, candidate_id, now, now),
    )
    conn.execute(
        "INSERT INTO saved_search_runs (saved_search_id, search_run_id, created_at) VALUES (?, ?, ?)",
        (search_id, run_2, now),
    )
    conn.commit()
    conn.close()

    # --- C. get_latest_usable_run_id_for_search() prefers run 1 (real,
    # terminal) over the newer orphaned run 2 (still RUNNING). ---
    conn = db_mod.get_conn()
    usable = search_store.get_latest_usable_run_id_for_search(conn, search_id)
    newest_created = search_store.get_latest_run_id_for_search(conn, search_id)
    conn.close()
    check(usable == run_1, f"C. get_latest_usable_run_id_for_search() returns run 1 (real), not the orphaned run 2, got {usable!r}")
    check(newest_created == run_2, f"C. setup sanity: get_latest_run_id_for_search() (unchanged, chronologically newest) still returns run 2, got {newest_created!r}")

    # --- D/E. The results API's displayed run_id and its "sources"
    # audit both refer to run 1 -- run 1's real result is not hidden,
    # and the audit is never a different run's than the one shown. ---
    resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
    body = resp.json()
    check(body["run_id"] == run_1, f"D/E. results API's displayed run_id is run 1, got {body['run_id']!r}")
    check(len(body["results"]) == 1, f"D. run 1's real result is not hidden by the orphaned run, got {len(body['results'])} results")
    fake_source_audit = next((s for s in body["sources"] if s["source"] == "FAKE_LATESTRUN_SOURCE"), None)
    check(fake_source_audit is not None and fake_source_audit["status"] == "SUCCESS",
          f"E. source audit shown corresponds to run 1 (SUCCESS), not the orphaned run 2 (would show NOT_ATTEMPTED), got {fake_source_audit}")

    # --- recover_orphaned_runs(): only the genuinely orphaned run 2 is
    # recovered; run 1 (already terminal) is untouched. ---
    conn = db_mod.get_conn()
    recovered_count = search_store.recover_orphaned_runs(conn)
    run_1_status = conn.execute("SELECT status FROM search_runs WHERE search_run_id=?", (run_1,)).fetchone()[0]
    run_2_row = conn.execute("SELECT status, error_message FROM search_runs WHERE search_run_id=?", (run_2,)).fetchone()
    queue_2_status = conn.execute("SELECT status FROM search_queue WHERE search_run_id=?", (run_2,)).fetchone()[0]
    conn.close()
    check(recovered_count == 1, f"recover_orphaned_runs(): recovers exactly the 1 genuinely orphaned run, got {recovered_count}")
    check(run_1_status == "COMPLETED", f"recover_orphaned_runs(): run 1 (already terminal) is untouched, got {run_1_status}")
    check(run_2_row[0] == "FAILED", f"recover_orphaned_runs(): orphaned run 2 is marked FAILED, got {run_2_row[0]}")
    check(run_2_row[1] == "Worker process was not running after API restart.",
          f"recover_orphaned_runs(): honest, explicit reason recorded, got {run_2_row[1]!r}")
    check(queue_2_status == "FAILED", f"recover_orphaned_runs(): search_queue row also marked FAILED, got {queue_2_status}")

    # Re-running the same recovery again must be a no-op (idempotent --
    # nothing left QUEUED/RUNNING to recover).
    conn = db_mod.get_conn()
    recovered_again = search_store.recover_orphaned_runs(conn)
    conn.close()
    check(recovered_again == 0, f"recover_orphaned_runs(): idempotent, second call recovers 0, got {recovered_again}")

finally:
    source_registry.list_sources = _orig_list_sources

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
