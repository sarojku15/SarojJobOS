#!/usr/bin/env python3

"""
Regression tests for the Phase 13 GUI/UX fixes:
  - the "Unknown candidate" inconsistency (a stale candidate_id trusted
    by the frontend without server-side validation)
  - the new "upload resume first" onboarding flow (create a placeholder
    candidate, then upload -> extraction overwrites the placeholder)
  - the results API's new matched_skills/missing_skills fields
  - re-confirmation that none of Phase 11's DRAFT-profile-results
    fix, the disabled-source rejection, the Excel download, or the
    no-auto-apply guarantee regressed

No live network/browser call of any kind -- FastAPI TestClient against
an isolated temp database, a fake offline-only source adapter
registered the same safe way every prior phase's tests do (only
source_registry.list_sources is monkeypatched, get_adapter_status()
itself is never touched, restored in a finally block).
"""

import hashlib
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
API_DIR = ROOT / "api"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(API_DIR))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)

import source_registry
from source_adapter import JobSourceAdapter, AdapterHealth, BlockReason, AdapterStatus

passed = 0
failed = 0


def check(condition, message):
    global passed, failed
    if condition:
        passed += 1
        print(f"PASS: {message}")
    else:
        failed += 1
        print(f"FAIL: {message}")


class FakeAdapterEnabled(JobSourceAdapter):
    name = "FAKE_PHASE13_ENABLED"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [
            {
                "source": self.name,
                "company": "GenericCo",
                "title": query.role,
                "location": query.location,
                "job_url": f"https://example.com/{query.role.lower().replace(' ', '-')}",
                "application_url": f"https://example.com/apply/{query.role.lower().replace(' ', '-')}",
                "posted_date": "2026-09-19",
                "jd_text": f"{query.role} role.",
                "experience_required": "",
                "mandatory_skills": [],
                "preferred_skills": [],
            }
        ]


source_registry.ADAPTERS["FAKE_PHASE13_ENABLED"] = FakeAdapterEnabled
_original_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_PHASE13_ENABLED"]

