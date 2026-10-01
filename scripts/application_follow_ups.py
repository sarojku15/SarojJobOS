#!/usr/bin/env python3

"""
Follow-up scheduling with real, non-destructive history (2026-09-28
application-tracker implementation).

Root cause this fixes: before this module, a follow-up was a single
overwritable candidate_job_matches.follow_up_date column -- clearing it
(the only "done" action that existed) looked identical to a follow-up
that was never scheduled at all, and there was no record that a
follow-up was ever acted on. The audit also found the dashboard and
the n8n reminder workflow each independently recomputed "is this due"
with slightly different (and inconsistent) date comparisons.

This module is the ONE canonical place both facts now live:

  1. application_follow_ups -- one row per follow-up INSTANCE
     (migrate_v16_application_tracking.py), in status PENDING /
     COMPLETED / CANCELLED. Never deleted, never overwritten in place
     once it leaves PENDING -- "Follow Up Now" and "Reschedule" both
     close out the current PENDING row (if any) and, for reschedule,
     open a new one; the closed row's own history is permanent.

  2. compute_due_state() -- the single is_overdue/is_due_today/is_due/
     is_upcoming definition every consumer (the API, the dashboard, the
     n8n workflow) now shares, computed server-side, UTC calendar-date
     comparison (consistent with every other timestamp in this project
     being UTC ISO-8601 -- see application_lifecycle.py's _now_iso()).

candidate_job_matches.follow_up_date is NOT removed or reinterpreted --
it continues to mean exactly what it always has ("the current active
PENDING follow-up's due date, or NULL"), kept in sync by every function
below, so the existing PATCH .../follow-up endpoint, the existing
GET .../follow-ups endpoint's basic shape, the dashboard, and the n8n
workflow's own JobOS Config node all keep working unchanged. This
module only adds the missing history and canonical due-state on top.
"""

import sqlite3
import uuid
from datetime import datetime, timezone, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class FollowUpError(ValueError):
    """Raised for an unknown (candidate_id, job_id) pair, or an action
    (complete/reschedule/cancel) attempted with no PENDING follow-up."""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _today_date_str():
    return datetime.now(timezone.utc).date().isoformat()


def compute_due_state(due_date_str, today_str=None):
    """
    The one canonical due-state computation. Pure function, no DB
    access -- reused identically by the API layer and by this
    project's tests. `due_date_str` is a plain ISO date string
    ("YYYY-MM-DD"), never a datetime (a follow-up is a reminder DATE,
    not a timestamp -- see the existing FollowUpDateIn's own comment).

    Returns a dict with is_overdue / is_due_today / is_due / is_upcoming
    -- exactly the four semantics the read-only audit specified:

        is_overdue:  due_date <  today
        is_due_today: due_date == today
        is_due:      due_date <= today   (overdue OR due today)
        is_upcoming: due_date >  today
    """
    today_str = today_str or _today_date_str()
    today = date.fromisoformat(today_str)
    due = date.fromisoformat(due_date_str)
    overdue = due < today
    due_today = due == today
    return {
        "is_overdue": overdue,
        "is_due_today": due_today,
        "is_due": overdue or due_today,
        "is_upcoming": due > today,
    }


def _verify_match_exists(conn, candidate_id, job_id):
    row = conn.execute(
        "SELECT 1 FROM candidate_job_matches WHERE candidate_id = ? AND job_id = ?",
        (candidate_id, job_id),
    ).fetchone()
    if row is None:
        raise FollowUpError(
            f"No match record for candidate {candidate_id!r} and job {job_id!r}."
        )


def _current_pending_row(conn, candidate_id, job_id):
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM application_follow_ups WHERE candidate_id = ? AND job_id = ? "
        "AND status = 'PENDING' ORDER BY created_at DESC LIMIT 1",
        (candidate_id, job_id),
    ).fetchone()


def create_follow_up(conn, candidate_id, job_id, due_date, notes=None):
    """
    Schedule a follow-up for (candidate_id, job_id) on `due_date`
    (ISO date string). If a PENDING follow-up already exists for this
    pair, it is marked CANCELLED first (its own history row is kept,
    never deleted/overwritten) -- this is also what backs the
    dedicated "Reschedule" action below. Keeps
    candidate_job_matches.follow_up_date in sync for backward
    compatibility with every existing reader.
    """
    _verify_match_exists(conn, candidate_id, job_id)

    existing = _current_pending_row(conn, candidate_id, job_id)
    now = _now_iso()
    if existing is not None:
        conn.execute(
            "UPDATE application_follow_ups SET status = 'CANCELLED', updated_at = ? WHERE follow_up_id = ?",
            (now, existing["follow_up_id"]),
        )

    follow_up_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO application_follow_ups "
        "(follow_up_id, candidate_id, job_id, due_date, status, notes, completed_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'PENDING', ?, NULL, ?, ?)",
        (follow_up_id, candidate_id, job_id, due_date, notes, now, now),
    )
    conn.execute(
        "UPDATE candidate_job_matches SET follow_up_date = ?, updated_at = ? WHERE candidate_id = ? AND job_id = ?",
        (due_date, now, candidate_id, job_id),
    )
    conn.commit()
    return get_follow_up_history(conn, candidate_id, job_id)[-1]


