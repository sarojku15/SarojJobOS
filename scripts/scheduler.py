#!/usr/bin/env python3
"""
Phase 4: recurring saved-search scheduling.

Fully persisted, no in-memory state at all (see search_schedules,
migrate_v14_search_schedules.py) -- restart-safe by construction, and
never a fragile in-process background thread/loop (this project has
deliberately never had one; see scripts/run_search_worker.py's own
"no daemon/loop mode" docstring, which this module follows exactly).

run_due_schedules() is the one bounded, single-pass primitive (same
convention as search_worker.run_once()): find every schedule that is
due, advance its own next_run_at FIRST (via an optimistic
compare-and-swap UPDATE keyed on the next_run_at value just read, so a
process crash between "marked due" and "actually triggered" can only
ever cause a MISSED tick, never a duplicate one), THEN call the
EXISTING search_store.trigger_run() -- which already refuses to start
a second run if one is QUEUED/RUNNING (api/search_store.
get_active_run_for_search()) -- never a second, independent
concurrent-run check here.

Intervals are simple, deterministic elapsed-time additions (hourly = +1
hour, daily = +1 day, weekly = +7 days) from the last due-check, not
calendar-aware "run at 8am local time" scheduling -- a deliberate,
documented scope decision (this project's own architecture has no
existing time-of-day/cron-expression concept to build on, and adding
one is a larger feature than "make the existing dead schedule_json
field actually do something"). `timezone` is persisted for display/
future use.
"""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

VALID_FREQUENCIES = ("hourly", "daily", "weekly")

_INTERVALS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


class SchedulerError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def compute_next_run(frequency, after=None):
    if frequency not in VALID_FREQUENCIES:
        raise SchedulerError(f"Unsupported frequency: {frequency!r} (must be one of {VALID_FREQUENCIES})")
    base = after if after is not None else _now()
    return base + _INTERVALS[frequency]


def get_schedule(conn, saved_search_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM search_schedules WHERE saved_search_id = ?", (saved_search_id,)
    ).fetchone()
    return dict(row) if row else None


def set_schedule(conn, candidate_id, saved_search_id, enabled, frequency=None, timezone_name="UTC"):
    """
    Enable/disable/reconfigure scheduling for one saved search.
    Enabling computes a fresh next_run_at (now + one interval) --
    re-enabling an already-enabled schedule with a new frequency
    re-anchors from now, never leaves a stale next_run_at from a
    different frequency in place.
    """
    if enabled and frequency not in VALID_FREQUENCIES:
        raise SchedulerError(f"frequency must be one of {VALID_FREQUENCIES} when enabling a schedule")

    now = _now()
    next_run_at = _iso(compute_next_run(frequency, now)) if enabled else None
    existing = get_schedule(conn, saved_search_id)

    if existing:
        conn.execute(
            """
            UPDATE search_schedules
            SET enabled = ?, frequency = ?, timezone = ?, next_run_at = ?, updated_at = ?
            WHERE saved_search_id = ?
            """,
            (1 if enabled else 0, frequency, timezone_name, next_run_at, _iso(now), saved_search_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO search_schedules (
                saved_search_id, candidate_id, enabled, frequency, timezone,
                next_run_at, last_run_at, last_run_status, last_run_error,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
            """,
            (saved_search_id, candidate_id, 1 if enabled else 0, frequency, timezone_name,
             next_run_at, _iso(now), _iso(now)),
        )
    conn.commit()
    return get_schedule(conn, saved_search_id)


def run_due_schedules(conn, db_path):
    """
    One bounded pass: process every currently-due, enabled schedule
    and return a list of per-schedule outcome dicts. Never loops,
    never daemonizes -- see scripts/run_scheduled_searches.py for the
    CLI wrapper meant to be invoked repeatedly by an external trigger
    (launchd/cron/n8n Schedule node), exactly like run_search_worker.py.
    """
    import search_store

    now = _now()
    conn.row_factory = sqlite3.Row
    due = conn.execute(
        "SELECT * FROM search_schedules WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?",
        (_iso(now),),
    ).fetchall()

    outcomes = []
    for row in due:
        saved_search_id = row["saved_search_id"]
        candidate_id = row["candidate_id"]
        original_next_run_at = row["next_run_at"]
        frequency = row["frequency"]

        new_next_run_at = _iso(compute_next_run(frequency, now))
        claimed = conn.execute(
            "UPDATE search_schedules SET next_run_at = ? WHERE saved_search_id = ? AND next_run_at = ?",
            (new_next_run_at, saved_search_id, original_next_run_at),
        )
        conn.commit()
        if claimed.rowcount == 0:
            # Another process already advanced this tick (or it was
            # disabled in the meantime) -- never process it twice.
            continue

        outcome = {"saved_search_id": saved_search_id, "candidate_id": candidate_id}
        try:
            saved_search = search_store.get_saved_search_for_candidate(conn, saved_search_id, candidate_id)
            trigger_result = search_store.trigger_run(conn, db_path, saved_search)
            if trigger_result["already_running"]:
                status = "SKIPPED_ALREADY_RUNNING"
                error = None
            else:
                status = trigger_result["status"]
                error = None
            outcome.update({"status": status, "run_id": trigger_result["search_run_id"], "error": error})
        except Exception as error:  # noqa: BLE001 -- must persist ANY real failure, never crash the whole pass
            status = "FAILED"
            error_message = str(error)
            outcome.update({"status": status, "run_id": None, "error": error_message})

        conn.execute(
            "UPDATE search_schedules SET last_run_at = ?, last_run_status = ?, last_run_error = ? WHERE saved_search_id = ?",
            (_iso(now), outcome["status"], outcome.get("error"), saved_search_id),
        )
        conn.commit()
        outcomes.append(outcome)

    return outcomes
