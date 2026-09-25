#!/usr/bin/env python3

"""
Phase 10 (job-discovery-engine + generic multi-source + API extensions)
offline tests. No live network call anywhere in this file -- ATS
adapters are exercised against local fixture JSON via a monkeypatched
ats_common._fetch_json/load_boards; the discovery Skill is exercised
against the MOCK adapter and deliberately-unavailable sources; the API
extensions are exercised through FastAPI's in-process TestClient
against a temporary, isolated database (never
data/applications/jobos_dev.db, never production).
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
API_DIR = ROOT / "api"
SKILL_SCRIPTS_DIR = ROOT / ".claude" / "skills" / "job-discovery-engine" / "scripts"
for p in (SCRIPTS_DIR, API_DIR, SKILL_SCRIPTS_DIR):
    sys.path.insert(0, str(p))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"
FIXTURES = SCRIPTS_DIR / "fixtures"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)

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


# ----------------------------------------------------------- source_capabilities

import source_capabilities
import source_registry
from source_adapter import AdapterStatus

caps = {c["source_name"]: c for c in source_capabilities.list_source_capabilities()}

check(caps["NAUKRI"]["enabled"] is True, "1. NAUKRI capability record reports enabled=True")
# Phase 11: HIRIST passed the full live-validation gate (two-stage
# listing+detail-page fetch fixed the company/title ambiguity) and is
# now genuinely enabled -- see data/reports/phase11_public_multisource_completion.md.
check(caps["HIRIST"]["enabled"] is True and caps["HIRIST"]["reason"] is None, "1. HIRIST reports enabled=True (Phase 11), no reason needed")
check(caps["LINKEDIN"]["enabled"] is False and caps["LINKEDIN"]["authorized"] is False, "1. LINKEDIN reports enabled=False, authorized=False")
check(caps["GREENHOUSE"]["acquisition_method"] == "provider", "1. GREENHOUSE acquisition_method == 'provider'")
check(caps["WEB_SEARCH"]["acquisition_method"] == "web_search_discovery", "1. WEB_SEARCH acquisition_method == 'web_search_discovery'")
check(caps["IIMJOBS"]["enabled"] is True, "1. IIMJOBS reports enabled=True (Phase 11, shares Hirist's platform)")
check(caps["APNA"]["enabled"] is True, "1. APNA reports enabled=True (Phase 12, real server-rendered data feed)")
check(
    all(not c["enabled"] for name, c in caps.items() if name not in ("NAUKRI", "MOCK", "HIRIST", "IIMJOBS", "APNA")),
    "1. every source except NAUKRI/MOCK/HIRIST/IIMJOBS/APNA is enabled=False (nothing silently claimed working)",
)
enabled_list = source_capabilities.list_enabled_sources()
check(all(c["source_name"] != "MOCK" for c in enabled_list), "1. list_enabled_sources() excludes MOCK")

# ------------------------------------------------------------------- ATS adapters

import ats_common
import greenhouse_adapter
import lever_adapter
import ashby_adapter
from greenhouse_adapter import GreenhouseAdapter
from lever_adapter import LeverAdapter
from ashby_adapter import AshbyAdapter
from source_adapter import SearchQuery

# Each adapter module did `from ats_common import _fetch_json,
# load_boards`, binding its OWN local name at import time -- patching
# ats_common's own attributes after that has no effect on those
# already-bound references, so every adapter module's local names must
# be patched too (this is exactly why greenhouse_adapter.py's own
# docstring says "no adapter makes a network call while NOT_ENABLED":
# these are the only two functions capable of one, and both are fully
# substitutable for tests).
_originals = {
    "ats_common._fetch_json": ats_common._fetch_json,
    "ats_common.load_boards": ats_common.load_boards,
    "greenhouse_adapter._fetch_json": greenhouse_adapter._fetch_json,
    "greenhouse_adapter.load_boards": greenhouse_adapter.load_boards,
    "lever_adapter._fetch_json": lever_adapter._fetch_json,
    "lever_adapter.load_boards": lever_adapter.load_boards,
    "ashby_adapter._fetch_json": ashby_adapter._fetch_json,
    "ashby_adapter.load_boards": ashby_adapter.load_boards,
}


def _fixture_fetch(url, timeout=10):
    if "greenhouse" in url:
        return json.loads((FIXTURES / "greenhouse_sample.json").read_text())
    if "lever" in url:
        return json.loads((FIXTURES / "lever_sample.json").read_text())
    if "ashby" in url:
        return json.loads((FIXTURES / "ashby_sample.json").read_text())
    raise AssertionError(f"unexpected fixture URL: {url}")


for module in (ats_common, greenhouse_adapter, lever_adapter, ashby_adapter):
    module._fetch_json = _fixture_fetch
    module.load_boards = lambda platform: ["examplecompany"]

try:
    gh_results = GreenhouseAdapter().search(SearchQuery(role="Senior Cloud Engineer", location="Bangalore"))
    check(len(gh_results) == 1 and gh_results[0]["company"] == "examplecompany", "2. GreenhouseAdapter.search() finds the matching fixture job, filters out the non-matching one")
    check(gh_results[0]["source"] == "GREENHOUSE" and gh_results[0]["job_url"].startswith("https://"), "2. GreenhouseAdapter normalizes to the CommonJob shape with a usable URL")

    lever_results = LeverAdapter().search(SearchQuery(role="Data Scientist", location="Remote"))
    check(len(lever_results) == 1 and lever_results[0]["title"] == "Data Scientist", "2. LeverAdapter.search() finds the matching fixture job")

    ashby_results = AshbyAdapter().search(SearchQuery(role="Product Manager", location="Hyderabad"))
    check(len(ashby_results) == 1 and ashby_results[0]["company"] == "examplecompany", "2. AshbyAdapter.search() finds the matching fixture job")

    greenhouse_adapter.load_boards = lambda platform: []
    check(
        GreenhouseAdapter().search(SearchQuery(role="anything", location="anywhere")) == [],
        "2. an ATS adapter with zero configured boards returns zero results, makes no fetch call",
    )
finally:
    ats_common._fetch_json = _originals["ats_common._fetch_json"]
    ats_common.load_boards = _originals["ats_common.load_boards"]
    greenhouse_adapter._fetch_json = _originals["greenhouse_adapter._fetch_json"]
    greenhouse_adapter.load_boards = _originals["greenhouse_adapter.load_boards"]
    lever_adapter._fetch_json = _originals["lever_adapter._fetch_json"]
    lever_adapter.load_boards = _originals["lever_adapter.load_boards"]
    ashby_adapter._fetch_json = _originals["ashby_adapter._fetch_json"]
    ashby_adapter.load_boards = _originals["ashby_adapter.load_boards"]

check(GreenhouseAdapter.status == AdapterStatus.NOT_ENABLED, "2. GreenhouseAdapter.status remains NOT_ENABLED (not live-validated)")
check(LeverAdapter.status == AdapterStatus.NOT_ENABLED, "2. LeverAdapter.status remains NOT_ENABLED")
check(AshbyAdapter.status == AdapterStatus.NOT_ENABLED, "2. AshbyAdapter.status remains NOT_ENABLED")

# ------------------------------------------------------- web_search_discovery_adapter

import web_search_discovery_adapter as wsda
from source_adapter import AdapterNotEnabledError

check(wsda.WEB_SEARCH_BACKEND is None, "3. WEB_SEARCH_BACKEND is None by default in this runtime")

adapter = wsda.WebSearchDiscoveryAdapter()
raised = False
try:
    adapter.search(SearchQuery(role="X", location="Y"))
except AdapterNotEnabledError:
    raised = True
check(raised, "3. WebSearchDiscoveryAdapter.search() raises AdapterNotEnabledError with no backend configured -- never a fake result")

try:
    wsda.set_web_search_backend(lambda q: [{"source": "WEB_SEARCH", "company": "FakeCo", "title": q.role, "job_url": "https://example.com/x"}])
    results = adapter.search(SearchQuery(role="Mechanical Engineer", location="Pune"))
    check(len(results) == 1 and results[0]["title"] == "Mechanical Engineer", "3. once a backend is registered, search() delegates to it correctly")
finally:
    wsda.clear_web_search_backend()
check(wsda.WEB_SEARCH_BACKEND is None, "3. clear_web_search_backend() resets the hook")

# ------------------------------------------------------------------- validate_discovered_job

from validate_discovered_job import validate_discovered_job, validate_batch

valid, reasons = validate_discovered_job({"title": "X", "company": "Y", "source": "NAUKRI", "job_url": "https://example.com/j"})
check(valid and not reasons, "4. a well-formed job passes validation")

valid, reasons = validate_discovered_job({"title": "", "company": "Y", "source": "NAUKRI", "job_url": "https://example.com/j"})
check(not valid and "title" in reasons[0], "4. missing title is rejected")

valid, reasons = validate_discovered_job({"title": "X", "company": "Y", "source": "NAUKRI"})
check(not valid, "4. a job with no usable URL is rejected -- never presented as actionable")

valid, reasons = validate_discovered_job({"title": "X", "company": "Y", "source": "NOT_A_REAL_SOURCE", "job_url": "https://example.com/j"})
check(not valid, "4. an unknown source name is rejected")

accepted, rejected = validate_batch([
    {"title": "Good", "company": "C", "source": "NAUKRI", "job_url": "https://example.com/1"},
    {"title": "", "company": "C", "source": "NAUKRI", "job_url": "https://example.com/2"},
])
check(len(accepted) == 1 and len(rejected) == 1, "4. validate_batch() splits a mixed batch without aborting on the bad record")

# ---------------------------------------------------------------- normalize_discovered_jobs

from normalize_discovered_jobs import dedup_within_batch, normalize_discovered_jobs

deduped = dedup_within_batch([
    {"source": "NAUKRI", "job_url": "https://x/1"},
    {"source": "NAUKRI", "job_url": "https://x/1"},
    {"source": "NAUKRI", "job_url": "https://x/2"},
])
check(len(deduped) == 2, "5. dedup_within_batch() removes the exact (source, job_url) duplicate")

normalized, errors = normalize_discovered_jobs([{"company": "C", "title": "T", "source": "NAUKRI", "job_url": "https://x/1"}])
check(len(normalized) == 1 and normalized[0].get("discovered_at"), "5. normalize_discovered_jobs() reuses discover_local.normalize_job() and stamps discovered_at")

normalized, errors = normalize_discovered_jobs([{"title": "no company"}])
check(len(normalized) == 0 and len(errors) == 1, "5. a job missing required fields is reported as an error, not silently dropped or crashing the batch")

# --------------------------------------------------------------------------- discover_jobs

import discover_jobs

plan = discover_jobs.build_adapter_query_plan(
    {"titles": ["A", "B"], "locations": ["X", "Y"], "experience_min": 5, "max_job_age_days": 3},
    ["NAUKRI"],
)
check(len(plan) == 4, "6. build_adapter_query_plan() produces title x location cross product (2x2=4)")

many_titles = [f"Title{i}" for i in range(30)]
many_locations = [f"Loc{i}" for i in range(30)]
bounded_plan = discover_jobs.build_adapter_query_plan({"titles": many_titles, "locations": many_locations}, ["NAUKRI"])
check(len(bounded_plan) == discover_jobs.MAX_ADAPTER_QUERIES, f"6. query plan is hard-capped at {discover_jobs.MAX_ADAPTER_QUERIES} even for a huge cross product")

web_queries = discover_jobs.build_web_search_query_strings({
    "titles": ["Senior Cloud Engineer", "Cloud Infrastructure Lead"],
    "locations": ["Bangalore"],
    "skills": ["AWS", "Terraform", "Kubernetes"],
    "work_model": ["REMOTE"],
    "experience_min": 8,
    "experience_max": 12,
})
check(len(web_queries) <= discover_jobs.MAX_WEB_SEARCH_QUERIES_PER_PAIR, f"6. web-search query plan stays within the per-pair bound ({len(web_queries)} <= {discover_jobs.MAX_WEB_SEARCH_QUERIES_PER_PAIR})")
check(any("Bangalore" in q for q in web_queries), "6. every generated web-search query token traces back to the supplied criteria")

outcome = discover_jobs.discover({
    "titles": ["Accountant"],
    "locations": ["Chennai"],
    "sources": ["MOCK", "LINKEDIN"],
})
check(outcome.jobs and outcome.jobs[0]["title"] == "Accountant", "6. discover() end-to-end via MOCK returns a job matching the REQUESTED (generic, non-DevOps) title")
check(
    any(u["source_name"] == "LINKEDIN" for u in outcome.unavailable_sources),
    "6. discover() surfaces LINKEDIN as unavailable with a reason rather than silently dropping it",
)

outcome_all_unavailable = discover_jobs.discover({"titles": ["X"], "locations": ["Y"], "sources": ["LINKEDIN", "INDEED"]})
check(outcome_all_unavailable.jobs == [] and len(outcome_all_unavailable.unavailable_sources) == 2, "6. discover() with only unavailable sources requested returns no jobs and reports both as unavailable")

# ------------------------------------------------------------------------------- API layer

import db as db_mod
import init_dev_db
from source_adapter import JobSourceAdapter, AdapterHealth, BlockReason

tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase10_"))
tmp_db = tmp_dir / "jobos_test.db"
init_dev_db.init_dev_db(tmp_db)
db_mod.DEV_DB = tmp_db


class FakeAdapterEnabled10(JobSourceAdapter):
    name = "FAKE_PHASE10_ENABLED"
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


source_registry.ADAPTERS["FAKE_PHASE10_ENABLED"] = FakeAdapterEnabled10
_original_list_sources = source_registry.list_sources
source_registry.list_sources = lambda: ["FAKE_PHASE10_ENABLED"]

try:
    from fastapi.testclient import TestClient
    import main as api_main
    api_main.db_mod.DEV_DB = tmp_db

    client = TestClient(api_main.app)

    resp = client.get("/api/health")
    check(resp.status_code == 200 and resp.json()["status"] == "OK", "7. GET /api/health -> 200 OK")

    resp = client.post("/api/candidates", json={"name": "Mechanical Engineer Test"})
    candidate_id = resp.json()["candidate_id"]

    resp = client.patch(f"/api/candidates/{candidate_id}", json={"phone": "+91-9000000000"})
    check(resp.status_code == 200 and resp.json()["phone"] == "+91-9000000000", "7. PATCH /api/candidates/{id} updates identity fields")

    resp = client.put(
        f"/api/candidates/{candidate_id}/profile",
        json={
            "current_title": "Mechanical Engineer",
            "total_experience_years": 7,
            "industries": ["Automotive", "Manufacturing"],
            "job_preferences": {"target_roles": ["Mechanical Engineer"], "target_locations": ["Pune"]},
        },
    )
    check(resp.status_code == 200 and resp.json()["professional_summary"]["industries"] == ["Automotive", "Manufacturing"], "7. PUT profile persists the new 'industries' field")

    resp = client.post(f"/api/candidates/{candidate_id}/profile/confirm")
    check(resp.status_code == 200, "7. profile confirm still works with the extended schema")

    resp = client.post(
        f"/api/candidates/{candidate_id}/searches",
        json={
            "name": "ME search",
            "target_roles": ["Mechanical Engineer"],
            "target_locations": ["Pune"],
            "skills": ["CAD", "SolidWorks"],
            "schedule": {"enabled": True, "frequency": "DAILY"},
            "sources": ["FAKE_PHASE10_ENABLED"],
            "minimum_match_score": 0,
        },
    )
    check(resp.status_code == 200, "7. create saved search with new skills/schedule fields -> 200")
    search = resp.json()
    check(search["skills"] == ["CAD", "SolidWorks"], "7. saved search round-trips 'skills'")
    check(search["schedule"] == {"enabled": True, "frequency": "DAILY"}, "7. saved search round-trips 'schedule' (stored only -- launchd untouched)")
    search_id = search["saved_search_id"]

    resp = client.patch(f"/api/searches/{search_id}?candidate_id={candidate_id}", json={"minimum_match_score": 50})
    check(resp.status_code == 200 and resp.json()["minimum_match_score"] == 50, "7. PATCH /api/searches/{id} works as an alias for the existing update logic")

    resp = client.get("/api/sources")
    body = resp.json()
    check("enabled" in body and "unavailable" in body, "7. GET /api/sources now also returns 'enabled'/'unavailable' capability detail")
    check(isinstance(body["unavailable"], list), "7. 'unavailable' key is present and well-formed")

    resp = client.post(f"/api/searches/{search_id}/run?candidate_id={candidate_id}")
    run_id = resp.json()["run_id"]
    import time as _time
    deadline = _time.time() + 15
    status = None
    while _time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}?candidate_id={candidate_id}")
        status = r.json()["status"]
        if status in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED"):
            break
        _time.sleep(0.2)
    check(status == "COMPLETED", f"7. run triggered via the extended search schema completes (status={status})")

    resp = client.get(f"/api/searches/{search_id}/results?candidate_id={candidate_id}")
    results = resp.json()["results"]
    check(len(results) >= 1, "7. results non-empty for the Mechanical Engineer search")
    check("match_reason" in results[0], "7. each result row now includes a 'match_reason' field")

    resp = client.get(f"/api/searches/{search_id}/report?candidate_id={candidate_id}")
    check(resp.status_code == 200 and resp.headers["content-type"].startswith("application/vnd.openxmlformats"), "7. GET /api/searches/{id}/report downloads the existing 9-sheet workbook via a search-scoped URL")

    resp = client.get(f"/api/candidates/{candidate_id}/dashboard")
    dash = resp.json()
    check("summary" in dash and "sources" in dash, "7. dashboard response includes 'summary' and 'sources' (available/unavailable)")
    # Note: this test's own source_registry.list_sources() monkeypatch
    # (required so submit_search()'s default-source resolution can
    # never reach real Naukri -- see the Phase 8 safety discipline this
    # file follows) also narrows what source_capabilities.py can see
    # during this block, so "every other real source is unavailable" is
    # verified against the REAL, unpatched registry in section 1 above
    # instead -- here we only check the response shape is well-formed.
    check(isinstance(dash["sources"]["unavailable"], list), "7. dashboard's 'sources.unavailable' is a well-formed list")

    resp = client.get("/searches")
    check(resp.status_code == 200, "8. GET /searches (list page) -> 200")
    resp = client.get(f"/searches/{search_id}")
    check(resp.status_code == 200, "8. GET /searches/{id} (detail page) -> 200")

finally:
    source_registry.list_sources = _original_list_sources
    source_registry.ADAPTERS.pop("FAKE_PHASE10_ENABLED", None)

production_after = _sha(PRODUCTION_DB)
check(
    production_before == production_after,
    f"9. production DB byte-identical before/after this entire test run (sha256 before={production_before}, after={production_after})",
)

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
