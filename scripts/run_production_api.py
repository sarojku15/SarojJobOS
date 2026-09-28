#!/usr/bin/env python3
"""
Launches the SAME FastAPI app (api/main.py) as the normal dev server,
but pointed at the real production database
(data/applications/jobos.db) instead of jobos_dev.db -- for n8n/
automation use only, never for interactive development.

This is the ONLY place in the repository that sets JOBOS_DB_PATH (see
api/db.py's own docstring on this) -- setting it here, in this
process's own environ, before api.main is ever imported, means the
normal dev launch command
(`.venv/bin/uvicorn api.main:app --reload --port 8420`) is completely
unaffected: it never sets this variable, so api/db.py's default
(jobos_dev.db) applies exactly as it always has.

Production schema must already be migrated (see
scripts/migrate_production_schema.py) before this is useful -- this
script does NOT run migrations itself, by design: production schema
changes are explicit, backed-up, one-time operations, never something
a server launch silently triggers.

Usage:
    .venv/bin/python3 scripts/run_production_api.py
    .venv/bin/python3 scripts/run_production_api.py --port 8421   # default

Serves on 127.0.0.1 only (not 0.0.0.0) -- this is a local automation
endpoint for n8n (reached via host.docker.internal from inside the
n8n container), never meant to be exposed on the network.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"

DEFAULT_PORT = 8421


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the JobOS API against the production database, for automation (n8n) use only.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn's auto-reload (off by default -- this is an automation endpoint, not a dev workflow).")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)

    if not PRODUCTION_DB.exists():
        print(f"Production DB not found at {PRODUCTION_DB} -- refusing to start.", file=sys.stderr)
        return 1

    os.environ["JOBOS_DB_PATH"] = str(PRODUCTION_DB)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import uvicorn

    print(f"Starting JobOS production/automation API on 127.0.0.1:{args.port} -> {PRODUCTION_DB}")
    print("For n8n running in Docker, reach this at http://host.docker.internal:%d" % args.port)
    uvicorn.run("api.main:app", host="127.0.0.1", port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
