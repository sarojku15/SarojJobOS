#!/usr/bin/env python3

"""
Regression tests for the Phase 13 broad-discovery work: the
discovery_source/job_source CommonJob fields (Part 11), the
WebSearchDiscoveryAdapter provider-interface extension (Part 9), the
employer/ATS fallback detector (Part 12), the source-capability
final_status vocabulary (Part 1/14), and the manual-import pipeline
(Part 13) -- both the pure builder/validator and the end-to-end API
path.

No live network/browser call of any kind. The manual-import API tests
use FastAPI TestClient against an isolated temp database, following
the exact harness scripts/test_phase13_gui_ux_fixes.py already
established (init_dev_db against a tmp path, db_mod.DEV_DB overridden
in both `db` and `api.main`, production DB SHA compared before/after).
"""

import hashlib
import sys
import tempfile
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


# ---------------------------------------------------------------------
# Part 11: discovery_source / job_source additive fields
# ---------------------------------------------------------------------

from discover_local import normalize_job

job_default = normalize_job({"source": "NAUKRI", "company": "Acme", "title": "SRE"}, 1)
check(job_default["job_source"] == "NAUKRI", "11. job_source defaults to source when not supplied")
check(job_default["discovery_source"] == "NAUKRI", "11. discovery_source defaults to source when not supplied")

job_override = normalize_job(
    {
        "source": "LINKEDIN",
        "job_source": "LINKEDIN",
        "discovery_source": "WEB_SEARCH",
        "company": "Acme",
        "title": "SRE",
    },
    1,
)
check(job_override["job_source"] == "LINKEDIN", "11. job_source passthrough when explicitly supplied")
check(job_override["discovery_source"] == "WEB_SEARCH", "11. discovery_source passthrough when explicitly supplied (distinct from job_source)")

# Existing callers that never pass these keys must see zero behavior
# change -- every OTHER field must be unaffected by this addition.
check(set(job_default.keys()) - {"job_source", "discovery_source"} == set(normalize_job({"source": "NAUKRI", "company": "Acme", "title": "SRE"}, 1).keys()) - {"job_source", "discovery_source"}, "11. no other normalize_job() field changed by this addition")


# ---------------------------------------------------------------------
# Part 12: employer/ATS fallback detector (pure, offline)
# ---------------------------------------------------------------------

from employer_ats_resolver import detect_ats_platform, resolve_ats_fallback

check(detect_ats_platform("https://boards.greenhouse.io/examplecompany/jobs/1234567") == ("greenhouse", "examplecompany"), "12. detects a Greenhouse board URL")
check(detect_ats_platform("https://job-boards.greenhouse.io/examplecompany/jobs/1234567") == ("greenhouse", "examplecompany"), "12. detects the newer job-boards.greenhouse.io domain too")
check(detect_ats_platform("https://jobs.lever.co/examplecompany/abcd-1234") == ("lever", "examplecompany"), "12. detects a Lever board URL")
check(detect_ats_platform("https://jobs.ashbyhq.com/examplecompany/posting-abc") == ("ashby", "examplecompany"), "12. detects an Ashby board URL")
check(detect_ats_platform("https://www.linkedin.com/jobs/view/1234567890/") == (None, None), "12. a non-ATS URL (LinkedIn itself) is correctly NOT detected as an ATS")
check(detect_ats_platform("") == (None, None), "12. an empty URL never raises, returns (None, None)")

result = resolve_ats_fallback("https://boards.greenhouse.io/examplecompany/jobs/1234567")
check(result["detected"] is True and result["platform"] == "greenhouse", "12. resolve_ats_fallback() detects and describes a Greenhouse URL")
check(result["board_configured"] is False, "12. an unconfigured board is honestly reported as not configured (config/career_pages.json ships empty)")

result_none = resolve_ats_fallback("https://www.linkedin.com/jobs/view/1234567890/")
check(result_none["detected"] is False, "12. resolve_ats_fallback() honestly reports no ATS match rather than guessing")


# ---------------------------------------------------------------------
# Part 9: WebSearchDiscoveryAdapter provider-interface extension
# ---------------------------------------------------------------------

import web_search_discovery_adapter as wsda
from source_adapter import AdapterNotEnabledError, SearchQuery

adapter = wsda.WebSearchDiscoveryAdapter()

# Backend unset (the honest, current state of this runtime) -> every
# new convenience method must raise the same AdapterNotEnabledError
# search()/health_check() already raise -- never a fake empty result.
wsda.clear_web_search_backend()
try:
    adapter.search_site("linkedin.com/jobs/view", SearchQuery(role="SRE", location="Bangalore"))
    check(False, "9. search_site() must raise AdapterNotEnabledError when no backend is configured")
except AdapterNotEnabledError:
    check(True, "9. search_site() raises AdapterNotEnabledError when no backend is configured")

