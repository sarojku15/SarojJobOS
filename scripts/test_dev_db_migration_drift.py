#!/usr/bin/env python3

"""
Regression test for a real, currently-reproducing bug found live on
this project's own persistent dev database: api/db.py's
ensure_dev_db() used to only call init_dev_db.init_dev_db() when
DEV_DB didn't exist AT ALL -- so any migration added AFTER a
candidate's dev DB file was first created (e.g.
migrate_v5_search_run_sources.py) was NEVER applied to that
already-existing file. The only way it ever took effect was a human
manually running that one migrate_vN.py script by hand against the
live file.

Concretely, on the REAL data/applications/jobos_dev.db, this meant
search_run_sources silently did not exist. That table's absence is
caught defensively on BOTH sides (by design, for backward
compatibility with pre-v5 DBs):
  - WRITE: search_worker.py's _persist_source_run_states() catches
    "no such table" and simply returns, no exception, no rows written.
  - READ: search_store.get_run_sources() catches the same error and
    returns [] -- which search_store.get_source_filter_summary() then
    fills with SYNTHESIZED "NOT_ATTEMPTED" placeholders for every
    configured source.

Net effect: a completed run's results kept populating normally (via
the unrelated candidate_job_matches path), while the "Source Execution
Audit" section silently showed fabricated NOT_ATTEMPTED/0/0/0 rows for
every source -- with no error anywhere. This is the actual root cause
of a reported "332 results, but every source shows NOT_ATTEMPTED"
inconsistency.

Fix: ensure_dev_db() now calls the fully-idempotent
init_dev_db.init_dev_db() unconditionally (once per process, via a
module-level flag, not on every request), so ANY migration -- this
one or any future one -- is always applied to an already-existing dev
DB file, not just a brand-new one.

Covers:
  1. A dev DB created with only the OLDER (pre-v5) schema, opened via
     api/db.py's real get_conn()/ensure_dev_db(), automatically gains
     search_run_sources (and v6/v7's table/column) -- no manual
     migrate_vN.py invocation required.
  2. A second get_conn() call on the same process does not error or
     redundantly re-run migrations.
  3. End-to-end: running a real search (fake adapters, offline) against
     this now-migrated former-pre-v5 DB produces REAL search_run_sources
     rows in the API response (attempted=True, status=SUCCESS, real
     raw/eligible counts) -- never the synthesized NOT_ATTEMPTED
     fallback that masked this bug.

Fully offline (fake adapters, no real network call). Never opens
data/applications/jobos.db.
"""

import hashlib
import sqlite3
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

import init_tracker
import migrate_v2_schema
import migrate_v3_saved_searches
import migrate_v4_search_extensions
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


class _FakeDriftAdapter(JobSourceAdapter):
    name = "FAKE_DRIFT_SOURCE"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [
            {
                "source": self.name, "job_id": "FAKE-DRIFT-1", "company": "DriftCo",
                "title": query.role, "location": query.location, "work_model": "Hybrid",
                "job_url": "https://example.com/jobs/drift-1",
                "application_url": "https://example.com/apply/drift-1",
                "posted_date": "2026-09-20",
                "jd_text": f"{query.role} role requiring strong skills.",
                "experience_required": "5+ years",
                "mandatory_skills": [], "preferred_skills": [],
            }
        ]


source_registry.ADAPTERS["FAKE_DRIFT_SOURCE"] = _FakeDriftAdapter
_original_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_DRIFT_SOURCE"]

try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_migration_drift_"))
    tmp_db = tmp_dir / "jobos_pre_v5.db"

    # --- build a dev DB with ONLY the OLDER (pre-v5) schema, exactly
    #     simulating a real dev DB file that predates
    #     migrate_v5_search_run_sources.py ever being added ---
    original_data_dir = init_tracker.DATA_DIR
    original_db_path = init_tracker.DB_PATH
    try:
        init_tracker.DATA_DIR = tmp_dir
        init_tracker.DB_PATH = tmp_db
        init_tracker.main()
    finally:
        init_tracker.DATA_DIR = original_data_dir
        init_tracker.DB_PATH = original_db_path

    conn = sqlite3.connect(tmp_db)
    conn.execute("PRAGMA foreign_keys = ON")
    migrate_v2_schema._create_new_tables(conn)
    migrate_v2_schema._ensure_job_columns(conn)
    conn.commit()
    conn.close()
    migrate_v3_saved_searches.migrate(tmp_db)
    migrate_v4_search_extensions.migrate(tmp_db)
    # Deliberately NOT calling migrate_v5/v6/v7 -- this is the "old" DB.

    tables_before = {
        r[0] for r in sqlite3.connect(tmp_db).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    check("search_run_sources" not in tables_before, "1. setup: the simulated pre-v5 DB genuinely lacks search_run_sources")
    check("candidate_job_status_history" not in tables_before, "1. setup: the simulated pre-v5 DB genuinely lacks candidate_job_status_history (v6)")

    # --- 1. opening it via the REAL api/db.py get_conn() must
    #     automatically bring it up to the current schema ---
    import db as db_mod
    db_mod.DEV_DB = tmp_db
    db_mod._migrations_applied = False

    conn1 = db_mod.get_conn()
    tables_after = {
        r[0] for r in conn1.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    check("search_run_sources" in tables_after, "1. get_conn() on a pre-v5 DB automatically creates search_run_sources -- no manual migrate_v5 invocation needed")
    check("candidate_job_status_history" in tables_after, "1. get_conn() also brings v6 (status history) up to date")
    match_cols = {r[1] for r in conn1.execute("PRAGMA table_info(candidate_job_matches)").fetchall()}
    check("resume_variant" in match_cols, "1. get_conn() also brings v7 (resume_variant column) up to date")
    conn1.close()

    # --- 2. a second get_conn() call must not error or redundantly
    #     reapply migrations ---
    conn2 = db_mod.get_conn()
    check(conn2 is not None, "2. a second get_conn() call on the same process succeeds without error")
    conn2.close()

    # --- 3. end-to-end: a real search against this now-migrated DB
    #     produces REAL search_run_sources rows, not synthesized
    #     NOT_ATTEMPTED placeholders ---
    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db

    client = TestClient(api_main.app)

    resp = client.post("/api/candidates", json={"name": "Drift Test Candidate"})
    candidate_id = resp.json()["candidate_id"]
    client.put(
        f"/api/candidates/{candidate_id}/profile",
        json={
            "current_title": "Drift Role",
            "total_experience_years": 5,
            "skills": {"other": [{"name": "Generic Skill"}]},
            "job_preferences": {"target_roles": ["Drift Role"], "target_locations": ["Remote"]},
        },
    )
    client.post(f"/api/candidates/{candidate_id}/profile/confirm")

    resp = client.post(
        f"/api/candidates/{candidate_id}/searches",
        json={
            "name": "drift regression search",
            "target_roles": ["Drift Role"],
            "target_locations": ["Remote"],
            "minimum_match_score": 0,
            "sources": ["FAKE_DRIFT_SOURCE"],
        },
    )
    search_id = resp.json()["saved_search_id"]
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
    check(status == "COMPLETED", f"3. setup: the search run completes on the now-migrated former-pre-v5 DB, got {status}")

    resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
    body = resp.json()
    check(len(body["results"]) >= 1, "3. results populate correctly")

    sources = {s["source"]: s for s in body["sources"]}
    fake_source_entry = sources.get("FAKE_DRIFT_SOURCE")
    check(fake_source_entry is not None, "3. FAKE_DRIFT_SOURCE appears in the sources/audit list")
    if fake_source_entry:
        check(fake_source_entry["attempted"] is True, f"3. SAFETY: source audit shows attempted=True (a REAL row, not the synthesized NOT_ATTEMPTED placeholder that masked this bug), got {fake_source_entry['attempted']!r}")
        check(fake_source_entry["status"] == "SUCCESS", f"3. SAFETY: source audit shows the REAL status=SUCCESS, not a fabricated NOT_ATTEMPTED, got {fake_source_entry['status']!r}")
        check(fake_source_entry["raw_count"] == 1, f"3. SAFETY: source audit shows the REAL raw_count=1 (not 0), got {fake_source_entry['raw_count']!r}")

finally:
    source_registry.list_sources = _original_list_sources

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
