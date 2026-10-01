#!/usr/bin/env python3

"""
Focused API regression tests for the Application Tracker (2026-09-28):
filtering on GET /applications, notes CRUD, Mark as Applied edge
cases (idempotency, resume re-confirmation, non-APPROVED rejection),
and explicit per-endpoint candidate-ownership checks for every new
route this implementation added.

Complements scripts/test_e2e_application_tracker.py (the full 17-step
happy-path flow) -- this file targets the edge cases and the exact
ownership checklist from the master task's Section 16.

Fully offline, isolated temp DB, FastAPI TestClient. Production DB
verified byte-identical before/after.
"""

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

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


tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_applications_api_"))
tmp_db = tmp_dir / "test.db"

import init_dev_db
init_dev_db.init_dev_db(tmp_db)
import db as db_mod
db_mod.DEV_DB = tmp_db
from fastapi.testclient import TestClient
import main as api_main
api_main.db_mod.DEV_DB = tmp_db
client = TestClient(api_main.app)

resp = client.post("/api/candidates", json={"name": "API Test A"})
candidate_a = resp.json()["candidate_id"]
resp = client.post("/api/candidates", json={"name": "API Test B"})
candidate_b = resp.json()["candidate_id"]

conn = db_mod.get_conn()
now = "2026-09-28T00:00:00+00:00"
jobs = [
    ("jA1", "NAUKRI", "Acme", "SRE", "APPLIED"),
    ("jA2", "HIRIST", "Beta Corp", "DevOps Engineer", "APPROVED"),
    ("jA3", "NAUKRI", "Acme", "Platform Engineer", "SHORTLISTED"),
    ("jA4", "APNA", "Gamma Inc", "Cloud Engineer", "FOUND"),
]
for job_id, source, company, title, status in jobs:
    conn.execute(
        "INSERT INTO jobs (job_id, source, company, title, location, job_url, application_url, discovered_at) "
        "VALUES (?, ?, ?, ?, 'Remote', 'http://x', 'http://x', ?)",
        (job_id, source, company, title, now),
    )
    applied_at = now if status == "APPLIED" else None
    conn.execute(
        "INSERT INTO candidate_job_matches (candidate_id, job_id, candidate_status, fit_score, applied_at, created_at, updated_at) "
        "VALUES (?, ?, ?, 80, ?, ?, ?)",
        (candidate_a, job_id, status, applied_at, now, now),
    )
conn.commit()
conn.close()

# --- A. candidate applications list ---
resp = client.get(f"/api/candidates/{candidate_a}/applications")
apps = resp.json()["applications"]
job_ids = {a["job_id"] for a in apps}
check("jA4" not in job_ids, "A. FOUND (never acted on) is excluded from the default unfiltered list")
check({"jA1", "jA2", "jA3"} <= job_ids, f"A. SHORTLISTED/APPROVED/APPLIED all appear by default, got {job_ids}")

resp = client.get(f"/api/candidates/{candidate_a}/applications?status=FOUND")
check({a["job_id"] for a in resp.json()["applications"]} == {"jA4"}, "A. ?status=FOUND explicitly returns the excluded status when asked")

resp = client.get(f"/api/candidates/{candidate_a}/applications?status=APPLIED")
check({a["job_id"] for a in resp.json()["applications"]} == {"jA1"}, "A. status filter works")

resp = client.get(f"/api/candidates/{candidate_a}/applications?company=Acme")
check({a["job_id"] for a in resp.json()["applications"]} == {"jA1", "jA3"}, "A. company filter (substring match) works")

resp = client.get(f"/api/candidates/{candidate_a}/applications?source=HIRIST")
check({a["job_id"] for a in resp.json()["applications"]} == {"jA2"}, "A. source filter (exact match) works")

resp = client.get(f"/api/candidates/{candidate_b}/applications")
check(resp.json()["applications"] == [], "A. candidate B's applications list returns nothing (candidate A's rows never leak)")

# --- B. Mark as Applied edge cases ---
resp = client.post(f"/api/candidates/{candidate_a}/jobs/jA3/mark-applied", json={})
check(resp.status_code == 400, f"B. Mark as Applied on a SHORTLISTED (non-APPROVED) job is rejected, got {resp.status_code}")

resp = client.post(f"/api/candidates/{candidate_a}/jobs/jA2/mark-applied", json={"resume_variant": "v1"})
check(resp.status_code == 200 and resp.json()["candidate_status"] == "APPLIED", f"B. Mark as Applied on an APPROVED job succeeds, got {resp.status_code}")
first_applied_at = resp.json()["applied_at"]
check(first_applied_at is not None, "B. applied_at populated on first Mark as Applied")

