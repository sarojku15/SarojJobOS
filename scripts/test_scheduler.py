#!/usr/bin/env python3
"""
Phase 4 regression tests: recurring saved-search scheduling
(scripts/scheduler.py, migrate_v14_search_schedules.py,
api/main.py's PUT/GET .../schedule + POST /api/scheduler/run-due).

Covers (Phase 13 checklist):
  J. schedule creation (enabling computes a real next_run_at)
  K. disable (next_run_at cleared, run_due_schedules skips it)
  L. due-execution (a due, enabled schedule genuinely triggers a run
     via the real search_store.trigger_run())
  M. duplicate prevention (a schedule whose previous run is still
     QUEUED/RUNNING is not double-triggered -- delegates to the
     EXISTING trigger_run() concurrency guard, never a second check)
  N. restart-safety (the compare-and-swap UPDATE ... WHERE
     next_run_at = ? primitive: a "stale" claim attempt -- simulating
     a second process racing on the same tick -- is refused)

Fully offline (fake adapter, isolated temp DB, no live network calls,
no real clock/daemon -- run_due_schedules() is called directly, one
bounded pass at a time, matching this module's own "no loop" design).
Production DB verified byte-identical before/after.
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


class _SlowFakeAdapter(JobSourceAdapter):
    name = "FAKE_SCHEDULER_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        time.sleep(0.6)
        return [{
            "source": self.name, "job_id": "FAKE-SCHEDULER-JOB", "company": "TestCo",
            "title": query.role, "location": query.location, "work_model": "Remote",
            "job_url": "https://example.com/jobs/scheduler", "application_url": "https://example.com/apply/scheduler",
            "posted_date": "2026-09-26", "jd_text": "test jd", "experience_required": "3+ years",
            "mandatory_skills": [], "preferred_skills": [],
        }]


source_registry.ADAPTERS["FAKE_SCHEDULER_SOURCE"] = _SlowFakeAdapter
source_registry.list_sources = lambda: ["FAKE_SCHEDULER_SOURCE"]

tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_scheduler_"))
tmp_db = tmp_dir / "jobos.db"
import init_dev_db
init_dev_db.init_dev_db(tmp_db)
import db as db_mod
db_mod.DEV_DB = tmp_db
from fastapi.testclient import TestClient
import main as api_main
api_main.db_mod.DEV_DB = tmp_db
import scheduler

client = TestClient(api_main.app)

resp = client.post("/api/candidates", json={"name": "Scheduler Test"})
candidate_id = resp.json()["candidate_id"]
client.put(f"/api/candidates/{candidate_id}/profile", json={
    "current_title": "SRE", "total_experience_years": 5,
    "skills": {}, "job_preferences": {"target_roles": ["SRE"], "target_locations": ["Remote"]},
})
client.post(f"/api/candidates/{candidate_id}/profile/confirm")
resp = client.post(f"/api/candidates/{candidate_id}/searches", json={
    "name": "Scheduler test search", "target_roles": ["SRE"], "target_locations": ["Remote"],
    "minimum_match_score": 0, "sources": ["FAKE_SCHEDULER_SOURCE"],
})
search_id = resp.json()["saved_search_id"]

# --- J: schedule creation ---
resp = client.put(f"/api/searches/{search_id}/schedule?candidate_id={candidate_id}", json={"enabled": True, "frequency": "daily", "timezone": "Asia/Kolkata"})
check(resp.status_code == 200, f"J: enabling a schedule succeeds, got {resp.status_code} {resp.text[:200]}")
sched = resp.json()
check(sched["enabled"] == 1, f"J: schedule persisted as enabled, got {sched['enabled']}")
check(sched["next_run_at"] is not None, "J: enabling computes a real next_run_at")
check(sched["frequency"] == "daily" and sched["timezone"] == "Asia/Kolkata", "J: frequency/timezone persisted correctly")

resp = client.get(f"/api/searches/{search_id}/schedule?candidate_id={candidate_id}")
check(resp.json()["next_run_at"] == sched["next_run_at"], "J: GET returns the same persisted schedule")

# integration fix: GET /api/searches/{id} (the saved-search's OWN
# response, not the dedicated schedule route) must also reflect the
# REAL search_schedules state -- migrate_v14's own docstring promised
# this but it was never wired up until now (search_store._row_to_dict).
resp = client.get(f"/api/searches/{search_id}?candidate_id={candidate_id}")
embedded_schedule = resp.json()["schedule"]
check(embedded_schedule["enabled"] is True and embedded_schedule["frequency"] == "daily",
      f"integration fix: the saved search's own GET response embeds the REAL schedule (not the dead legacy schedule_json), got {embedded_schedule}")
check(embedded_schedule["next_run_at"] == sched["next_run_at"], "integration fix: embedded next_run_at matches the dedicated schedule route's value")

resp = client.get(f"/api/candidates/{candidate_id}/searches")
listed = next(s for s in resp.json()["searches"] if s["saved_search_id"] == search_id)
check(listed["schedule"]["enabled"] is True, f"integration fix: the searches LIST response also embeds the real schedule, got {listed['schedule']}")

# reject an invalid frequency
resp = client.put(f"/api/searches/{search_id}/schedule?candidate_id={candidate_id}", json={"enabled": True, "frequency": "fortnightly"})
check(resp.status_code == 422, f"J: an invalid frequency is rejected, got {resp.status_code}")

# --- K: disable ---
resp = client.put(f"/api/searches/{search_id}/schedule?candidate_id={candidate_id}", json={"enabled": False})
check(resp.status_code == 200 and resp.json()["enabled"] == 0, "K: disabling a schedule persists enabled=0")
check(resp.json()["next_run_at"] is None, "K: disabling clears next_run_at")

conn = db_mod.get_conn()
outcomes = scheduler.run_due_schedules(conn, tmp_db)
conn.close()
check(outcomes == [], f"K: a disabled schedule is never picked up by run_due_schedules, got {outcomes}")

# --- L: due-execution ---
resp = client.put(f"/api/searches/{search_id}/schedule?candidate_id={candidate_id}", json={"enabled": True, "frequency": "hourly"})
check(resp.status_code == 200, "setup: re-enable schedule as hourly for due-execution test")

# Force this schedule into the past so it is genuinely due right now,
# exactly as a real elapsed hour would -- never bypassing
# run_due_schedules' own due-check logic.
conn = db_mod.get_conn()
conn.execute("UPDATE search_schedules SET next_run_at = '2020-01-01T00:00:00+00:00' WHERE saved_search_id = ?", (search_id,))
conn.commit()
conn.close()

conn = db_mod.get_conn()
outcomes = scheduler.run_due_schedules(conn, tmp_db)
conn.close()
check(len(outcomes) == 1 and outcomes[0]["saved_search_id"] == search_id, f"L: the due schedule is processed exactly once, got {outcomes}")
check(outcomes[0]["status"] in ("QUEUED", "RUNNING") and outcomes[0]["run_id"], f"L: due-execution genuinely triggers a real run via trigger_run(), got {outcomes[0]}")
run_id_1 = outcomes[0]["run_id"]

conn = db_mod.get_conn()
row = conn.execute("SELECT COUNT(*) FROM search_runs WHERE candidate_id = ?", (candidate_id,)).fetchone()
check(row[0] == 1, f"L: exactly one search_runs row exists after due-execution, got {row[0]}")
sched_row = conn.execute("SELECT next_run_at, last_run_at, last_run_status FROM search_schedules WHERE saved_search_id = ?", (search_id,)).fetchone()
conn.close()
check(sched_row[0] > "2026-09-27", f"L: next_run_at was advanced forward (never left in the past), got {sched_row[0]}")
check(sched_row[1] is not None and sched_row[2] in ("QUEUED", "RUNNING"), f"L: last_run_at/last_run_status recorded, got {tuple(sched_row)}")

# --- M: duplicate prevention while the triggered run is still active ---
time.sleep(0.15)  # let the worker thread genuinely claim QUEUED -> RUNNING
conn = db_mod.get_conn()
conn.execute("UPDATE search_schedules SET next_run_at = '2020-01-01T00:00:01+00:00' WHERE saved_search_id = ?", (search_id,))
conn.commit()
conn.close()

conn = db_mod.get_conn()
outcomes2 = scheduler.run_due_schedules(conn, tmp_db)
conn.close()
check(len(outcomes2) == 1, f"M: the still-due schedule is processed (its tick is claimed), got {len(outcomes2)}")
check(outcomes2[0]["status"] == "SKIPPED_ALREADY_RUNNING", f"M: outcome correctly reports SKIPPED_ALREADY_RUNNING (delegated to trigger_run()'s existing guard), got {outcomes2[0]}")
check(outcomes2[0]["run_id"] == run_id_1, f"M: no second run_id was created -- same run_id referenced, got {outcomes2[0]['run_id']} vs {run_id_1}")

conn = db_mod.get_conn()
total_runs = conn.execute("SELECT COUNT(*) FROM search_runs WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
conn.close()
check(total_runs == 1, f"M: still exactly one search_runs row -- no duplicate run was spawned, got {total_runs}")

# --- N: restart-safety / compare-and-swap ---
# Simulate two processes racing on the exact same due tick: read the
# current next_run_at, then have one "process" successfully claim it
# via the real compare-and-swap UPDATE, and confirm a second attempt
# using the SAME (now-stale) original value is refused by the DB
# itself -- exactly what protects against a crash-and-restart
# double-processing the same tick.
conn = db_mod.get_conn()
stale_next_run_at = conn.execute("SELECT next_run_at FROM search_schedules WHERE saved_search_id = ?", (search_id,)).fetchone()[0]
first_claim = conn.execute(
    "UPDATE search_schedules SET next_run_at = ? WHERE saved_search_id = ? AND next_run_at = ?",
    ("2099-01-01T00:00:00+00:00", search_id, stale_next_run_at),
)
conn.commit()
check(first_claim.rowcount == 1, f"N: first claim on a fresh next_run_at value succeeds, got rowcount={first_claim.rowcount}")

second_claim = conn.execute(
    "UPDATE search_schedules SET next_run_at = ? WHERE saved_search_id = ? AND next_run_at = ?",
    ("2099-02-02T00:00:00+00:00", search_id, stale_next_run_at),
)
conn.commit()
check(second_claim.rowcount == 0, f"N: a second claim using the same now-stale next_run_at value affects zero rows (compare-and-swap refuses it), got rowcount={second_claim.rowcount}")
final_value = conn.execute("SELECT next_run_at FROM search_schedules WHERE saved_search_id = ?", (search_id,)).fetchone()[0]
conn.close()
check(final_value == "2099-01-01T00:00:00+00:00", f"N: only the first claim's value stuck -- no double-advance, got {final_value}")

# --- N (real concurrency): two genuinely simultaneous scheduler
# invocations against the SAME due schedule, each on its own thread
# with its own sqlite connection -- not the simulated sequential race
# above. A threading.Barrier lines both threads up so they issue their
# compare-and-swap UPDATEs as close to simultaneously as this process
# can arrange; SQLite's own file-level locking (Python's sqlite3
# default 5s busy-timeout) serializes the two writers, and the
# compare-and-swap WHERE clause guarantees only the winner's UPDATE
# actually matches a row.
resp = client.post(f"/api/candidates/{candidate_id}/searches", json={
    "name": "Concurrency race search", "target_roles": ["SRE"], "target_locations": ["Remote"],
    "minimum_match_score": 0, "sources": ["FAKE_SCHEDULER_SOURCE"],
})
race_search_id = resp.json()["saved_search_id"]
resp = client.put(f"/api/searches/{race_search_id}/schedule?candidate_id={candidate_id}", json={"enabled": True, "frequency": "hourly"})
check(resp.status_code == 200, "setup: race-test schedule enabled")

conn = db_mod.get_conn()
conn.execute("UPDATE search_schedules SET next_run_at = '2020-01-01T00:00:00+00:00' WHERE saved_search_id = ?", (race_search_id,))
conn.commit()
conn.close()

barrier = threading.Barrier(2)
thread_outcomes = [None, None]


def _race_worker(index):
    thread_conn = db_mod.get_conn()
    barrier.wait()
    try:
        thread_outcomes[index] = scheduler.run_due_schedules(thread_conn, tmp_db)
    finally:
        thread_conn.close()


threads = [threading.Thread(target=_race_worker, args=(i,)) for i in range(2)]
for t in threads:
    t.start()
for t in threads:
    t.join(timeout=10)

race_relevant = [
    [o for o in (outcomes_list or []) if o["saved_search_id"] == race_search_id]
    for outcomes_list in thread_outcomes
]
total_claims = sum(len(r) for r in race_relevant)
check(total_claims == 1, f"N (real concurrency): exactly one of the two simultaneous invocations claims the due tick, got {total_claims} across {thread_outcomes}")

conn = db_mod.get_conn()
race_run_count = conn.execute(
    "SELECT COUNT(*) FROM saved_search_runs WHERE saved_search_id = ?", (race_search_id,)
).fetchone()[0]
conn.close()
check(race_run_count == 1, f"N (real concurrency): exactly one search_run was actually created for the race schedule, got {race_run_count}")

race_run_id = next(o["run_id"] for r in race_relevant for o in r)

# Wait for the (deliberately slow) runs to finish so the test process
# exits cleanly (no lingering threads).
deadline = time.time() + 15
while time.time() < deadline:
    r = client.get(f"/api/runs/{run_id_1}?candidate_id={candidate_id}")
    r2 = client.get(f"/api/runs/{race_run_id}?candidate_id={candidate_id}")
    if r.json()["status"] in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED") and r2.json()["status"] in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED"):
        break
    time.sleep(0.1)

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
