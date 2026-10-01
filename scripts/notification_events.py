#!/usr/bin/env python3

"""
Provider-neutral, testable notification architecture for due follow-
ups (2026-09-28 application-tracker implementation, Phase 4).

Deliberately does NOT send a real notification of any kind in this
implementation -- see this module's own docstring section in the
master task: "First make notification architecture testable and
provider-neutral" before any real Slack/Email/Telegram integration is
wired in. What this module DOES provide, real and tested:

  1. A persistent record (notification_events, migrate_v16_
     application_tracking.py) of "a due follow-up was identified for
     notification" -- never a claim that anything was actually
     delivered unless a real provider later confirms it.
  2. Deduplication: exactly one notification_events row per
     (follow_up_id, cycle_date) -- enforced by a UNIQUE index, not just
     application logic -- so re-running the same daily check twice
     (e.g. n8n re-triggering, or a retried request) never produces a
     second request for the same follow-up on the same day.
  3. Honest status vocabulary: NOT_REQUESTED / QUEUED / SENT / FAILED.
     This module only ever writes NOT_REQUESTED (recording that a due
     follow-up was IDENTIFIED, nothing more -- no provider exists yet
     to queue/send/fail anything) or QUEUED (if a caller explicitly
     hands it a provider name). SENT is reserved for a real provider
     confirming delivery -- never set here.

request_notification() is idempotent per (follow_up_id, cycle_date):
calling it twice for the same still-due follow-up on the same day
returns the SAME existing row, never a duplicate.
"""

import sqlite3
import uuid
from datetime import datetime, timezone


class NotificationEventError(ValueError):
    pass


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _today_date_str():
    return datetime.now(timezone.utc).date().isoformat()


def request_notification(conn, candidate_id, follow_up_id, provider=None, cycle_date=None):
    """
    Record that `follow_up_id` was identified as due and a
    notification was (or would be) requested for it, for today's cycle
    (or `cycle_date` if explicitly given -- tests use this to simulate
    a previous day's cycle without needing to mock the system clock).

    Returns the notification_events row -- either newly created
    (status=NOT_REQUESTED if provider is None, else QUEUED) or, if one
    already exists for this exact (follow_up_id, cycle_date), the
    EXISTING row unchanged (the dedup guarantee).
    """
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT follow_up_id, candidate_id FROM application_follow_ups WHERE follow_up_id = ? AND candidate_id = ?",
        (follow_up_id, candidate_id),
    ).fetchone()
    if row is None:
        raise NotificationEventError(f"Unknown follow_up_id {follow_up_id!r} for candidate {candidate_id!r}.")

    cycle_date = cycle_date or _today_date_str()

    existing = conn.execute(
        "SELECT * FROM notification_events WHERE follow_up_id = ? AND cycle_date = ?",
        (follow_up_id, cycle_date),
    ).fetchone()
    if existing is not None:
        return dict(existing)

    now = _now_iso()
    event_id = str(uuid.uuid4())
    status = "QUEUED" if provider else "NOT_REQUESTED"
    conn.execute(
        "INSERT INTO notification_events "
        "(event_id, candidate_id, follow_up_id, cycle_date, provider, status, detail, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
        (event_id, candidate_id, follow_up_id, cycle_date, provider, status, now, now),
    )
    conn.commit()
    return dict(
        conn.execute("SELECT * FROM notification_events WHERE event_id = ?", (event_id,)).fetchone()
    )


def request_notifications_for_due_follow_ups(conn, candidate_id, due_follow_ups, provider=None, cycle_date=None):
    """
    Convenience batch wrapper: `due_follow_ups` is the list already
    produced by application_follow_ups.list_follow_ups_with_state(...,
    due_only=True) -- this function never recomputes due-ness itself
    (the ONE canonical definition stays in that module), it only
    requests a notification for each entry that actually has a
    follow_up_id (a PENDING row). Rows without one are skipped, never
    fabricated.
    """
    results = []
    for entry in due_follow_ups:
        follow_up_id = entry.get("follow_up_id")
        if not follow_up_id:
            continue
        results.append(
            request_notification(conn, candidate_id, follow_up_id, provider=provider, cycle_date=cycle_date)
        )
    return results


def get_notification_status(conn, candidate_id, follow_up_id):
    """Human-facing status for one follow-up's notification state, for
    a frontend that wants to show "Notification not configured" rather
    than silently implying nothing happened. Returns None if no
    notification was ever requested for this follow-up at all."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM notification_events WHERE candidate_id = ? AND follow_up_id = ? ORDER BY created_at DESC LIMIT 1",
        (candidate_id, follow_up_id),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    # No real provider is wired into this implementation (see module
    # docstring) -- a NOT_REQUESTED event always means "no provider
    # configured," never "something silently failed."
    result["friendly_message"] = (
        "Notification not configured" if result["status"] == "NOT_REQUESTED" else result["status"]
    )
    return result