# calling it again (already APPLIED) must not move applied_at, but CAN update resume/notes
resp = client.post(f"/api/candidates/{candidate_a}/jobs/jA2/mark-applied", json={"resume_variant": "v2-corrected", "notes": "typo fix"})
check(resp.status_code == 200, f"B. Mark as Applied again (already APPLIED) still succeeds (idempotent, not an error), got {resp.status_code}")
check(resp.json()["applied_at"] == first_applied_at, "B. applied_at is NOT moved by a second Mark as Applied call")
check(resp.json()["applied_resume_variant"] == "v2-corrected", "B. a second Mark as Applied call CAN still update the resume actually used")
check(resp.json()["notes"] == "typo fix", "B. a second Mark as Applied call CAN still update notes")

# status-history: a second Mark as Applied call legitimately records
# its own APPLIED->APPLIED confirmation (the resume/notes DID change,
# so this is real, accurate audit information, not corruption) --
# what must NEVER happen is the history losing track of the ORIGINAL
# transition or getting corrupted into a non-APPLIED status.
resp = client.get(f"/api/candidates/{candidate_a}/jobs/jA2/status-history")
history = resp.json()["history"]
check(resp.json()["current_status"] == "APPLIED", "B. current_status is still APPLIED after a second Mark as Applied call")
check(all(h["to_status"] == "APPLIED" for h in history if h["from_status"] in (None, "APPROVED", "APPLIED")), f"B. status-history was never corrupted by the second Mark as Applied call, got {history}")

# no resume/notes supplied at all -- never fabricated, still succeeds.
resp = client.post(f"/api/candidates/{candidate_a}/jobs/jA1/mark-applied", json={})
check(resp.status_code == 200 and resp.json()["applied_resume_id"] is None and resp.json()["applied_resume_variant"] is None, f"B. Mark as Applied with no resume info never fabricates one, got {resp.json()}")

# --- C. Notes ---
resp = client.patch(f"/api/candidates/{candidate_a}/jobs/jA1/notes", json={"notes": "First note"})
check(resp.status_code == 200 and resp.json()["notes"] == "First note", "C. create note")
resp = client.patch(f"/api/candidates/{candidate_a}/jobs/jA1/notes", json={"notes": "Updated note"})
check(resp.status_code == 200 and resp.json()["notes"] == "Updated note", "C. update (overwrite) note")
resp = client.get(f"/api/candidates/{candidate_a}/applications?status=APPLIED")
own = next(a for a in resp.json()["applications"] if a["job_id"] == "jA1")
check(own["notes"] == "Updated note", "C. note retrievable via My Applications")
resp = client.patch(f"/api/candidates/{candidate_a}/jobs/jA1/notes", json={"notes": None})
check(resp.json()["notes"] is None, "C. clearing a note works")

# --- D. Follow-up scenarios (due/overdue/upcoming, in addition to E2E's happy path) ---
resp = client.post(f"/api/candidates/{candidate_a}/jobs/jA1/follow-up/schedule", json={"due_date": "2099-12-31"})
check(resp.status_code == 200, f"D. schedule an UPCOMING follow-up, got {resp.status_code}")
resp = client.get(f"/api/candidates/{candidate_a}/follow-ups")
row = next(r for r in resp.json()["follow_ups"] if r["job_id"] == "jA1")
check(row["is_upcoming"] is True and row["is_due"] is False, f"D. a far-future follow-up is correctly classified is_upcoming, not due, got {row}")

import datetime
today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
resp = client.post(f"/api/candidates/{candidate_a}/jobs/jA1/follow-up/schedule", json={"due_date": today_str})
resp = client.get(f"/api/candidates/{candidate_a}/follow-ups")
row = next(r for r in resp.json()["follow_ups"] if r["job_id"] == "jA1")
check(row["is_due_today"] is True and row["is_overdue"] is False and row["is_due"] is True, f"D. a today-due follow-up is correctly classified is_due_today (not overdue), got {row}")

resp = client.post(f"/api/candidates/{candidate_a}/jobs/jA1/follow-up/cancel")
check(resp.status_code == 200, f"D. cancel a pending follow-up succeeds, got {resp.status_code}")
resp = client.get(f"/api/candidates/{candidate_a}/follow-ups")
check(not any(r["job_id"] == "jA1" for r in resp.json()["follow_ups"]), "D. a cancelled follow-up no longer appears in the active list")
resp = client.get(f"/api/candidates/{candidate_a}/jobs/jA1/follow-up-history")
check(any(h["status"] == "CANCELLED" for h in resp.json()["history"]), "D. the cancellation is preserved in history, not deleted")

# --- E. legacy PATCH .../follow-up still works and now also creates history ---
resp = client.patch(f"/api/candidates/{candidate_a}/jobs/jA1/follow-up", json={"follow_up_date": "2030-01-01"})
check(resp.status_code == 200 and resp.json()["follow_up_date"] == "2030-01-01", f"E. legacy PATCH .../follow-up endpoint still works unchanged, got {resp.status_code}")
resp = client.get(f"/api/candidates/{candidate_a}/jobs/jA1/follow-up-history")
check(any(h["due_date"] == "2030-01-01" and h["status"] == "PENDING" for h in resp.json()["history"]), "E. the legacy PATCH endpoint now also creates a real history row")
resp = client.patch(f"/api/candidates/{candidate_a}/jobs/jA1/follow-up", json={"follow_up_date": None})
check(resp.status_code == 200, f"E. legacy PATCH .../follow-up clearing still works, got {resp.status_code}")
resp = client.get(f"/api/candidates/{candidate_a}/jobs/jA1/follow-up-history")
check(any(h["due_date"] == "2030-01-01" and h["status"] == "CANCELLED" for h in resp.json()["history"]), "E. clearing via the legacy endpoint now preserves history as CANCELLED instead of silently losing it")

# --- Section 16: explicit ownership checklist, one check per new endpoint ---
resp = client.get(f"/api/candidates/{candidate_b}/applications")
check(resp.status_code == 200 and resp.json()["applications"] == [], "ownership: candidate B cannot view candidate A's applications")

resp = client.post(f"/api/candidates/{candidate_b}/jobs/jA1/mark-applied", json={})
check(resp.status_code == 404, f"ownership: candidate B cannot update (mark-applied) candidate A's application, got {resp.status_code}")

resp = client.patch(f"/api/candidates/{candidate_b}/jobs/jA1/status", json={"status": "WITHDRAWN"})
check(resp.status_code == 404, f"ownership: candidate B cannot change candidate A's application status, got {resp.status_code}")

resp = client.get(f"/api/candidates/{candidate_b}/follow-ups")
check(resp.status_code == 200 and resp.json()["follow_ups"] == [], "ownership: candidate B cannot see candidate A's follow-ups")

resp = client.post(f"/api/candidates/{candidate_a}/jobs/jA2/follow-up/schedule", json={"due_date": today_str})
resp = client.post(f"/api/candidates/{candidate_b}/jobs/jA2/follow-up/complete", json={})
check(resp.status_code == 404, f"ownership: candidate B cannot complete candidate A's follow-up, got {resp.status_code}")

resp = client.post(f"/api/candidates/{candidate_b}/jobs/jA2/follow-up/schedule", json={"due_date": today_str})
check(resp.status_code == 404, f"ownership: candidate B cannot schedule a follow-up on candidate A's job match, got {resp.status_code}")

resp = client.post(f"/api/candidates/{candidate_b}/jobs/jA2/follow-up/cancel")
check(resp.status_code == 404, f"ownership: candidate B cannot cancel candidate A's follow-up, got {resp.status_code}")

resp = client.patch(f"/api/candidates/{candidate_b}/jobs/jA2/notes", json={"notes": "hijacked"})
check(resp.status_code == 404, f"ownership: candidate B cannot alter candidate A's notes, got {resp.status_code}")

resp = client.get(f"/api/candidates/{candidate_b}/jobs/jA2/follow-up-history")
check(resp.status_code == 404, f"ownership: candidate B cannot read candidate A's follow-up history, got {resp.status_code}")

# notification-request ownership (module-level, exercised directly since no public POST route exists yet)
import application_follow_ups as fu
import notification_events as ne
conn = db_mod.get_conn()
due_a = fu.list_follow_ups_with_state(conn, candidate_a, due_only=True)
target = next((d for d in due_a if d["job_id"] == "jA2"), None)
if target:
    try:
        ne.request_notification(conn, candidate_b, target["follow_up_id"])
        check(False, "ownership: candidate B must not be able to trigger a notification for candidate A's follow-up")
    except ne.NotificationEventError:
        check(True, "ownership: candidate B cannot trigger a notification for candidate A's follow-up")
conn.close()

import shutil
shutil.rmtree(tmp_dir, ignore_errors=True)

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
