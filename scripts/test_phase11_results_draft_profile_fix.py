#!/usr/bin/env python3

"""
Regression tests for the post-Phase-11 bugfix:
GET /api/searches/{id}/results used to raise an unhandled ValueError
(-> HTTP 500) the moment a candidate's profile went back to DRAFT
(e.g. after editing it) while an earlier CONFIRMED-profile run's
results still existed, because api/results_store.py unconditionally
called candidate_profile.to_legacy_matching_profile(), which refuses
any non-CONFIRMED profile by design.

No live network/browser call of any kind -- uses FastAPI's in-process
TestClient against an isolated temp database, with a fake, offline-only
source adapter registered the same safe way test_phase9_api.py/
test_phase10_discovery_engine.py already establish (only
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
    name = "FAKE_PHASE11_BUGFIX_ENABLED"
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


source_registry.ADAPTERS["FAKE_PHASE11_BUGFIX_ENABLED"] = FakeAdapterEnabled
_original_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_PHASE11_BUGFIX_ENABLED"]

try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase11_bugfix_"))
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

    def _make_confirmed_candidate(name, title, roles, locations):
        resp = client.post("/api/candidates", json={"name": name})
        candidate_id = resp.json()["candidate_id"]
        client.put(
            f"/api/candidates/{candidate_id}/profile",
            json={
                "current_title": title,
                "total_experience_years": 5,
                "skills": {"other": [{"name": "Generic Skill"}]},
                "job_preferences": {"target_roles": roles, "target_locations": locations},
            },
        )
        resp = client.post(f"/api/candidates/{candidate_id}/profile/confirm")
        assert resp.json()["metadata"]["profile_status"] == "CONFIRMED"
        return candidate_id

    # ---------------------------------------------------------------- A/B setup

    candidate_id = _make_confirmed_candidate("Draft Bug Candidate", "Generic Role", ["Generic Role"], ["Generic City"])

    resp = client.post(
        f"/api/candidates/{candidate_id}/searches",
        json={
            "name": "A/B search",
            "target_roles": ["Generic Role"],
            "target_locations": ["Generic City"],
            "minimum_match_score": 0,
            "sources": ["FAKE_PHASE11_BUGFIX_ENABLED"],
        },
    )
    search_id = resp.json()["saved_search_id"]

    resp = client.post(f"/api/searches/{search_id}/run?candidate_id={candidate_id}")
    run_id = resp.json()["run_id"]
    status = _wait_for_terminal(run_id, candidate_id)
    check(status == "COMPLETED", f"setup: run completes while profile is CONFIRMED (status={status})")

    # -------------------------------------------------------- A. CONFIRMED + results

    resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
    check(resp.status_code == 200, "A. CONFIRMED profile + persisted results -> 200")
    results_confirmed = resp.json()["results"]
    check(len(results_confirmed) >= 1, "A. CONFIRMED profile + persisted results -> non-empty results, existing behavior")
    check(results_confirmed[0]["title"] == "Generic Role", "A. results reflect the live rescore (existing, unchanged path)")

    # ------------------------------------------------- E. edit does not promote

    resp = client.put(f"/api/candidates/{candidate_id}/profile", json={"headline": "Updated headline"})
    check(resp.status_code == 200, "E. PUT profile succeeds")
    check(resp.json()["metadata"]["profile_status"] == "DRAFT", "E. editing a CONFIRMED profile resets it to DRAFT, never silently stays/promotes to CONFIRMED")

    # Confirm on disk too, independent of the PUT response.
    resp = client.get(f"/api/candidates/{candidate_id}/profile")
    check(resp.json()["metadata"]["profile_status"] == "DRAFT", "E. profile is genuinely persisted as DRAFT, not just in the PUT response")

    # --------------------------------------------- B. DRAFT + existing results

    import results_store as results_store_mod

    def _poisoned_to_legacy(*args, **kwargs):
        raise AssertionError("to_legacy_matching_profile() must NOT be called for a DRAFT profile with persisted results")

    original_to_legacy = results_store_mod.to_legacy_matching_profile
    results_store_mod.to_legacy_matching_profile = _poisoned_to_legacy
    try:
        resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
        check(resp.status_code == 200, "B. DRAFT profile + persisted results -> 200, not 500 (this was the reported bug)")
        results_draft = resp.json()["results"]
        check(len(results_draft) >= 1, "B. DRAFT profile + persisted results -> non-empty results, served from persisted data")
        check(results_draft[0]["title"] == "Generic Role", "B. persisted results carry the correct job title")
        check(results_draft[0]["company"] == "GenericCo", "B. persisted results carry the correct company")
        check(results_draft[0]["score"] is not None, "B. persisted results carry a real, already-computed score (not fabricated/null)")
        check(results_draft[0]["eligible"] is True, "B. a persisted match row is eligible by construction")
    finally:
        results_store_mod.to_legacy_matching_profile = original_to_legacy

    check(True, "B. to_legacy_matching_profile() was never invoked while serving DRAFT + persisted results (no AssertionError raised)")

    # ------------------------------------------- C. DRAFT + no persisted results

    resp = client.post("/api/candidates", json={"name": "Never Confirmed Candidate"})
    draft_only_candidate_id = resp.json()["candidate_id"]
    client.put(
        f"/api/candidates/{draft_only_candidate_id}/profile",
        json={"job_preferences": {"target_roles": ["Some Role"], "target_locations": ["Some City"]}},
    )
    # Deliberately never confirmed, never run -- create the saved search
    # directly against the store to bypass the run-requires-CONFIRMED
    # gate (search_submission.submit_search() already refuses a DRAFT
    # profile at the API/run layer -- this test targets the RESULTS
    # endpoint specifically, for a search that was created but never
    # successfully run).
    import sqlite3
    import search_store as search_store_mod
    from schemas import SavedSearchCreate

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    orphan_search_id = search_store_mod.create_saved_search(
        conn,
        draft_only_candidate_id,
        SavedSearchCreate(
            name="Never run",
            target_roles=["Some Role"],
            target_locations=["Some City"],
            sources=["FAKE_PHASE11_BUGFIX_ENABLED"],
        ),
    )
    conn.close()

    resp = client.get(f"/api/searches/{orphan_search_id}/results?candidate_id={draft_only_candidate_id}")
    check(resp.status_code == 409, f"C. DRAFT profile + no persisted results -> 409 (clear state), not 500 (got {resp.status_code})")
    check("CONFIRMED" in resp.json()["detail"], "C. the 409 response explains the profile must be CONFIRMED")

    # --------------------------------------- D. real matching pipeline still refuses DRAFT

    from candidate_profile import to_legacy_matching_profile as real_to_legacy_matching_profile
    import profile_store as profile_store_mod

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    draft_profile = profile_store_mod.load_active_profile(conn, draft_only_candidate_id)
    conn.close()

    check(draft_profile.metadata.profile_status.value == "DRAFT", "D. sanity: this candidate's profile is genuinely DRAFT")

    raised = False
    try:
        real_to_legacy_matching_profile(draft_profile)
    except ValueError:
        raised = True
    check(raised, "D. to_legacy_matching_profile() still raises ValueError for a DRAFT profile -- the protected function itself is unmodified")

finally:
    source_registry.list_sources = _original_list_sources
    source_registry.ADAPTERS.pop("FAKE_PHASE11_BUGFIX_ENABLED", None)

production_after = _sha(PRODUCTION_DB)
check(
    production_before == production_after,
    f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})",
)

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