# Reschedule is create_follow_up under a task-facing name: it already
# cancels any current PENDING row (preserving its history) and opens a
# new one, exactly matching "preserve the completed/old follow-up,
# create/update the next pending follow-up."
def reschedule_follow_up(conn, candidate_id, job_id, new_due_date, notes=None):
    return create_follow_up(conn, candidate_id, job_id, new_due_date, notes=notes)


def complete_follow_up(conn, candidate_id, job_id, notes=None):
    """
    "Follow Up Now": marks the CURRENT PENDING follow-up COMPLETED
    (completed_at set, row never deleted) and clears
    candidate_job_matches.follow_up_date (there is no longer an ACTIVE
    reminder). Scheduling the NEXT follow-up is a separate, subsequent
    call to create_follow_up()/reschedule_follow_up() -- this function
    does not invent one.
    """
    _verify_match_exists(conn, candidate_id, job_id)
    existing = _current_pending_row(conn, candidate_id, job_id)
    if existing is None:
        raise FollowUpError(
            f"No pending follow-up to complete for candidate {candidate_id!r} and job {job_id!r}."
        )
    now = _now_iso()
    merged_notes = notes if notes is not None else existing["notes"]
    conn.execute(
        "UPDATE application_follow_ups SET status = 'COMPLETED', completed_at = ?, notes = ?, updated_at = ? "
        "WHERE follow_up_id = ?",
        (now, merged_notes, now, existing["follow_up_id"]),
    )
    conn.execute(
        "UPDATE candidate_job_matches SET follow_up_date = NULL, updated_at = ? WHERE candidate_id = ? AND job_id = ?",
        (now, candidate_id, job_id),
    )
    conn.commit()
    return get_follow_up_history(conn, candidate_id, job_id)[-1]


def cancel_follow_up(conn, candidate_id, job_id):
    """
    Cancel the current PENDING follow-up without claiming it was
    completed -- a distinct terminal state ("decided not to follow up
    after all") from COMPLETED ("I did follow up"). Also backs the
    EXISTING PATCH .../follow-up endpoint's "clear the date" behavior,
    now upgraded to preserve history instead of silently losing it.
    """
    _verify_match_exists(conn, candidate_id, job_id)
    existing = _current_pending_row(conn, candidate_id, job_id)
    now = _now_iso()
    if existing is not None:
        conn.execute(
            "UPDATE application_follow_ups SET status = 'CANCELLED', updated_at = ? WHERE follow_up_id = ?",
            (now, existing["follow_up_id"]),
        )
    conn.execute(
        "UPDATE candidate_job_matches SET follow_up_date = NULL, updated_at = ? WHERE candidate_id = ? AND job_id = ?",
        (now, candidate_id, job_id),
    )
    conn.commit()
    return get_follow_up_history(conn, candidate_id, job_id)


def get_follow_up_history(conn, candidate_id, job_id):
    """Ordered (oldest first) application_follow_ups rows for one
    (candidate_id, job_id) pair -- [] if none were ever scheduled."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT follow_up_id, candidate_id, job_id, due_date, status, notes, completed_at, created_at, updated_at "
        "FROM application_follow_ups WHERE candidate_id = ? AND job_id = ? ORDER BY created_at ASC",
        (candidate_id, job_id),
    ).fetchall()
    return [dict(r) for r in rows]


def list_follow_ups_with_state(conn, candidate_id, due_only=False):
    """
    The canonical, enriched replacement body for
    GET /api/candidates/{id}/follow-ups -- every job this candidate
    currently has an ACTIVE pending follow-up on (follow_up_date IS NOT
    NULL, unchanged scope from before this module existed), each row
    now carrying the canonical is_overdue/is_due_today/is_due/
    is_upcoming state plus the associated PENDING application_follow_
    ups row's own id/notes/status, so no consumer ever needs to
    recompute "is this due" itself again.
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT cjm.job_id, cjm.follow_up_date, cjm.candidate_status,
               j.title, j.company, j.job_url, j.application_url
        FROM candidate_job_matches cjm
        JOIN jobs j ON j.job_id = cjm.job_id
        WHERE cjm.candidate_id = ? AND cjm.follow_up_date IS NOT NULL
        ORDER BY cjm.follow_up_date ASC
        """,
        (candidate_id,),
    ).fetchall()

    today_str = _today_date_str()
    results = []
    for row in rows:
        entry = dict(row)
        entry.update(compute_due_state(entry["follow_up_date"], today_str))

        pending = _current_pending_row(conn, candidate_id, entry["job_id"])
        entry["follow_up_id"] = pending["follow_up_id"] if pending else None
        entry["follow_up_status"] = pending["status"] if pending else None
        entry["completed_at"] = pending["completed_at"] if pending else None
        entry["follow_up_notes"] = pending["notes"] if pending else None

        if due_only and not entry["is_due"]:
            continue
        results.append(entry)

    return results
