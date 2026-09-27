#!/usr/bin/env python3
"""
CLI for scripts/scheduler.py's single safe processing primitive
(run_due_schedules()) -- exactly the same convention as
scripts/run_search_worker.py: one bounded pass over every currently
due, enabled schedule (across every candidate), then exit. No daemon/
loop mode -- an external trigger (launchd, cron, or an n8n Schedule
node calling this script, or the equivalent
POST /api/scheduler/run-due route) is expected to invoke this
repeatedly.

Usage:
    python3 scripts/run_scheduled_searches.py --once
    python3 scripts/run_scheduled_searches.py --once --db /path/to/other.db

Defaults to the multi-candidate DEV database (data/applications/
jobos_dev.db) -- the current web app's own database -- never the
legacy single-candidate production DB scripts/run_search_worker.py
defaults to.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

DEFAULT_DB_PATH = ROOT / "data" / "applications" / "jobos_dev.db"


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run every currently due, enabled saved-search schedule once, then exit.")
    parser.add_argument("--once", action="store_true", required=True, help="Required -- there is no daemon/loop mode.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    from scheduler import run_due_schedules

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        outcomes = run_due_schedules(conn, args.db)
    finally:
        conn.close()

    if not outcomes:
        print("No schedules were due.")
        return 0

    for o in outcomes:
        print(f"search={o['saved_search_id']} candidate={o['candidate_id']} status={o['status']} run_id={o.get('run_id')} error={o.get('error')}")
    print(f"\n{len(outcomes)} schedule(s) processed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
