#!/usr/bin/env python3

"""
Regression tests for scripts/migrate_v16_application_tracking.py --
the additive schema backing the application tracker + follow-up
system (applied_at/applied_resume_id/applied_resume_variant/notes on
candidate_job_matches, plus the new application_follow_ups and
notification_events tables).

Covers the three scenarios required by this project's migration
testing convention: (A) an empty/fresh DB, (B) a representative
existing DB, (C) a production-schema copy in a temporary location --
never the real jobos.db/jobos_dev.db files themselves. Production and
dev DB SHA-256 verified unchanged before and after this entire file.
"""

import hashlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"
DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)
dev_before = _sha(DEV_DB)

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


import db_safety
import init_dev_db
import migrate_v16_application_tracking as mig


def _new_tmp_dir():
    return Path(tempfile.mkdtemp(prefix="jobos_test_migrate_v16_"))


# --- A. empty/fresh DB (via the full init_dev_db chain) ---
tmp_dir_a = _new_tmp_dir()
db_safety.assert_safe_to_destroy(tmp_dir_a / "test.db")
db_a = init_dev_db.init_dev_db(tmp_dir_a / "test.db")

conn = sqlite3.connect(db_a)
cols = [r[1] for r in conn.execute("PRAGMA table_info(candidate_job_matches)")]
check(
    {"applied_at", "applied_resume_id", "applied_resume_variant", "notes"} <= set(cols),
    "A. fresh DB via init_dev_db(): all 4 new candidate_job_matches columns present",
)
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
check("application_follow_ups" in tables, "A. fresh DB: application_follow_ups table created")
check("notification_events" in tables, "A. fresh DB: notification_events table created")
check(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "A. fresh DB: integrity_check ok")
conn.close()
shutil.rmtree(tmp_dir_a, ignore_errors=True)

# --- A2. idempotence on an empty DB (run twice, no error, no duplicate columns) ---
tmp_dir_a2 = _new_tmp_dir()
db_a2 = tmp_dir_a2 / "test.db"
db_safety.assert_safe_to_destroy(db_a2)
conn = sqlite3.connect(db_a2)
conn.execute(
    "CREATE TABLE candidate_job_matches (candidate_id TEXT, job_id TEXT, candidate_status TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
)
conn.execute("CREATE TABLE candidates (candidate_id TEXT PRIMARY KEY)")
conn.commit()
conn.close()

mig.migrate(db_a2)
mig.migrate(db_a2)  # idempotence

conn = sqlite3.connect(db_a2)
cols = [r[1] for r in conn.execute("PRAGMA table_info(candidate_job_matches)")]
check(cols.count("applied_at") == 1, "A2. re-running migrate() does not duplicate columns")
check(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "A2. idempotent re-run: integrity_check ok")
conn.close()
shutil.rmtree(tmp_dir_a2, ignore_errors=True)

# --- B. representative existing DB (a copy of the real dev DB, never the file itself) ---
tmp_dir_b = _new_tmp_dir()
db_b = tmp_dir_b / "dev_copy.db"
db_safety.assert_safe_to_destroy(db_b)
if DEV_DB.exists():
    shutil.copy2(DEV_DB, db_b)
    conn = sqlite3.connect(db_b)
    before = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("candidates", "candidate_job_matches", "jobs")
    }
    conn.close()

    mig.migrate(db_b)

    conn = sqlite3.connect(db_b)
    after = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("candidates", "candidate_job_matches", "jobs")
    }
    check(before == after, f"B. representative dev-DB copy: row counts unchanged by migration ({before} -> {after})")
    check(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "B. representative dev-DB copy: integrity_check ok")
    conn.close()
    shutil.rmtree(tmp_dir_b, ignore_errors=True)
else:
    print("SKIP: B. dev DB does not exist in this environment")

# --- C. production-schema copy in a temporary location ---
tmp_dir_c = _new_tmp_dir()
db_c = tmp_dir_c / "prod_copy.db"
db_safety.assert_safe_to_destroy(db_c)
if PRODUCTION_DB.exists():
    shutil.copy2(PRODUCTION_DB, db_c)
    check(_sha(PRODUCTION_DB) == production_before, "C. copying the production DB does not modify the real file")

    conn = sqlite3.connect(db_c)
    before = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("candidates", "candidate_job_matches", "jobs", "search_runs", "saved_searches")
    }
    saroj_before = conn.execute("SELECT candidate_id, name, status FROM candidates WHERE candidate_id='saroj'").fetchone()
    conn.close()

    mig.migrate(db_c)

    conn = sqlite3.connect(db_c)
    after = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("candidates", "candidate_job_matches", "jobs", "search_runs", "saved_searches")
    }
    check(before == after, f"C. production-schema copy: row counts unchanged by migration ({before} -> {after})")
    check(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "C. production-schema copy: integrity_check ok")

    saroj_after = conn.execute("SELECT candidate_id, name, status FROM candidates WHERE candidate_id='saroj'").fetchone()
    check(saroj_before == saroj_after, f"C. real candidate 'saroj' row byte-identical after migration ({saroj_before} == {saroj_after})")

    cols = [r[1] for r in conn.execute("PRAGMA table_info(candidate_job_matches)")]
    check({"applied_at", "applied_resume_id", "applied_resume_variant", "notes"} <= set(cols), "C. production-schema copy: all 4 new columns present after migration")
    conn.close()
    shutil.rmtree(tmp_dir_c, ignore_errors=True)

    check(_sha(PRODUCTION_DB) == production_before, "C. the REAL production file is still byte-identical after this entire test")
else:
    print("SKIP: C. production DB does not exist in this environment")

# --- final safety check ---
production_after = _sha(PRODUCTION_DB)
dev_after = _sha(DEV_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")
check(dev_before == dev_after, f"dev DB byte-identical before/after this test run (sha256 before={dev_before}, after={dev_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
