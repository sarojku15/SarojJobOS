"""
Phase 9 API database access.

By default -- and for every normal developer launch
(`uvicorn api.main:app --reload --port 8420`) -- always opens
data/applications/jobos_dev.db, exactly as before. There is still no
header or request parameter anywhere in this API that can redirect a
CLIENT to a different database; that surface is unchanged.

The one narrow, explicit exception: an operator (never a client
request) can launch a SEPARATE process pointed at the production
database by setting the JOBOS_DB_PATH environment variable before this
module is first imported -- see scripts/run_production_api.py, the
only place this repository ever does that. Reading it once, here, at
import time (not per-request) means a stray environment variable set
in some unrelated shell can never retroactively redirect an
already-running dev server; the value is fixed for the lifetime of
whichever process imported this module.
"""

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"
PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"

_db_path_override = os.environ.get("JOBOS_DB_PATH")
DEV_DB = Path(_db_path_override).resolve() if _db_path_override else DEFAULT_DEV_DB

# 2026-09-29 startup/database safety hardening -- root-caused during a
# live investigation: port 8421 (this project's documented production
# port) was found actually running as a bare `uvicorn api.main:app
# --port 8421` process, which never sets JOBOS_DB_PATH and therefore
# silently opened jobos_dev.db instead of the real production DB, with
# no error, no warning -- just a wrong `db` field in /api/health that
# nobody was checking. IS_PRODUCTION/JOBOS_DB_PATH_CONFIGURED are the
# two facts every downstream safeguard below is built from: whether
# THIS process's env explicitly requested a DB path at all (never
# re-read from the live environment later -- see this module's own
# top-of-file note on why JOBOS_DB_PATH is captured once, at import),
# and whether that path genuinely resolves to the real production file
# (catches a JOBOS_DB_PATH typo too, not just "was it set").
JOBOS_DB_PATH_CONFIGURED = bool(_db_path_override)
IS_PRODUCTION = DEV_DB.resolve() == PRODUCTION_DB.resolve()

# Resume files now follow the SAME dev/production separation the
# database itself already had -- previously api/main.py's RESUME_DIR
# was a hardcoded constant with no JOBOS_DB_PATH-equivalent override,
# so even a correctly-launched production process (real jobos.db) still
# wrote every uploaded resume into resumes_dev/. Never moves or deletes
# any existing file at either path -- this only decides where a NEW
# upload from THIS process goes.
RESUME_DIR = ROOT / "data" / "applications" / ("resumes" if IS_PRODUCTION else "resumes_dev")

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

    Never runs against the production DB path: init_dev_db.init_dev_db()
    itself refuses that (a deliberate guard), and production schema is
    only ever brought up to date explicitly, out-of-band, via
    scripts/migrate_production_schema.py -- never silently auto-run by
    a request to this API, unlike the dev DB's own convenience
    auto-migration.
    """
    global _migrations_applied
    if _migrations_applied:
        return
    if DEV_DB.resolve() == PRODUCTION_DB.resolve():
        _migrations_applied = True
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
