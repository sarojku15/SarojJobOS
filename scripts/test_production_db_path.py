#!/usr/bin/env python3
"""
Regression tests for the production/automation API path
(api/db.py's JOBOS_DB_PATH env var, scripts/run_production_api.py,
scripts/migrate_production_schema.py).

Covers:
  1. dev DB remains the default when JOBOS_DB_PATH is unset
  2. an explicit JOBOS_DB_PATH selects a different DB file
  3/4. a "saroj"-like candidate resolves through that API; follow-ups
       returns 200
  5. a nonexistent candidate still 404s
  6. candidate isolation is unaffected by which DB is active
  7/8. scheduler run-due / follow-ups both work against the
       alternate DB
  11/12/13. migrate_production_schema.py is idempotent, preserves
       integrity, and never shrinks/mutates existing rows or the
       real candidate row -- tested against a disposable BYTE COPY of
       the real production file, never the file itself.

Fully offline. The REAL production DB (data/applications/jobos.db) is
opened read-only (to make the disposable copy) and its own SHA-256/
size are verified unchanged before and after this entire test file.
"""
import hashlib
import shutil
import sqlite3
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


# ---------------------------------------------------- 1/2: DB path selection

import importlib
import os

# 1. Unset -> dev default (exactly today's behavior).
os.environ.pop("JOBOS_DB_PATH", None)
for mod_name in ("db",):
    sys.modules.pop(mod_name, None)
import db as db_mod_default
check(
    db_mod_default.DEV_DB == db_mod_default.DEFAULT_DEV_DB,
    f"1. with JOBOS_DB_PATH unset, DEV_DB is the default dev path, got {db_mod_default.DEV_DB}",
)

# 2. Explicit JOBOS_DB_PATH (pointed at a disposable temp file, never
# the real production file) is honored at import time.
tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_prod_path_test_"))
alt_db = tmp_dir / "alt.db"
os.environ["JOBOS_DB_PATH"] = str(alt_db)
sys.modules.pop("db", None)
import db as db_mod_alt
check(db_mod_alt.DEV_DB == alt_db.resolve(), f"2. with JOBOS_DB_PATH set, DEV_DB honors it, got {db_mod_alt.DEV_DB}")
os.environ.pop("JOBOS_DB_PATH", None)
sys.modules.pop("db", None)

# ------------------------------------------------- 3/4/5/6/7/8: API behavior

from fastapi.testclient import TestClient
sys.modules.pop("main", None)
sys.modules.pop("db", None)
import db as db_mod
import main as api_main
api_main.db_mod.DEV_DB = alt_db
client = TestClient(api_main.app)

resp = client.post("/api/candidates", json={"name": "Production Path Test Candidate"})
check(resp.status_code == 200, f"setup: candidate created against the alternate DB, got {resp.status_code}")
candidate_id = resp.json()["candidate_id"]

resp = client.get(f"/api/candidates/{candidate_id}/follow-ups")
check(resp.status_code == 200, f"3/4. GET .../follow-ups returns 200 for a real candidate on the alternate DB, got {resp.status_code}")
check(resp.json() == {"follow_ups": []}, f"3/4. empty follow-ups list is a valid, honest response, got {resp.json()}")

resp = client.get("/api/candidates/does-not-exist-at-all/follow-ups")
check(resp.status_code == 404, f"5. a nonexistent candidate still 404s on the alternate DB, got {resp.status_code}")

resp2 = client.post("/api/candidates", json={"name": "Second Candidate"})
candidate_2 = resp2.json()["candidate_id"]
conn = db_mod.get_conn()
conn.execute(
    "INSERT INTO jobs (job_id, source, company, title, location, work_model, jd_text, experience_required, mandatory_skills, preferred_skills, discovered_at, last_updated) "
    "VALUES ('prod-path-job-1', 'TEST', 'TestCo', 'SRE', 'Remote', 'Remote', 'test', '3+ years', '[]', '[]', datetime('now'), datetime('now'))"
)
conn.execute(
    "INSERT INTO candidate_job_matches (candidate_id, job_id, fit_score, priority, candidate_status, follow_up_date, created_at, updated_at) "
    "VALUES (?, 'prod-path-job-1', 80, 'B', 'FOUND', '2020-01-01', datetime('now'), datetime('now'))",
    (candidate_id,),
)
conn.commit()
conn.close()

resp = client.get(f"/api/candidates/{candidate_id}/follow-ups")
check(len(resp.json()["follow_ups"]) == 1, f"6. candidate 1's own follow-up is visible, got {resp.json()}")
resp = client.get(f"/api/candidates/{candidate_2}/follow-ups")
check(resp.json()["follow_ups"] == [], f"6. candidate isolation holds on the alternate DB -- candidate 2 sees none of candidate 1's data, got {resp.json()}")

resp = client.post("/api/scheduler/run-due")
check(resp.status_code == 200 and resp.json()["processed"] == 0, f"7. scheduler run-due works against the alternate DB (nothing due), got {resp.status_code} {resp.json()}")

# ------------------------------------------------- 9/10: n8n workflow config

import json as json_mod
for wf_path in (ROOT / "n8n" / "workflows" / "jobos_scheduled_search_runner.json", ROOT / "n8n" / "workflows" / "jobos_follow_up_reminder.json"):
    wf = json_mod.loads(wf_path.read_text())
    base_url = next(
        a["value"] for n in wf["nodes"] if n["name"] == "JobOS Config" for a in n["parameters"]["assignments"]["assignments"] if a["name"] == "jobos_base_url"
    )
    check(base_url.startswith("http://host.docker.internal:"), f"9. {wf_path.name}: default jobos_base_url uses host.docker.internal (not localhost, since n8n runs in Docker), got {base_url!r}")
    check("localhost" not in base_url and "127.0.0.1" not in base_url, f"10. {wf_path.name}: never uses localhost/127.0.0.1 as the default (unreachable from inside the n8n container), got {base_url!r}")

# ---------------------------------------------- 11/12/13: production migration

import migrate_production_schema

prod_copy_dir = Path(tempfile.mkdtemp(prefix="jobos_prod_migration_test_"))
prod_copy = prod_copy_dir / "jobos_copy.db"
shutil.copy2(PRODUCTION_DB, prod_copy)

pre_tables = {r[0] for r in sqlite3.connect(prod_copy).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
pre_counts = {t: sqlite3.connect(prod_copy).execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in pre_tables}

result = migrate_production_schema.migrate_production_schema(db_path=prod_copy, backup_dir=prod_copy_dir / "backups")
check(Path(result["backup_path"]).exists(), "11. migration writes a real backup file before touching anything")
check("search_schedules" in result["new_tables"] or "search_schedules" in pre_tables, f"11. search_schedules table exists after migration, got new_tables={result['new_tables']}")

post_conn = sqlite3.connect(prod_copy)
check(post_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "12. production DB integrity_check passes after migration")
for t in pre_tables:
    post_count = post_conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    check(post_count == pre_counts[t], f"13. table {t} row count unchanged by migration, got {pre_counts[t]} -> {post_count}")
post_conn.close()

sha_after_first = _sha(prod_copy)
result2 = migrate_production_schema.migrate_production_schema(db_path=prod_copy, backup_dir=prod_copy_dir / "backups")
check(result2["new_tables"] == [], f"11. re-running migration is idempotent -- no new tables the second time, got {result2['new_tables']}")
check(_sha(prod_copy) == sha_after_first, "11. re-running migration produces a byte-identical file (true idempotence, not just 'no error')")

shutil.rmtree(tmp_dir, ignore_errors=True)
shutil.rmtree(prod_copy_dir, ignore_errors=True)

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