try:
    adapter.search_jobs("SRE", "Bangalore")
    check(False, "9. search_jobs() must raise AdapterNotEnabledError when no backend is configured")
except AdapterNotEnabledError:
    check(True, "9. search_jobs() raises AdapterNotEnabledError when no backend is configured")

try:
    adapter.search_recent_jobs("SRE", "Bangalore", max_age_days=3)
    check(False, "9. search_recent_jobs() must raise AdapterNotEnabledError when no backend is configured")
except AdapterNotEnabledError:
    check(True, "9. search_recent_jobs() raises AdapterNotEnabledError when no backend is configured")

# A configured backend (simulating a future real search-API credential,
# or an agent-supervised evidence-capture run) -- verify delegation
# actually happens and the site scoping reaches the backend via
# query.extra["site"], restored in a finally block either way.
_captured_queries = []


def _fake_backend(query):
    _captured_queries.append(query)
    return [{"source": "LINKEDIN", "company": "TestCo", "title": query.role, "location": query.location, "job_url": "https://example.com/1"}]


try:
    wsda.set_web_search_backend(_fake_backend)
    jobs = adapter.search_site("linkedin.com/jobs/view", SearchQuery(role="SRE", location="Bangalore"))
    check(len(jobs) == 1 and jobs[0]["company"] == "TestCo", "9. search_site() delegates to a configured backend and returns its jobs")
    check(_captured_queries[-1].extra.get("site") == "linkedin.com/jobs/view", "9. search_site() passes the site restriction through query.extra['site']")

    jobs = adapter.search_jobs("SRE", "Bangalore", max_job_age_days=3)
    check(len(jobs) == 1, "9. search_jobs() delegates to a configured backend")

    jobs = adapter.search_recent_jobs("SRE", "Bangalore", max_age_days=3)
    check(len(jobs) == 1, "9. search_recent_jobs() delegates to a configured backend")
finally:
    wsda.clear_web_search_backend()

check(wsda.WEB_SEARCH_BACKEND is None, "9. backend is cleared after the test (no leaked global state for other tests)")


# ---------------------------------------------------------------------
# Part 1/14: source_capabilities final_status vocabulary
# ---------------------------------------------------------------------

import source_capabilities as sc

caps = {c["source_name"]: c for c in sc.list_source_capabilities()}

check(caps["NAUKRI"]["final_status"] == "ENABLED", "14. NAUKRI (real, live-validated adapter) -> ENABLED")
check(caps["APNA"]["final_status"] == "ENABLED", "14. APNA (real, live-validated adapter) -> ENABLED")
# Phase 14 superseded these three with its own, more specific
# AVAILABLE_VIA_SEARCH_PROVIDER / SEARCH_PROVIDER_NOT_CONFIGURED
# vocabulary once a real search-provider adapter
# (search_provider_adapter.py) was registered for each board -- see
# source_capabilities.py's _search_provider_status(). This environment
# DOES have at least one search-provider *_API_KEY configured in .env
# (correctly loaded before this point -- see search_provider.py's
# root-cause .env-loading fix), so every one of the seven genuinely
# reports AVAILABLE_VIA_SEARCH_PROVIDER, not the Phase 13
# LIMITED/MANUAL_IMPORT placeholder these assertions originally
# checked for, and not SEARCH_PROVIDER_NOT_CONFIGURED either (that
# value is for an environment with zero provider keys at all).
check(caps["LINKEDIN"]["final_status"] == "AVAILABLE_VIA_SEARCH_PROVIDER", "14. LINKEDIN (Phase 14 search-provider adapter registered, a provider key IS configured) -> AVAILABLE_VIA_SEARCH_PROVIDER")
check(caps["INSTAHYRE"]["final_status"] == "AVAILABLE_VIA_SEARCH_PROVIDER", "14. INSTAHYRE -> AVAILABLE_VIA_SEARCH_PROVIDER")
check(caps["WELLFOUND"]["final_status"] == "AVAILABLE_VIA_SEARCH_PROVIDER", "14. WELLFOUND -> AVAILABLE_VIA_SEARCH_PROVIDER")
check(caps["SHINE"]["final_status"] == "AVAILABLE_VIA_SEARCH_PROVIDER", "14. SHINE -> AVAILABLE_VIA_SEARCH_PROVIDER")
check(caps["GREENHOUSE"]["final_status"] == "ATS_FALLBACK", "14. GREENHOUSE (existing ATS provider, zero boards configured) -> ATS_FALLBACK")
check(caps["WEB_SEARCH"]["final_status"] == "NOT_CONFIGURED", "14. WEB_SEARCH (no backend configured) -> NOT_CONFIGURED")
check(caps["LINKEDIN"]["manual_import_available"] is True, "14. LINKEDIN is manual_import_available")
check(caps["GREENHOUSE"]["manual_import_available"] is False, "14. an ATS provider (not a job board a candidate browses) is NOT manual_import_available")
check(caps["LINKEDIN"]["search_provider_evidence"]["normalizable_jobs"] == 9, "14. LINKEDIN's captured evidence record is exposed with its real count")
check(caps["NAUKRI"]["search_provider_evidence"] is None, "14. a source never investigated for search-provider evidence has None, not a fabricated record")

