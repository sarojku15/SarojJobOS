#!/usr/bin/env python3

"""
Per-candidate application lifecycle transitions -- the genuinely
missing write path this project's own audit found: config/
application_schema.json already defines a full status lifecycle
(FOUND -> ... -> SHORTLISTED -> READY_FOR_APPROVAL -> APPROVED ->
APPLICATION_STARTED -> APPLIED -> interview states -> terminal states)
and candidate_job_matches.candidate_status already exists to hold it
per (candidate_id, job_id) pair, but until this module nothing ever
WROTE to it after initial discovery -- there was no shortlist, no
approve, no mark-applied action anywhere in the codebase.

This module does NOT invent a second status taxonomy or a second
candidate_job_matches-like table -- it only adds the one missing
write operation over the EXISTING schema/table, reusing
config/application_schema.json's own allowed_statuses list rather
than a second, hardcoded copy.

SAFETY GATE (non-negotiable, per this project's Application Safety
rules): a job may never move to APPLICATION_STARTED or APPLIED unless
its candidate_status is ALREADY APPROVED (or further along that same
forward path) -- there is no way to skip the human-approval step
through this function. This module never submits anything itself; it
only records a status a human explicitly set via the API, exactly
like every other action in this project stops at READY_FOR_APPROVAL
until a human takes the next step themselves.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

with open(ROOT / "config" / "application_schema.json", "r", encoding="utf-8") as f:
    _SCHEMA = json.load(f)

ALLOWED_STATUSES = frozenset(_SCHEMA["allowed_statuses"])

# The one hard-enforced safety gate: neither of these two statuses is
# reachable except from APPROVED (or from each other / anything past
# them on the same forward path -- re-recording an already-applied
# job's status, or moving from APPLICATION_STARTED to APPLIED, is
# fine). Every other transition in ALLOWED_STATUSES is otherwise
# unrestricted -- this module does not attempt to model a full state
# machine for interview/rejection tracking, which are externally-driven
# facts a human is simply recording, not gated actions.
_GATED_STATUSES = frozenset({"APPLICATION_STARTED", "APPLIED"})
_PRE_APPROVAL_SATISFYING_STATUSES = frozenset({"APPROVED", "APPLICATION_STARTED", "APPLIED"})


class ApplicationLifecycleError(ValueError):
    """Raised for an unknown status value, an unknown (candidate_id,
    job_id) pair, or an attempt to skip the approval gate."""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_candidate_job_status_history(conn, candidate_id, job_id):
    """Ordered (oldest first) transition history for one (candidate_id,
    job_id) pair, from candidate_job_status_history -- see
    migrate_v6_status_history.py. Returns [] for a pair with no
    recorded transitions yet (e.g. only ever at its initial
    score-bucket status, which is seeded directly by search_worker.py,
    not via transition_candidate_job_status()). Cursor-scoped
    row_factory so this never mutates the caller's connection-wide
    default for whatever else runs on the same connection afterward."""
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute(
        "SELECT from_status, to_status, changed_at FROM candidate_job_status_history "
        "WHERE candidate_id = ? AND job_id = ? ORDER BY id ASC",
        (candidate_id, job_id),
    ).fetchall()
    return [dict(row) for row in rows]


def get_candidate_job_status(conn, candidate_id, job_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT candidate_id, job_id, candidate_status, updated_at, applied_at, "
        "applied_resume_id, applied_resume_variant, notes FROM candidate_job_matches "
        "WHERE candidate_id = ? AND job_id = ?",
        (candidate_id, job_id),
    ).fetchone()
    if row is None:
        raise ApplicationLifecycleError(
            f"No match record for candidate {candidate_id!r} and job {job_id!r} -- "
            "a job must already be an eligible match for this candidate before its "
            "application status can be changed."
        )
    return dict(row)


def transition_candidate_job_status(conn, candidate_id, job_id, new_status):
    """
    Set candidate_job_matches.candidate_status for exactly this
    (candidate_id, job_id) pair -- never any other candidate's row for
    the same GLOBAL job (candidate_job_matches is already keyed that
    way; see this module's own docstring on why this table, not
    jobs.status, is the correct place for a per-candidate lifecycle
    status).

    Raises ApplicationLifecycleError for an unrecognized status, an
    unknown match, or an attempt to reach APPLICATION_STARTED/APPLIED
    without already being APPROVED. Returns the updated row.
    """
    if new_status not in ALLOWED_STATUSES:
        raise ApplicationLifecycleError(
            f"Unknown status {new_status!r}. Allowed: {sorted(ALLOWED_STATUSES)}"
        )

    current = get_candidate_job_status(conn, candidate_id, job_id)

    if new_status in _GATED_STATUSES and current["candidate_status"] not in _PRE_APPROVAL_SATISFYING_STATUSES:
        raise ApplicationLifecycleError(
            f"Cannot move to {new_status!r} from {current['candidate_status']!r} -- "
            "a job must be APPROVED by the candidate before APPLICATION_STARTED/APPLIED. "
            "This system never submits an application automatically; the candidate must "
            "explicitly approve it first (see config/application_schema.json)."
        )

    now = _now_iso()

    # applied_at (migrate_v16_application_tracking.py): set exactly
    # ONCE, the first time this pair genuinely transitions to APPLIED
    # -- never overwritten by any later status change, including a
    # later transition back through APPLIED (e.g. APPLIED -> WITHDRAWN
    # -> APPLIED again would still preserve the ORIGINAL applied_at,
    # since this system does not model "multiple distinct application
    # events" for the same job -- the existing status-history table
    # already preserves the full transition timeline for that case).
    if new_status == "APPLIED" and current.get("applied_at") is None:
        conn.execute(
            "UPDATE candidate_job_matches SET candidate_status = ?, updated_at = ?, applied_at = ? "
            "WHERE candidate_id = ? AND job_id = ?",
            (new_status, now, now, candidate_id, job_id),
        )
    else:
        conn.execute(
            "UPDATE candidate_job_matches SET candidate_status = ?, updated_at = ? "
            "WHERE candidate_id = ? AND job_id = ?",
            (new_status, now, candidate_id, job_id),
        )
    # One history row per transition (see migrate_v6_status_history.py)
    # -- candidate_job_matches itself only ever holds the CURRENT
    # status, so this is the only place "when did this move from X to
    # Y" is reconstructable. Recorded in the same transaction as the
    # status update itself (committed together below), so a rejected
    # transition (the gate check above, or an unknown status) never
    # leaves a history row with no corresponding status change.
    conn.execute(
        "INSERT INTO candidate_job_status_history "
        "(candidate_id, job_id, from_status, to_status, changed_at) VALUES (?, ?, ?, ?, ?)",
        (candidate_id, job_id, current["candidate_status"], new_status, now),
    )
    conn.commit()

    return get_candidate_job_status(conn, candidate_id, job_id)


def mark_applied(conn, candidate_id, job_id, resume_id=None, resume_variant=None, applied_at_override=None, notes=None):
    """
    The dedicated "Mark as Applied" action (2026-09-28 application-
    tracker implementation) -- a purpose-built action distinct from the
    generic transition_candidate_job_status(new_status="APPLIED") path,
    for the one moment that most needs its own explicit confirmation:
    the resume actually used.

    Still goes through transition_candidate_job_status() underneath
    (same APPROVED-before-APPLIED gate, same status-history row, same
    applied_at-set-once semantics) -- this function never bypasses or
    duplicates that logic, it only adds the application-time facts
    (applied_resume_id/applied_resume_variant/notes/applied_at) on top.

    resume_id/resume_variant are NEVER fabricated: if the caller (the
    API route) doesn't pass them, they are simply left NULL -- the
    match-time resume_id/resume_variant columns are untouched either
    way, so the historical scoring record is always intact regardless
    of whether an application-time resume was confirmed.

    applied_at_override lets a human backfill a real, already-known
    past application date/time (e.g. "I actually applied 3 days ago,
    before I opened JobOS today") -- honored ONLY on the transition
    that actually sets applied_at for the first time; a later call
    (already applied) can still update resume/notes but can never
    move or fabricate a new applied_at, per the same preserve-the-
    original-timestamp rule transition_candidate_job_status() already
    enforces.
    """
    already_applied = get_candidate_job_status(conn, candidate_id, job_id).get("applied_at") is not None

    result = transition_candidate_job_status(conn, candidate_id, job_id, "APPLIED")

    if applied_at_override and not already_applied:
        conn.execute(
            "UPDATE candidate_job_matches SET applied_at = ? WHERE candidate_id = ? AND job_id = ?",
            (applied_at_override, candidate_id, job_id),
        )

    if resume_id is not None or resume_variant is not None:
        conn.execute(
            "UPDATE candidate_job_matches SET applied_resume_id = ?, applied_resume_variant = ? "
            "WHERE candidate_id = ? AND job_id = ?",
            (resume_id, resume_variant, candidate_id, job_id),
        )

    if notes is not None:
        conn.execute(
            "UPDATE candidate_job_matches SET notes = ? WHERE candidate_id = ? AND job_id = ?",
            (notes, candidate_id, job_id),
        )

    conn.commit()
    return get_candidate_job_status(conn, candidate_id, job_id)


def update_application_notes(conn, candidate_id, job_id, notes):
    """
    Application-scoped notes (migrate_v16_application_tracking.py) --
    candidate-owned, job/application-specific, distinct from
    interview_preparations.outcome_notes (a different, interview-
    scoped fact). A plain overwrite, same convention as the existing
    follow_up_date PATCH endpoint (None/"" clears it).
    """
    get_candidate_job_status(conn, candidate_id, job_id)  # raises if unknown pair
    conn.execute(
        "UPDATE candidate_job_matches SET notes = ?, updated_at = ? WHERE candidate_id = ? AND job_id = ?",
        (notes, _now_iso(), candidate_id, job_id),
    )
    conn.commit()
    return get_candidate_job_status(conn, candidate_id, job_id)
