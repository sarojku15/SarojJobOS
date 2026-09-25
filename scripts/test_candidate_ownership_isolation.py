#!/usr/bin/env python3

"""
Phase 11/18 regression test: candidate A must never be able to read
(or run, archive, or download the report for) candidate B's saved
search, run, results, or report, merely by knowing or guessing
B's search_id/run_id.

This is the exact gap this session's audit found: routes keyed by a
bare search_id/run_id (GET/PUT/PATCH/DELETE /api/searches/{id},
POST .../run, GET /api/runs/{id}, GET .../results, GET .../report)
never verified the caller-supplied candidate_id against the record's
real owner -- they now require an explicit ?candidate_id= and 404 on
any mismatch (search_store.get_saved_search_for_candidate() /
get_run_status_for_candidate()), deliberately indistinguishable from
"this search_id doesn't exist at all" so a caller can never use the
response to probe which ids exist for another candidate.

Exercises api/main.py's FastAPI app in-process via TestClient. Fully
offline, isolated temp DB (never jobos.db/jobos_dev.db). No network.
"""

import hashlib
import json
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


class FakeAdapterEnabled(JobSourceAdapter):
    name = "FAKE_ISOLATION_ENABLED"
    status = AdapterStatus.ENABLED

    def health_check(self):
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query):
        return [
            {
                "source": self.name,
                "company": "IsolationCo",
                "title": query.role,
                "location": query.location,
                "work_model": "Hybrid",
                "job_url": "https://example.com/jobs/isolation-1",
                "application_url": "https://example.com/apply/isolation-1",
                "posted_date": "2026-09-19",
                "jd_text": f"{query.role} role.",
                "experience_required": "5+ years",
                "mandatory_skills": [],
                "preferred_skills": [],
            }
        ]


source_registry.ADAPTERS["FAKE_ISOLATION_ENABLED"] = FakeAdapterEnabled
_original_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_ISOLATION_ENABLED"]

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


try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_isolation_"))
    tmp_db = tmp_dir / "jobos_test.db"

    import init_dev_db
    init_dev_db.init_dev_db(tmp_db)

    import db as db_mod
    db_mod.DEV_DB = tmp_db

    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db

    client = TestClient(api_main.app)

    def _make_confirmed_candidate(name, role, location):
        resp = client.post("/api/candidates", json={"name": name})
        candidate_id = resp.json()["candidate_id"]
        client.put(
            f"/api/candidates/{candidate_id}/profile",
            json={
                "current_title": role,
                "total_experience_years": 5,
                "skills": {"other": [{"name": "Generic Skill"}]},
                "job_preferences": {"target_roles": [role], "target_locations": [location]},
            },
        )
        resp = client.post(f"/api/candidates/{candidate_id}/profile/confirm")
        assert resp.json()["metadata"]["profile_status"] == "CONFIRMED"
        return candidate_id

    candidate_a = _make_confirmed_candidate("Isolation Candidate A", "Role A", "City A")
    candidate_b = _make_confirmed_candidate("Isolation Candidate B", "Role B", "City B")

    resp = client.post(
        f"/api/candidates/{candidate_a}/searches",
        json={
            "name": "A's search",
            "target_roles": ["Role A"],
            "target_locations": ["City A"],
            "minimum_match_score": 0,
            "sources": ["FAKE_ISOLATION_ENABLED"],
        },
    )
    search_a = resp.json()["saved_search_id"]

    resp = client.post(f"/api/searches/{search_a}/run?candidate_id={candidate_a}")
    check(resp.status_code == 200, "setup: A can run A's own search")
    run_a = resp.json()["run_id"]

    deadline = time.time() + 20
    status = None
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_a}?candidate_id={candidate_a}")
        status = r.json()["status"]
        if status in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED"):
            break
        time.sleep(0.2)
    check(status == "COMPLETED", f"setup: A's run completes (status={status})")

    # ---------------------------------------------- B cannot read A's data

    resp = client.get(f"/api/searches/{search_a}?candidate_id={candidate_b}")
    check(resp.status_code == 404, "1. B cannot GET A's saved search by id (got {})".format(resp.status_code))

    resp = client.put(f"/api/searches/{search_a}?candidate_id={candidate_b}", json={"minimum_match_score": 99})
    check(resp.status_code == 404, "1b. B cannot PUT/update A's saved search")

    resp = client.post(f"/api/searches/{search_a}/run?candidate_id={candidate_b}")
    check(resp.status_code == 404, "2. B cannot trigger a run on A's saved search")

    resp = client.get(f"/api/runs/{run_a}?candidate_id={candidate_b}")
    check(resp.status_code == 404, "3. B cannot GET A's run status/source-audit by id")

    resp = client.get(f"/api/searches/{search_a}/results?candidate_id={candidate_b}")
    check(resp.status_code == 404, "4. B cannot GET A's search results")

    resp = client.get(f"/api/searches/{search_a}/report?candidate_id={candidate_b}")
    check(resp.status_code == 404, "5. B cannot download A's search-scoped Excel report")

    resp = client.delete(f"/api/searches/{search_a}?candidate_id={candidate_b}")
    check(resp.status_code == 404, "6. B cannot archive/delete A's saved search")

    # ---------------------- the SAME lookups succeed for the real owner

    resp = client.get(f"/api/searches/{search_a}?candidate_id={candidate_a}")
    check(resp.status_code == 200, "7. A can still GET A's own saved search")

    resp = client.get(f"/api/runs/{run_a}?candidate_id={candidate_a}")
    check(resp.status_code == 200, "7. A can still GET A's own run status")

    resp = client.get(f"/api/searches/{search_a}/results?candidate_id={candidate_a}")
    check(resp.status_code == 200, "7. A can still GET A's own results")

    # ------------------------------------- missing candidate_id entirely

    resp = client.get(f"/api/searches/{search_a}")
    check(resp.status_code == 422, "8. omitting candidate_id entirely is rejected (422), never silently allowed or defaulted")

    resp = client.get(f"/api/runs/{run_a}")
    check(resp.status_code == 422, "8. omitting candidate_id entirely is rejected (422) for runs too")

    # -------------------------------------------- unknown ids stay 404

    resp = client.get(f"/api/searches/does-not-exist?candidate_id={candidate_a}")
    check(resp.status_code == 404, "9. a genuinely unknown search_id is still a clean 404")

    resp = client.get(f"/api/searches/{search_a}?candidate_id=does-not-exist")
    check(resp.status_code == 404, "9. a genuinely unknown candidate_id is still a clean 404 (same response shape as a mismatch)")

finally:
    source_registry.list_sources = _original_list_sources

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