try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase13_"))
    tmp_db = tmp_dir / "jobos_test.db"

    import init_dev_db
    init_dev_db.init_dev_db(tmp_db)

    import db as db_mod
    db_mod.DEV_DB = tmp_db

    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db

    client = TestClient(api_main.app)

    def _wait_for_terminal(run_id, candidate_id, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = client.get(f"/api/runs/{run_id}?candidate_id={candidate_id}")
            status = resp.json()["status"]
            if status in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED"):
                return status
            time.sleep(0.2)
        return None

    # -------------------------------------------------- 1/2. Resume-first onboarding

    resume_path = ROOT / "resumes" / "SarojKumarNayak_SRE_DevOps_11Yrs.pdf"

    resp = client.post("/api/candidates", json={"name": "New Candidate"})
    check(resp.status_code == 200, "1. placeholder candidate created for the 'Upload Resume' flow")
    resume_candidate_id = resp.json()["candidate_id"]

    if resume_path.exists():
        with resume_path.open("rb") as f:
            resp = client.post(
                f"/api/candidates/{resume_candidate_id}/resume",
                files={"file": ("resume.pdf", f, "application/pdf")},
            )
        check(resp.status_code == 200, "1. resume upload against the placeholder candidate -> 200")
        body = resp.json()
        check(body["status"] == "SUCCESS", "2. resume extraction succeeds")
        check(
            body["profile"]["identity"]["name"] not in ("", "New Candidate", None),
            f"2. resume extraction overwrites the placeholder name (got {body['profile']['identity']['name']!r})",
        )
        check(body["profile"]["metadata"]["profile_status"] == "DRAFT", "2. a freshly extracted profile is DRAFT, never auto-confirmed")
    else:
        print("SKIP: 1/2. real resume fixture not found")

    # ------------------------------------------------------- 3. Manual profile creation

    resp = client.post("/api/candidates", json={"name": "Manual Candidate"})
    check(resp.status_code == 200, "3. manual candidate creation ('Continue without a resume') -> 200")
    manual_candidate_id = resp.json()["candidate_id"]

    resp = client.put(
        f"/api/candidates/{manual_candidate_id}/profile",
        json={
            "current_title": "Product Manager",
            "total_experience_years": 6,
            "industries": ["SaaS"],
            "skills": {"other": [{"name": "Agile"}, {"name": "Product Strategy"}]},
            "job_preferences": {"target_roles": ["Product Manager"], "target_locations": ["Hyderabad"]},
        },
    )
    check(resp.status_code == 200, "3. manual profile fields save via the SAME PUT endpoint resume-based profiles use (one profile model, not two)")
    manual_profile = resp.json()
    check(manual_profile["professional_summary"]["current_title"] == "Product Manager", "3. manual profile data persisted correctly")

    # ------------------------------------------------------- 5. No "Unknown candidate" for a real id

    resp = client.get(f"/api/candidates/{manual_candidate_id}")
    check(resp.status_code == 200, "5. GET /api/candidates/{id} returns 200 for a candidate that genuinely exists (no false 'Unknown candidate')")

    resp = client.get(f"/api/candidates/{manual_candidate_id}/profile")
    check(resp.status_code == 200, "5. GET .../profile is consistent with GET /api/candidates/{id} for the same real id")

    # Root cause regression guard: a STALE id (the exact bug scenario --
    # a browser remembering an id the server no longer has) must return
    # a clean 404 the frontend's resolveActiveCandidate() can detect and
    # clear, never a 500 / unhandled crash.
    resp = client.get("/api/candidates/cand_stale_deleted_00000000")
    check(resp.status_code == 404, "5. a stale/unknown candidate_id returns a clean 404 (what resolveActiveCandidate() relies on to self-heal)")
    resp = client.get("/api/candidates/cand_stale_deleted_00000000/profile")
    check(resp.status_code == 404, "5. .../profile for a stale id also returns a clean 404, consistently with the plain candidate lookup")

    # ----------------------------------------------------------- 4. Consistency across "pages"

    # Simulates navigating Home -> Profile -> New Search -> Dashboard ->
    # Results with the SAME remembered candidate_id: every page's own
    # API call must resolve to the SAME candidate.
    for _ in range(4):
        resp = client.get(f"/api/candidates/{manual_candidate_id}")
        check(resp.json()["candidate_id"] == manual_candidate_id, "4. repeated lookups of the same candidate_id (simulating cross-page navigation) stay consistent")

    # ------------------------------------------------------------ 6/7. DRAFT <-> CONFIRMED

    resp = client.post(f"/api/candidates/{manual_candidate_id}/profile/confirm")
    check(resp.status_code == 200 and resp.json()["metadata"]["profile_status"] == "CONFIRMED", "7. confirming a valid profile sets it to CONFIRMED")

    resp = client.put(f"/api/candidates/{manual_candidate_id}/profile", json={"headline": "Updated"})
    check(resp.json()["metadata"]["profile_status"] == "DRAFT", "6. editing a CONFIRMED profile resets it to DRAFT (existing semantics preserved)")

    # Re-confirm for the rest of this test.
    client.post(f"/api/candidates/{manual_candidate_id}/profile/confirm")

    # --------------------------------------------------------- 12. Disabled source rejected

    resp = client.post(
        f"/api/candidates/{manual_candidate_id}/searches",
        json={"name": "bad source", "target_roles": ["X"], "target_locations": ["Y"], "sources": ["HIRIST_BUT_NOT_REGISTERED_HERE"]},
    )
    check(resp.status_code == 400, "12. a source not currently enabled (in this test's restricted registry) cannot be selected for execution -> 400")

    # ------------------------------------------------------------- 8. Run requires CONFIRMED

    resp = client.post(
        f"/api/candidates/{manual_candidate_id}/searches",
        json={
            "name": "PM search",
            "target_roles": ["Product Manager"],
            "target_locations": ["Hyderabad"],
            "minimum_match_score": 0,
            "sources": ["FAKE_PHASE13_ENABLED"],
        },
    )
    check(resp.status_code == 200, "setup: saved search created")
    pm_search_id = resp.json()["saved_search_id"]

    resp = client.post(f"/api/searches/{pm_search_id}/run?candidate_id={manual_candidate_id}")
    check(resp.status_code == 200, "setup: first run (profile is CONFIRMED at this point) succeeds")
    run_id = resp.json()["run_id"]
    status = _wait_for_terminal(run_id, manual_candidate_id)
    check(status == "COMPLETED", f"setup: run completes (status={status})")

    # Now de-confirm (edit) and try to run again -- must be refused
    # cleanly (409), never a 500, and never silently allowed.
    client.put(f"/api/candidates/{manual_candidate_id}/profile", json={"headline": "Edited again"})
    resp = client.post(f"/api/searches/{pm_search_id}/run?candidate_id={manual_candidate_id}")
    check(resp.status_code == 409, f"8. running a search with a DRAFT profile is refused cleanly (409), not a 500 (got {resp.status_code})")

    # ------------------------------------------------- 9/10. DRAFT + results / no results

    resp = client.get(f"/api/searches/{pm_search_id}/results?candidate_id={manual_candidate_id}")
    check(resp.status_code == 200, "9. DRAFT profile + already-persisted results -> 200 (Phase 11 fix still holds)")
    results = resp.json()["results"]
    check(len(results) >= 1, "9. persisted results are actually returned")

    # ------------------------------------------------------- 11. Result fields complete

    required_fields = {
        "job_id", "source", "title", "company", "location", "job_url", "application_url",
        "score", "priority", "eligible", "eligibility", "eligibility_reasons",
        "freshness", "freshness_age_days", "experience_required", "status", "report_status",
        "match_reason", "matched_skills", "missing_skills", "resume_variant",
        "first_discovered", "last_seen", "duplicate_suppressed",
    }
    missing = required_fields - set(results[0].keys())
    check(not missing, f"11. every required result field is present (missing: {missing})")
    check(isinstance(results[0]["matched_skills"], list) and isinstance(results[0]["missing_skills"], list), "11. matched_skills/missing_skills are lists, suitable for the result-detail view")

    # A search with genuinely zero persisted results and a DRAFT profile
    # must still return the existing, clean 409 -- never a 500.
    resp = client.post(
        f"/api/candidates/{manual_candidate_id}/searches",
        json={"name": "never run", "target_roles": ["X"], "target_locations": ["Y"], "sources": ["FAKE_PHASE13_ENABLED"]},
    )
    never_run_search_id = resp.json()["saved_search_id"]
    resp = client.get(f"/api/searches/{never_run_search_id}/results?candidate_id={manual_candidate_id}")
    check(resp.status_code == 409, f"10. DRAFT profile + zero persisted results -> 409 (existing not-ready state), not a 500 (got {resp.status_code})")

    # --------------------------------------------------------------- 13. Excel download

    client.post(f"/api/candidates/{manual_candidate_id}/profile/confirm")
    resp = client.get(f"/api/candidates/{manual_candidate_id}/report")
    check(resp.status_code == 200 and resp.headers["content-type"].startswith("application/vnd.openxmlformats"), "13. Excel report download remains functional")

    resp = client.get(f"/api/searches/{pm_search_id}/report?candidate_id={manual_candidate_id}")
    check(resp.status_code == 200, "13. search-scoped Excel report download remains functional")

    # ------------------------------------------------------------- 14. No auto-apply control

    suspicious_tokens = ["submit_application", "apply_to_job", "auto_apply", "autoapply"]
    offending = []
    for path in list((ROOT / "api").glob("*.py")) + list((ROOT / "web").glob("*.html")) + list((ROOT / "web").glob("*.js")):
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for token in suspicious_tokens:
            if token in text:
                offending.append((path.name, token))
    check(not offending, f"14. no automatic-application-submission control anywhere in api/ or web/ (found: {offending})")
    check(
        "auto apply" not in (ROOT / "web" / "results.html").read_text(encoding="utf-8").lower(),
        "14. results.html contains no 'Auto Apply' UI text",
    )

finally:
    source_registry.list_sources = _original_list_sources
    source_registry.ADAPTERS.pop("FAKE_PHASE13_ENABLED", None)

production_after = _sha(PRODUCTION_DB)
check(
    production_before == production_after,
    f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})",
)

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