_valid_final_statuses = {
    "ENABLED", "DIRECT_PUBLIC", "SEARCH_PROVIDER", "ATS_FALLBACK", "MANUAL_IMPORT", "LIMITED",
    "NOT_AUTHORIZED", "BLOCKED", "NOT_CONFIGURED",
    # Phase 14 additions (source_capabilities.py's _search_provider_status()):
    "AVAILABLE_VIA_SEARCH_PROVIDER", "SEARCH_PROVIDER_NOT_CONFIGURED",
}
check(all(c["final_status"] in _valid_final_statuses for c in caps.values()), "14. every source's final_status is one of the documented Part 14 vocabulary values")


# ---------------------------------------------------------------------
# Part 10: real search-provider evidence replays cleanly through the
# existing pipeline (normalize -> dedup -> eligibility -> score)
# ---------------------------------------------------------------------

from web_search_evidence_capture import CAPTURED_EVIDENCE
from discover_local import deduplicate
from job_eligibility import assess_job_eligibility
from score_job import score_job, PROFILE

all_captured_raw = []
for _source, (jobs, _skipped, _query) in CAPTURED_EVIDENCE.items():
    all_captured_raw.extend(jobs)

check(len(all_captured_raw) >= 25, f"10. captured real search-provider evidence yields a substantial job set (got {len(all_captured_raw)})")

normalized_evidence = [normalize_job(dict(j), i) for i, j in enumerate(all_captured_raw, 1)]
check(all(j["discovery_source"] == "WEB_SEARCH" for j in normalized_evidence), "10. every captured-evidence job normalizes with discovery_source=WEB_SEARCH")
_sources_with_normalizable_jobs = {source for source, (jobs, _s, _q) in CAPTURED_EVIDENCE.items() if jobs}
check(_sources_with_normalizable_jobs == {"LINKEDIN", "FOUNDIT", "INSTAHYRE", "CUTSHORT", "WELLFOUND"}, f"10. exactly the five sources with parseable company names yield normalizable jobs (Indeed has none, Shine had zero results this session) -- got {_sources_with_normalizable_jobs}")
check({j["job_source"] for j in normalized_evidence} == _sources_with_normalizable_jobs, "10. job_source correctly distinguishes each source with real evidence")

unique_evidence, dup_count = deduplicate(normalized_evidence)
check(dup_count == 0, "10. real captured URLs are all genuinely distinct -- zero spurious in-batch duplicates")

_scored_ok = 0
for job in unique_evidence:
    elig = assess_job_eligibility(job, PROFILE)
    if elig.eligible:
        score_job(job, PROFILE, experience_assessment=elig.experience_assessment)
        _scored_ok += 1
check(_scored_ok > 0, f"10. at least one real captured-evidence job is eligible and scores cleanly through the unmodified scoring engine (got {_scored_ok})")


# ---------------------------------------------------------------------
# Part 13: manual_import builder/validator (pure, offline)
# ---------------------------------------------------------------------

from manual_import import ManualImportValidationError, build_manual_import_raw_job, validate_manual_import

try:
    validate_manual_import({"job_source": "LINKEDIN", "title": "", "company": "Acme", "job_url": "https://x.com/1"})
    check(False, "13. validate_manual_import() must reject a missing title")
except ManualImportValidationError:
    check(True, "13. validate_manual_import() rejects a missing title")

try:
    validate_manual_import({"job_source": "LINKEDIN", "title": "SRE", "company": "Acme", "job_url": "not-a-url"})
    check(False, "13. validate_manual_import() must reject a non-http(s) job_url")
except ManualImportValidationError:
    check(True, "13. validate_manual_import() rejects a non-http(s) job_url")

try:
    validate_manual_import({"job_source": "BOGUS_SOURCE", "title": "SRE", "company": "Acme", "job_url": "https://x.com/1"})
    check(False, "13. validate_manual_import() must reject an unknown job_source")
except ManualImportValidationError:
    check(True, "13. validate_manual_import() rejects an unknown job_source")

