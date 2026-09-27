#!/usr/bin/env python3
"""
Phase 2 regression tests: company research (scripts/company_research.py,
migrate_v12_company_research.py, api/main.py's company-research routes).

Covers (Phase 13 checklist):
  E. persistence/versioning (each research call creates a NEW version,
     never overwrites an earlier one)
  F. source URLs retained (every populated field traces back to a real
     provider-returned URL)
  G. NOT_ATTEMPTED / FAILED / PARTIAL / SUCCESS are all representable,
     driven only by real provider outcomes -- never guessed
  T. candidate isolation

Uses a fake SearchProviderManager (never a live network call) injected
via company_research.get_manager, matching this project's established
fake-adapter testing convention. Isolated temp DB; production DB
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


import company_research
from search_provider_manager import SearchProviderPoolExhausted


class _FakeAttemptResult:
    def __init__(self, results, provider_name="FAKE_PROVIDER"):
        self.results = results
        self.provider_name = provider_name
        self.attempts = [(provider_name, f"success ({len(results)} results)")]


class _FakeManager:
    """Swappable per-test-case behavior, installed via company_research.get_manager."""

    def __init__(self, eligible, search_fn=None):
        self._eligible = eligible
        self._search_fn = search_fn

    def eligible_providers(self):
        return self._eligible

    def search(self, query, num=8):
        return self._search_fn(query, num=num)


tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_company_research_"))
tmp_db = tmp_dir / "jobos.db"
import init_dev_db
init_dev_db.init_dev_db(tmp_db)
import db as db_mod
db_mod.DEV_DB = tmp_db
from fastapi.testclient import TestClient
import main as api_main
api_main.db_mod.DEV_DB = tmp_db

client = TestClient(api_main.app)

resp = client.post("/api/candidates", json={"name": "Company Research Test A"})
candidate_a = resp.json()["candidate_id"]
resp = client.post("/api/candidates", json={"name": "Company Research Test B"})
candidate_b = resp.json()["candidate_id"]
client.put(f"/api/candidates/{candidate_a}/profile", json={"current_title": "SRE", "total_experience_years": 9})

# --- G: NOT_ATTEMPTED (no provider configured at all) ---
company_research.get_manager = lambda: _FakeManager(eligible=[])
resp = client.post(f"/api/candidates/{candidate_a}/company-research", json={"company_name": "Acme Corp"})
check(resp.status_code == 200, f"G: NOT_ATTEMPTED research call still succeeds (never blocks), got {resp.status_code}")
r_not_attempted = resp.json()
check(r_not_attempted["status"] == "NOT_ATTEMPTED", f"G: status is NOT_ATTEMPTED when no provider configured, got {r_not_attempted['status']}")
check(r_not_attempted["website"] is None and r_not_attempted["description"] is None, "G: NOT_ATTEMPTED leaves website/description null, never fabricated")
check(r_not_attempted["version"] == 1, f"E: first research call is version 1, got {r_not_attempted['version']}")

# --- G: FAILED (every provider fails) ---
def _raise_exhausted(query, num=8):
    raise SearchProviderPoolExhausted([("FAKE_PROVIDER", "TIMEOUT: connection timed out")])


company_research.get_manager = lambda: _FakeManager(eligible=["FAKE_PROVIDER"], search_fn=_raise_exhausted)
resp = client.post(f"/api/candidates/{candidate_a}/company-research", json={"company_name": "Acme Corp"})
r_failed = resp.json()
check(r_failed["status"] == "FAILED", f"G: status is FAILED when every provider fails, got {r_failed['status']}")
check(r_failed["error_message"] is not None and "TIMEOUT" in r_failed["error_message"], "G: FAILED carries a real, honest error_message")
check(r_failed["version"] == 2, f"E: second research call for the same company is version 2, got {r_failed['version']}")

# --- G: PARTIAL (search succeeds, but no confident official-site match) ---
def _search_no_match(query, num=8):
    return _FakeAttemptResult([
        {"url": "https://www.linkedin.com/company/acme-corp", "snippet": "Acme Corp on LinkedIn", "title": "Acme Corp | LinkedIn"},
        {"url": "https://www.glassdoor.com/Reviews/acme-corp", "snippet": "Acme Corp reviews", "title": "Acme Corp Reviews"},
    ])


company_research.get_manager = lambda: _FakeManager(eligible=["FAKE_PROVIDER"], search_fn=_search_no_match)
resp = client.post(f"/api/candidates/{candidate_a}/company-research", json={"company_name": "Acme Corp"})
r_partial = resp.json()
check(r_partial["status"] == "PARTIAL", f"G: status is PARTIAL when results exist but no official site is confidently identified, got {r_partial['status']}")
check(r_partial["website"] is None, "G: PARTIAL never guesses a website from an aggregator/social result")

# --- G / F / E: SUCCESS with real source URLs ---
def _search_with_match(query, num=8):
    return _FakeAttemptResult([
        {"url": "https://www.acmecorp.com/about", "snippet": "Acme Corp builds real things.", "title": "About Acme Corp", "date": "2026-01-01"},
        {"url": "https://www.linkedin.com/company/acme-corp", "snippet": "Acme Corp on LinkedIn", "title": "Acme Corp | LinkedIn"},
    ])


company_research.get_manager = lambda: _FakeManager(eligible=["FAKE_PROVIDER"], search_fn=_search_with_match)
resp = client.post(f"/api/candidates/{candidate_a}/company-research", json={"company_name": "Acme Corp", "job_id": None})
r_success = resp.json()
check(r_success["status"] == "SUCCESS", f"G: status is SUCCESS when a confident official site is found, got {r_success['status']}")
check(r_success["website"] == "https://www.acmecorp.com/about", f"F: website field is the real provider-returned URL, got {r_success['website']}")
check(r_success["description"] == "Acme Corp builds real things.", "F: description is a verbatim provider snippet, never invented")
check(r_success["version"] == 4, f"E: SUCCESS call is version 4 (4th call for this company), got {r_success['version']}")
check(set(r_success["source_urls"]) == {"https://www.acmecorp.com/about", "https://www.linkedin.com/company/acme-corp"}, f"F: every result URL retained in source_urls, got {r_success['source_urls']}")

# --- E: versioning never overwrites earlier records ---
resp = client.get(f"/api/candidates/{candidate_a}/company-research", params={"company_name": "Acme Corp"})
all_versions = resp.json()["company_research"]
check(len(all_versions) == 4, f"E: all 4 historical versions remain independently queryable, got {len(all_versions)}")
check({v["status"] for v in all_versions} == {"NOT_ATTEMPTED", "FAILED", "PARTIAL", "SUCCESS"}, f"E: each version preserved its own distinct outcome, got {[v['status'] for v in all_versions]}")
resp = client.get(f"/api/candidates/{candidate_a}/company-research/{r_not_attempted['company_research_id']}")
check(resp.status_code == 200 and resp.json()["status"] == "NOT_ATTEMPTED", "E: the original v1 record is still independently readable after 3 more versions were created")

# --- job_id validation: an unknown job_id is refused, never silently stored ---
resp = client.post(f"/api/candidates/{candidate_a}/company-research", json={"company_name": "Acme Corp", "job_id": "job-does-not-exist"})
check(resp.status_code == 404, f"job scope: an unknown job_id is refused rather than silently persisted, got {resp.status_code} {resp.text[:150]}")

# --- T: candidate isolation ---
resp = client.get(f"/api/candidates/{candidate_b}/company-research/{r_success['company_research_id']}")
check(resp.status_code == 404, f"T: candidate B cannot read candidate A's company research by ID, got {resp.status_code}")
resp = client.get(f"/api/candidates/{candidate_b}/company-research")
check(resp.json()["company_research"] == [], f"T: candidate B's own research list is empty, got {resp.json()['company_research']}")

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
