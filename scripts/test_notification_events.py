#!/usr/bin/env python3

"""
Regression tests for scripts/notification_events.py -- Phase 4's
provider-neutral, testable (never real-sending) notification seam for
due follow-ups. Fully offline, isolated temp DB. No real Slack/Email/
Telegram call is possible from this module at all in this
implementation -- see its own module docstring.

Covers exactly the 5 scenarios the master task specified:
  A. one due follow-up -> one notification request
  B. rerunning the workflow does not duplicate
  C. a completed follow-up -> no notification
  D. a future follow-up -> no notification
  E. a cancelled follow-up -> no notification
plus the "no provider configured" honesty requirement and candidate
isolation.
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


import init_dev_db
import application_follow_ups as fu
import notification_events as ne

tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_notification_events_"))
db_path = init_dev_db.init_dev_db(tmp_dir / "test.db")
conn = sqlite3.connect(db_path)
conn.execute("PRAGMA foreign_keys = ON")

now = "2026-09-28T00:00:00+00:00"
conn.execute("INSERT INTO candidates (candidate_id, name, created_at, updated_at, status) VALUES ('cand-ne-a','A',?,?,'ACTIVE')", (now, now))
conn.execute("INSERT INTO candidates (candidate_id, name, created_at, updated_at, status) VALUES ('cand-ne-b','B',?,?,'ACTIVE')", (now, now))
for jid in ("j1", "j2", "j3"):
    conn.execute(
        "INSERT INTO jobs (job_id, source, company, title, location, job_url, application_url, discovered_at) VALUES (?,'FAKE','Co','Role','Loc','http://x','http://x',?)",
        (jid, now),
    )
    conn.execute(
        "INSERT INTO candidate_job_matches (candidate_id, job_id, candidate_status, created_at, updated_at) VALUES ('cand-ne-a',?,'APPLIED',?,?)",
        (jid, now, now),
    )
conn.commit()

# j1: overdue (due), j2: far future (not due), j3: due but will be completed
fu.create_follow_up(conn, "cand-ne-a", "j1", "2020-01-01")
fu.create_follow_up(conn, "cand-ne-a", "j2", "2099-01-01")
fu.create_follow_up(conn, "cand-ne-a", "j3", "2020-01-01")
fu.complete_follow_up(conn, "cand-ne-a", "j3")

due = fu.list_follow_ups_with_state(conn, "cand-ne-a", due_only=True)
check(len(due) == 1 and due[0]["job_id"] == "j1", f"setup: only j1 is due (j2 future, j3 completed), got {[d['job_id'] for d in due]}")

# --- A. one due follow-up -> one notification request ---
results = ne.request_notifications_for_due_follow_ups(conn, "cand-ne-a", due)
check(len(results) == 1, f"A. one due follow-up produces exactly one notification request, got {len(results)}")
check(results[0]["status"] == "NOT_REQUESTED", f"A. no provider configured -> status NOT_REQUESTED (never fabricated SENT), got {results[0]['status']}")

# --- B. rerunning does not duplicate ---
results2 = ne.request_notifications_for_due_follow_ups(conn, "cand-ne-a", due)
check(results2[0]["event_id"] == results[0]["event_id"], "B. rerunning for the same due follow-up on the same day returns the SAME event, not a new one")
count = conn.execute("SELECT COUNT(*) FROM notification_events WHERE follow_up_id = ?", (due[0]["follow_up_id"],)).fetchone()[0]
check(count == 1, f"B. exactly 1 notification_events row exists after 2 identical requests, got {count}")

# --- direct duplicate-call check (same function, same args) ---
direct_dup = ne.request_notification(conn, "cand-ne-a", due[0]["follow_up_id"])
check(direct_dup["event_id"] == results[0]["event_id"], "B2. calling request_notification() directly a second time is also deduplicated")

# --- C. completed follow-up -> no notification ---
all_job_ids_notified = {r["follow_up_id"] for r in results}
j3_pending = fu._current_pending_row(conn, "cand-ne-a", "j3")
check(j3_pending is None, "C. j3 has no PENDING follow-up (it was completed) -- never eligible for a notification request in the first place")

# --- D. future follow-up -> no notification ---
due_job_ids = {d["job_id"] for d in due}
check("j2" not in due_job_ids, "D. j2 (due 2099-01-01) never appears in the due list -- no notification requested")

# --- E. cancelled follow-up -> no notification ---
fu.create_follow_up(conn, "cand-ne-a", "j2", "2020-06-01")  # reschedule j2 to overdue
fu.cancel_follow_up(conn, "cand-ne-a", "j2")
due_after_cancel = fu.list_follow_ups_with_state(conn, "cand-ne-a", due_only=True)
check("j2" not in {d["job_id"] for d in due_after_cancel}, f"E. a cancelled follow-up (even if its due_date is in the past) never appears as due, got {[d['job_id'] for d in due_after_cancel]}")

# --- honesty: "Notification not configured" rather than a silent/fabricated SENT ---
status = ne.get_notification_status(conn, "cand-ne-a", results[0]["follow_up_id"])
check(status["friendly_message"] == "Notification not configured", f"honesty: no real provider wired in -> 'Notification not configured', got {status['friendly_message']}")
check(status["status"] != "SENT", "honesty: status is never fabricated as SENT without a real provider confirming delivery")

# --- ownership: candidate B cannot request a notification for candidate A's follow-up ---
try:
    ne.request_notification(conn, "cand-ne-b", due[0]["follow_up_id"])
    check(False, "ownership: candidate B must not be able to request a notification for candidate A's follow_up_id")
except ne.NotificationEventError:
    check(True, "ownership: candidate B requesting a notification for candidate A's follow_up_id is rejected")

# --- ownership: candidate B sees no notification status for candidate A's follow-up ---
status_b = ne.get_notification_status(conn, "cand-ne-b", due[0]["follow_up_id"])
check(status_b is None, "ownership: candidate B's own notification-status lookup for candidate A's follow-up returns nothing")

conn.close()
shutil.rmtree(tmp_dir, ignore_errors=True)

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
