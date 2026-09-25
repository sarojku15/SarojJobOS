"""
Phase 9 API database access.

Always opens data/applications/jobos_dev.db -- the Phase 9 local
development database created by scripts/init_dev_db.py -- and NEVER
the production database (data/applications/jobos.db). There is no
environment variable, header, or request parameter anywhere in this
API that can redirect it elsewhere; this is deliberate, so the API
surface can never be pointed at production data by a client.
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"

SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


_migrations_applied = False


def ensure_dev_db():
    """
    Root-cause fix for a real bug: this used to only call
    init_dev_db.init_dev_db() when DEV_DB didn't exist AT ALL, so a
    migration added after a candidate's dev DB file was first created
    (e.g. migrate_v5_search_run_sources.py, added in an earlier
    session) was NEVER applied to that already-existing file -- the
    only way it took effect was a human manually running that one
    migrate_vN.py script by hand. In practice this meant the ENTIRE
    Source Execution Audit feature silently no-op'd on both write
    (search_worker.py's own "no such table" catch, by design, for
    backward compatibility with pre-v5 DBs) and read
    (search_store.get_run_sources()'s identical catch) -- runs
    completed normally and results kept populating via the unrelated
    candidate_job_matches path, but every source silently showed as a
    synthesized NOT_ATTEMPTED placeholder, with no error surfaced
    anywhere. init_dev_db.init_dev_db() is already fully idempotent
    (every migration is CREATE TABLE IF NOT EXISTS / ADD COLUMN
    IF NOT EXISTS-equivalent), so calling it unconditionally is safe;
    only actually done once per process (not per-request) via this
    module-level flag, since PRAGMA table_info introspection on every
    request would be wasteful.
    """
    global _migrations_applied
    if _migrations_applied:
        return
    import init_dev_db

    init_dev_db.init_dev_db(DEV_DB)
    _migrations_applied = True


def get_conn():
    ensure_dev_db()
    conn = sqlite3.connect(DEV_DB, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn
