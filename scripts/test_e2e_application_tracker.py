#!/usr/bin/env python3

"""
Full, isolated end-to-end test for the Application Tracker + Follow-Up
system (2026-09-28 implementation) -- the exact 17-step flow specified
by the master implementation task, run entirely against a temp SQLite
DB via FastAPI's TestClient (no real server process, no real network
call, no real provider, no real notification, no production DB).

    1. Candidate confirmed.
    2. Search/job match exists in temporary DB.
    3. Job becomes READY_FOR_APPROVAL.
    4. Job becomes APPROVED.
    5. User explicitly marks APPLIED.
    6. applied_at created.
    7. actual resume recorded.
    8. application appears in My Applications.
    9. follow-up scheduled.
    10. API reports correct due state.
    11. follow-up becomes due.
    12. Follow Up Now records completion.
    13. completion remains in history.
    14. another follow-up is scheduled.
    15. notification workflow logic identifies only the pending due item.
    16. application status can transition to interview/outcome.
    17. candidate isolation verified.

Production DB verified byte-identical before and after.
"""

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"
DEV_DB = ROOT / "data" / "applications" / "jobos_dev.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)
dev_before = _sha(DEV_DB)

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


tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_e2e_app_tracker_"))
tmp_db = tmp_dir / "test.db"

import init_dev_db
init_dev_db.init_dev_db(tmp_db)

import db as db_mod
db_mod.DEV_DB = tmp_db
from fastapi.testclient import TestClient
import main as api_main
api_main.db_mod.DEV_DB = tmp_db
client = TestClient(api_main.app)

import application_follow_ups as fu
import notification_events as ne

# --- 1. Candidate confirmed ---
resp = client.post("/api/candidates", json={"name": "TEST_CANDIDATE"})
check(resp.status_code == 200, f"1. candidate created, got {resp.status_code}")
candidate_id = resp.json()["candidate_id"]

resp = client.put(f"/api/candidates/{candidate_id}/profile", json={
    "current_title": "SRE",
    "total_experience_years": 8,
    "skills": {"cloud": [{"name": "AWS"}], "containers_orchestration": [{"name": "Kubernetes"}]},
    "employment_history": [{"employer": "Acme", "title": "SRE", "start_date": "Jan 2020", "end_date": "Present", "description": "Ran Kubernetes on AWS.", "skills": ["Kubernetes", "AWS"]}],
    "job_preferences": {"target_roles": ["SRE"], "target_locations": ["Remote"]},
})
check(resp.status_code == 200, f"1. profile saved, got {resp.status_code}")
resp = client.post(f"/api/candidates/{candidate_id}/profile/confirm")
check(resp.status_code == 200, f"1. profile confirmed, got {resp.status_code}")

# --- 2. Search/job match exists in temporary DB ---
conn = db_mod.get_conn()
now = "2026-09-28T00:00:00+00:00"
conn.execute(
    "INSERT INTO jobs (job_id, source, company, title, location, work_model, jd_text, "
    "experience_required, mandatory_skills, preferred_skills, application_url, job_url, discovered_at, last_updated) "
    "VALUES ('e2e-job-1', 'FAKE', 'Baker Hughes', 'Senior SRE', 'Remote', 'Remote', "
    "'Kubernetes and AWS experience required.', '5+ years', '[]', '[]', "
    "'https://example.com/apply/1', 'https://example.com/jobs/1', ?, ?)",
    (now, now),
)
conn.execute(
    "INSERT INTO candidate_job_matches (candidate_id, job_id, candidate_status, fit_score, resume_variant, created_at, updated_at) "
    "VALUES (?, 'e2e-job-1', 'SHORTLISTED', 88, 'SRE-A', ?, ?)",
    (candidate_id, now, now),
)
conn.commit()
conn.close()
check(True, "2. a real job + candidate_job_matches row exist in the temp DB")

# --- 3. Job becomes READY_FOR_APPROVAL ---
resp = client.patch(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/status", json={"status": "READY_FOR_APPROVAL"})
check(resp.status_code == 200 and resp.json()["candidate_status"] == "READY_FOR_APPROVAL", f"3. job -> READY_FOR_APPROVAL, got {resp.status_code} {resp.json().get('candidate_status')}")

# --- 4. Job becomes APPROVED ---
resp = client.patch(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/status", json={"status": "APPROVED"})
check(resp.status_code == 200 and resp.json()["candidate_status"] == "APPROVED", f"4. job -> APPROVED, got {resp.status_code} {resp.json().get('candidate_status')}")

# Safety check: APPLIED must still be unreachable before APPROVED for a DIFFERENT job (the gate is not weakened).
conn = db_mod.get_conn()
conn.execute(
    "INSERT INTO jobs (job_id, source, company, title, location, discovered_at, last_updated) VALUES ('e2e-job-gate', 'FAKE', 'X', 'Y', 'Z', ?, ?)",
    (now, now),
)
conn.execute(
    "INSERT INTO candidate_job_matches (candidate_id, job_id, candidate_status, created_at, updated_at) VALUES (?, 'e2e-job-gate', 'SHORTLISTED', ?, ?)",
    (candidate_id, now, now),
)
conn.commit()
conn.close()
resp = client.post(f"/api/candidates/{candidate_id}/jobs/e2e-job-gate/mark-applied", json={})
check(resp.status_code == 400, f"safety: Mark as Applied on a non-APPROVED job is still rejected (gate not weakened), got {resp.status_code}")

# --- 5. User explicitly marks APPLIED ---
resp = client.post(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/mark-applied", json={
    "resume_id": "resume-e2e-1",
    "resume_variant": "Saroj_SRE_Tailored_BakerHughes_v3",
    "notes": "Applied via referral from a former colleague.",
})
check(resp.status_code == 200, f"5. Mark as Applied succeeds, got {resp.status_code}")
applied_body = resp.json()
check(applied_body["candidate_status"] == "APPLIED", f"5. candidate_status is now APPLIED, got {applied_body['candidate_status']}")

# --- 6. applied_at created ---
check(applied_body["applied_at"] is not None, "6. applied_at was set")
first_applied_at = applied_body["applied_at"]

# applied_at must never move on a later status change (re-confirm the rule end-to-end via the API).
resp = client.patch(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/status", json={"status": "RECRUITER_CONTACTED"})
check(resp.json()["applied_at"] == first_applied_at, "6b. applied_at is preserved across a later status change, never overwritten")

# --- 7. actual resume recorded ---
check(applied_body["applied_resume_id"] == "resume-e2e-1", f"7. applied_resume_id recorded, got {applied_body['applied_resume_id']}")
check(applied_body["applied_resume_variant"] == "Saroj_SRE_Tailored_BakerHughes_v3", f"7. applied_resume_variant recorded, got {applied_body['applied_resume_variant']}")

# --- 8. application appears in My Applications ---
resp = client.get(f"/api/candidates/{candidate_id}/applications")
check(resp.status_code == 200, f"8. My Applications endpoint reachable, got {resp.status_code}")
apps = resp.json()["applications"]
target = next((a for a in apps if a["job_id"] == "e2e-job-1"), None)
check(target is not None, "8. the applied job appears in My Applications")
check(target["applied_at"] == first_applied_at and target["applied_resume_variant"] == "Saroj_SRE_Tailored_BakerHughes_v3", "8. My Applications shows the real applied_at/resume, not fabricated")
check(resp.json()["summary"]["applied"] >= 1, "8. summary counts reflect the applied job")

# --- 9. follow-up scheduled ---
resp = client.post(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/follow-up/schedule", json={"due_date": "2020-01-01", "notes": "Check in after a week"})
check(resp.status_code == 200 and resp.json()["status"] == "PENDING", f"9. follow-up scheduled (PENDING), got {resp.status_code} {resp.json().get('status')}")
first_follow_up_id = resp.json()["follow_up_id"]

# --- 10. API reports correct due state ---
resp = client.get(f"/api/candidates/{candidate_id}/follow-ups")
row = next(r for r in resp.json()["follow_ups"] if r["job_id"] == "e2e-job-1")
check(row["is_overdue"] is True and row["is_due"] is True and row["is_due_today"] is False and row["is_upcoming"] is False, f"10. due-state fields are correct for a 2020-01-01 due date, got {row}")

# --- 11. follow-up becomes due (already true above -- verify the due_only filter surfaces it) ---
resp = client.get(f"/api/candidates/{candidate_id}/follow-ups?due_only=true")
check(any(r["job_id"] == "e2e-job-1" for r in resp.json()["follow_ups"]), "11. the due follow-up is surfaced by ?due_only=true")

# --- 12. Follow Up Now records completion ---
resp = client.post(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/follow-up/complete", json={})
check(resp.status_code == 200 and resp.json()["status"] == "COMPLETED", f"12. Follow Up Now completes the follow-up, got {resp.status_code} {resp.json().get('status')}")
completed_at = resp.json()["completed_at"]
check(completed_at is not None, "12. completed_at was recorded")

# --- 13. completion remains in history ---
resp = client.get(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/follow-up-history")
history = resp.json()["history"]
check(len(history) == 1 and history[0]["status"] == "COMPLETED" and history[0]["follow_up_id"] == first_follow_up_id, f"13. the completed follow-up remains in history, never deleted, got {history}")

# --- 14. another follow-up is scheduled ---
resp = client.post(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/follow-up/schedule", json={"due_date": "2020-06-01"})
check(resp.status_code == 200 and resp.json()["status"] == "PENDING", f"14. a second follow-up is scheduled, got {resp.status_code}")
second_follow_up_id = resp.json()["follow_up_id"]
check(second_follow_up_id != first_follow_up_id, "14. the second follow-up is a genuinely NEW instance, not a mutation of the first")

resp = client.get(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/follow-up-history")
check(len(resp.json()["history"]) == 2, f"14b. history now has 2 entries (the completed one + the new pending one), got {len(resp.json()['history'])}")

# --- 15. notification workflow logic identifies only the pending due item ---
resp = client.get(f"/api/candidates/{candidate_id}/follow-ups?due_only=true")
due_rows = resp.json()["follow_ups"]
check(len(due_rows) == 1 and due_rows[0]["follow_up_id"] == second_follow_up_id, f"15. exactly the new PENDING due item is identified (not the completed one), got {due_rows}")

conn = db_mod.get_conn()
notif_results = ne.request_notifications_for_due_follow_ups(conn, candidate_id, due_rows)
check(len(notif_results) == 1, f"15b. exactly one notification request produced for the one due item, got {len(notif_results)}")
notif_results_2 = ne.request_notifications_for_due_follow_ups(conn, candidate_id, due_rows)
check(notif_results_2[0]["event_id"] == notif_results[0]["event_id"], "15c. rerunning notification logic does not duplicate")
conn.close()

# --- 16. application status can transition to interview/outcome ---
resp = client.patch(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/status", json={"status": "INTERVIEW_1"})
check(resp.status_code == 200 and resp.json()["candidate_status"] == "INTERVIEW_1", f"16. status transitions to INTERVIEW_1, got {resp.status_code}")

resp = client.post(f"/api/candidates/{candidate_id}/interview-prep", json={"job_id": "e2e-job-1"})
check(resp.status_code == 200, f"16b. interview prep generated, got {resp.status_code}")
prep_id = resp.json()["interview_prep_id"]

resp = client.patch(f"/api/candidates/{candidate_id}/interview-prep/{prep_id}/outcome", json={"outcome_status": "REJECTED", "outcome_notes": "Went with an internal candidate"})
check(resp.status_code == 200, f"16c. interview outcome recorded, got {resp.status_code}")

resp = client.get(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/status-history")
check(resp.json()["current_status"] == "EMPLOYER_REJECTED", f"16d. the outcome=REJECTED bridge auto-transitions candidate_status to EMPLOYER_REJECTED, got {resp.json()['current_status']}")

# --- 17. candidate isolation verified ---
resp = client.post("/api/candidates", json={"name": "TEST_CANDIDATE_B"})
candidate_b = resp.json()["candidate_id"]

resp = client.get(f"/api/candidates/{candidate_b}/applications")
check(resp.json()["applications"] == [], "17a. candidate B's My Applications is empty (cannot see candidate A's application)")

resp = client.get(f"/api/candidates/{candidate_b}/follow-ups")
check(resp.json()["follow_ups"] == [], "17b. candidate B's follow-ups list is empty")

resp = client.post(f"/api/candidates/{candidate_b}/jobs/e2e-job-1/mark-applied", json={})
check(resp.status_code == 404, f"17c. candidate B cannot mark candidate A's job applied, got {resp.status_code}")

resp = client.post(f"/api/candidates/{candidate_b}/jobs/e2e-job-1/follow-up/complete", json={})
check(resp.status_code == 404, f"17d. candidate B cannot complete candidate A's follow-up, got {resp.status_code}")

resp = client.patch(f"/api/candidates/{candidate_b}/jobs/e2e-job-1/notes", json={"notes": "hijacked"})
check(resp.status_code == 404, f"17e. candidate B cannot alter candidate A's notes, got {resp.status_code}")

resp = client.get(f"/api/candidates/{candidate_id}/jobs/e2e-job-1/notes" if False else f"/api/candidates/{candidate_id}/applications")
own_note = next(a["notes"] for a in resp.json()["applications"] if a["job_id"] == "e2e-job-1")
check(own_note == "Applied via referral from a former colleague.", f"17f. candidate A's own note was never touched by candidate B's rejected attempt, got {own_note!r}")

import shutil
shutil.rmtree(tmp_dir, ignore_errors=True)

production_after = _sha(PRODUCTION_DB)
dev_after = _sha(DEV_DB)
check(production_before == production_after, f"production DB byte-identical before/after this E2E run (sha256 before={production_before}, after={production_after})")
if dev_before != dev_after:
    print(f"NOTE: dev DB hash changed ({dev_before} -> {dev_after}) -- expected only if the --reload dev server auto-applied a migration during this run, never from this test itself (it never touches jobos_dev.db, only a temp DB)")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