valid_payload = {
    "job_source": "linkedin",
    "title": "Senior SRE",
    "company": "Acme Corp",
    "job_url": "https://in.linkedin.com/jobs/view/12345",
    "location": "Bengaluru",
}
validate_manual_import(valid_payload)  # must not raise
raw = build_manual_import_raw_job(valid_payload)
check(raw["source"] == "LINKEDIN" and raw["job_source"] == "LINKEDIN", "13. build_manual_import_raw_job() uses the real platform name as source/job_source (case-normalized)")
check(raw["discovery_source"] == "MANUAL_IMPORT", "13. build_manual_import_raw_job() tags discovery_source=MANUAL_IMPORT")
check(raw["application_url"] == raw["job_url"], "13. application_url defaults to job_url when not separately supplied")
check(raw["mandatory_skills"] == [] and raw["preferred_skills"] == [], "13. manual import never fabricates a skills list")


# ---------------------------------------------------------------------
# Part 13 end-to-end: manual import via the real API, temp DB only
# ---------------------------------------------------------------------

import source_registry

_original_list_sources = source_registry.list_sources

try:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase13_broad_"))
    tmp_db = tmp_dir / "jobos_test.db"

    import init_dev_db

    init_dev_db.init_dev_db(tmp_db)

    import db as db_mod

    db_mod.DEV_DB = tmp_db

    from fastapi.testclient import TestClient
    import main as api_main

    api_main.db_mod.DEV_DB = tmp_db

    client = TestClient(api_main.app)

    resp = client.post("/api/candidates", json={"name": "Import Test Candidate"})
    check(resp.status_code == 200, "13e2e. setup: candidate created")
    candidate_id = resp.json()["candidate_id"]

    import_payload = {
        "job_source": "LINKEDIN",
        "title": "Senior SRE",
        "company": "Acme Corp",
        "job_url": "https://in.linkedin.com/jobs/view/99999",
        "location": "Bengaluru",
    }

    # DRAFT profile (the default for a freshly-created candidate) -> the
    # same 409 process_queue_item()/trigger_run() already enforce for a
    # real search run, never a 500, and never an auto-confirm.
    resp = client.post(f"/api/candidates/{candidate_id}/jobs/import", json=import_payload)
    check(resp.status_code == 409, f"13e2e. importing against a DRAFT (unconfirmed) profile returns 409, not a 500 or a silent bypass (got {resp.status_code})")

    resp = client.put(
        f"/api/candidates/{candidate_id}/profile",
        json={
            "current_title": "Senior SRE",
            "total_experience_years": 11,
            "skills": {"other": [{"name": "Kubernetes"}, {"name": "Terraform"}, {"name": "AWS"}]},
            "job_preferences": {"target_roles": ["Senior SRE", "Site Reliability Engineer"], "target_locations": ["Bengaluru"]},
        },
    )
    check(resp.status_code == 200, "13e2e. setup: profile fields saved")
    resp = client.post(f"/api/candidates/{candidate_id}/profile/confirm")
    check(resp.status_code == 200 and resp.json()["metadata"]["profile_status"] == "CONFIRMED", "13e2e. setup: profile confirmed")

    resp = client.post(f"/api/candidates/{candidate_id}/jobs/import", json=import_payload)
    check(resp.status_code == 200, f"13e2e. import against a CONFIRMED profile succeeds (got {resp.status_code}: {resp.text[:200]})")
    body = resp.json()
    check(body["source"] == "LINKEDIN" and body["company"] == "Acme Corp", "13e2e. import response reflects the human-supplied source/company, not a fabricated one")
    check("job_id" in body and body["job_id"].startswith("LINKEDIN-"), "13e2e. a deterministic, URL-derived job_id is assigned")

    # Validation failure -> a clean 422, never a 500.
    resp = client.post(
        f"/api/candidates/{candidate_id}/jobs/import",
        json={"job_source": "LINKEDIN", "title": "", "company": "Acme", "job_url": "https://x.com/1"},
    )
    check(resp.status_code == 422, f"13e2e. a missing required field returns a clean 422 (got {resp.status_code})")

    # Unknown candidate -> a clean 404, never a 500.
    resp = client.post("/api/candidates/cand_does_not_exist_00000000/jobs/import", json=import_payload)
    check(resp.status_code == 404, f"13e2e. importing against an unknown candidate_id returns a clean 404 (got {resp.status_code})")

    # No auto-apply control: importing a job must never itself submit an
    # application anywhere -- confirmed structurally by the response
    # shape (score/priority/eligibility only, no application-status
    # mutation) and by the existing repo-wide no-auto-apply token scan
    # in test_phase13_gui_ux_fixes.py, not re-duplicated here.
    check(set(body.keys()) == {"job_id", "source", "company", "title", "eligible", "eligibility_reason", "score", "priority", "status", "matched_skills", "missing_skills"}, "13e2e. import response contains only scoring/eligibility fields, nothing application-submission-shaped")

finally:
    source_registry.list_sources = _original_list_sources

production_after = _sha(PRODUCTION_DB)
check(
    production_before == production_after,
    f"17. production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})",
)

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
