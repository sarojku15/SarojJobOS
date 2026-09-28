#!/usr/bin/env python3

"""
Phase 14 -- search-provider discovery for the seven restricted boards
(LinkedIn, Indeed, Foundit, Instahyre, Cutshort, Wellfound, Shine).

Fully offline. SERPER_API_KEY is set to a placeholder, non-secret value
BEFORE this file's first import of search_provider_adapter/
source_registry (see the module-level os.environ assignment right
below the imports) purely so this file's own adapter classes read
status=ENABLED at class-definition time -- the same convention every
adapter in this project uses (see search_provider_adapter.py's module
docstring). This NEVER results in a real network call: every test that
actually invokes .search() first calls set_search_provider() with a
ReplayProvider (real, previously-captured Phase 13 evidence) or a
small in-file fake, and clears it again in a finally block. No test in
this file constructs a real SerperProvider or touches the network.
"""

import json
import os
import sys
import tempfile
import sqlite3
import hashlib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
API_DIR = ROOT / "api"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(API_DIR))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


production_before = _sha(PRODUCTION_DB)

# Placeholder, non-secret value -- never a real credential. Set before
# the first import of search_provider_adapter (this file's own import,
# below) so its ENABLED-at-class-definition-time status reads true for
# every test in this process, matching how every other adapter's
# status is determined in this codebase.
os.environ["SERPER_API_KEY"] = "test-fixture-key-do-not-use"

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


import search_provider
import search_provider_adapter as spa
import restricted_source_registry as rsr
import source_registry
from source_adapter import AdapterBlockedError, AdapterNotEnabledError, AdapterStatus, JobSourceAdapter, SearchQuery
from canonical_job import derive_canonical_job
from cross_source_dedup import find_cross_source_duplicate_candidates
from discover_local import normalize_job
from freshness import classify_freshness
from web_search_evidence_capture import (
    _LINKEDIN_RESULTS,
    _INDEED_RESULTS,
    _FOUNDIT_RESULTS,
    _INSTAHYRE_RESULTS,
    _CUTSHORT_RESULTS,
    _WELLFOUND_RESULTS,
)

REAL_RESULTS_BY_SITE = {
    "LINKEDIN": _LINKEDIN_RESULTS,
    "INDEED": _INDEED_RESULTS,
    "FOUNDIT": _FOUNDIT_RESULTS,
    "INSTAHYRE": _INSTAHYRE_RESULTS,
    "CUTSHORT": _CUTSHORT_RESULTS,
    "WELLFOUND": _WELLFOUND_RESULTS,
}

ROLE = "Site Reliability Engineer"
LOCATION = "Bangalore"


def _hits_for(site_key):
    return [{"title": t, "url": u, "snippet": "", "date": None} for t, u in REAL_RESULTS_BY_SITE[site_key]]


def _build_replay_fixture():
    """One real-evidence-backed fixture keyed by the EXACT query string
    build_site_query() produces for (ROLE, LOCATION) -- proving the
    adapter's own query construction lines up with real captured data,
    not a hand-tuned test double."""
    fixture = {}
    for key in REAL_RESULTS_BY_SITE:
        site = rsr.SITE_BY_KEY[key]
        query_text = rsr.build_site_query(site, ROLE, LOCATION)
        fixture[query_text] = _hits_for(key)
    return fixture


REPLAY_FIXTURE = _build_replay_fixture()


# ---------------------------------------------------------------------
# 1. All seven sources produce records from real-evidence fixtures
# ---------------------------------------------------------------------

try:
    spa.set_search_provider(search_provider.ReplayProvider(fixture_data=REPLAY_FIXTURE))

    per_site_jobs = {}
    for site_key, adapter_cls in [
        ("LINKEDIN", spa.LinkedInSearchProviderAdapter),
        ("INDEED", spa.IndeedSearchProviderAdapter),
        ("FOUNDIT", spa.FounditSearchProviderAdapter),
        ("INSTAHYRE", spa.InstahyreSearchProviderAdapter),
        ("CUTSHORT", spa.CutshortSearchProviderAdapter),
        ("WELLFOUND", spa.WellfoundSearchProviderAdapter),
    ]:
        jobs = adapter_cls().search(SearchQuery(role=ROLE, location=LOCATION))
        per_site_jobs[site_key] = jobs
        check(len(jobs) >= 1, f"1. {site_key} adapter produces at least one job from its real-evidence fixture (got {len(jobs)})")
        check(all(j["source"] == site_key for j in jobs), f"1. every {site_key} job carries source={site_key!r}")
        check(all(j["discovery_source"] == "SEARCH_PROVIDER:REPLAY" for j in jobs), f"1. every {site_key} job's discovery_source names the provider")

    # Shine: real evidence from Phase 13 was zero results -- the adapter
    # must handle an empty hit list cleanly (0 jobs, no error), not
    # fabricate anything.
    shine_query = rsr.build_site_query(rsr.SITE_BY_KEY["SHINE"], ROLE, LOCATION)
    shine_jobs = spa.ShineSearchProviderAdapter().search(SearchQuery(role=ROLE, location=LOCATION))
    check(shine_jobs == [], "1. SHINE adapter returns an empty list (not an error, not a fabricated job) when the provider has zero results for its query")
finally:
    spa.clear_search_provider()


# ---------------------------------------------------------------------
# 2. Off-domain / non-detail-page rejection (hardened hostname check)
# ---------------------------------------------------------------------

linkedin_site = rsr.SITE_BY_KEY["LINKEDIN"]

off_domain_hit = {"title": "Fake job", "url": "https://notlinkedin.com.evil.example/jobs/view/senior-sre-123456789"}
validated, reason = rsr.validate_and_parse_hit(linkedin_site, off_domain_hit)
check(validated is None and reason == "off_domain", "2. a lookalike domain (substring match, not a real subdomain) is rejected as off_domain")

listing_page_hit = {"title": "SRE jobs in Bangalore | LinkedIn", "url": "https://www.linkedin.com/jobs/site-reliability-engineer-jobs-bangalore"}
validated, reason = rsr.validate_and_parse_hit(linkedin_site, listing_page_hit)
check(validated is None and reason == "not_detail_page", "2. a listing/search page URL (no numeric job id) is rejected as not_detail_page")

empty_url_hit = {"title": "Something", "url": ""}
validated, reason = rsr.validate_and_parse_hit(linkedin_site, empty_url_hit)
check(validated is None and reason == "empty_url", "2. a hit with no URL at all is rejected as empty_url, never crashes")

js_scheme_hit = {"title": "x", "url": "javascript:alert(1)"}
validated, reason = rsr.validate_and_parse_hit(linkedin_site, js_scheme_hit)
check(validated is None and reason == "off_domain", "2. a non-http(s) URL scheme is rejected")


# ---------------------------------------------------------------------
# 3. Canonical URL normalization
# ---------------------------------------------------------------------

indeed_site = rsr.SITE_BY_KEY["INDEED"]
tracked_hit = {"title": "Site Reliability Engineer - Bengaluru, Karnataka - Indeed.com", "url": "https://in.indeed.com/viewjob?jk=a1b2c3d4e5f60718&from=serp&xkcb=abc"}
validated, _ = rsr.validate_and_parse_hit(indeed_site, tracked_hit)
check(validated is not None and validated.canonical_url == "https://in.indeed.com/viewjob?jk=a1b2c3d4e5f60718", f"3. Indeed's canonical URL strips tracking params, keeps only jk= (got {validated.canonical_url if validated else None})")

linkedin_hit = {"title": "Acme hiring Senior SRE in Bengaluru", "url": "https://in.linkedin.com/jobs/view/senior-sre-at-acme-4012345678?refId=xyz&trk=serp"}
validated, _ = rsr.validate_and_parse_hit(linkedin_site, linkedin_hit)
check(validated is not None and validated.canonical_url == "https://www.linkedin.com/jobs/view/4012345678/", f"3. LinkedIn's canonical URL is deterministic, built from the job id alone (got {validated.canonical_url if validated else None})")

# Determinism: same input twice -> identical canonical URL.
indeed_validated_a, _ = rsr.validate_and_parse_hit(indeed_site, tracked_hit)
indeed_validated_b, _ = rsr.validate_and_parse_hit(indeed_site, tracked_hit)
check(
    bool(indeed_validated_a and indeed_validated_b and indeed_validated_a.canonical_url == indeed_validated_b.canonical_url),
    "3. canonical URL derivation is deterministic across repeated calls",
)


# ---------------------------------------------------------------------
# 4/5/6. Title / company / location parsing (real Phase 13 evidence)
# ---------------------------------------------------------------------

li_validated, _ = rsr.validate_and_parse_hit(
    linkedin_site, {"title": "Rattle hiring Site Reliability Engineer in Bengaluru, Karnataka, India | LinkedIn", "url": "https://in.linkedin.com/jobs/view/site-reliability-engineer-at-rattle-3713981489"}
)
check((li_validated.title, li_validated.company, li_validated.location) == ("Site Reliability Engineer", "Rattle", "Bengaluru, Karnataka, India"), f"4/5/6. LinkedIn title/company/location parsed correctly (got {li_validated.title!r}, {li_validated.company!r}, {li_validated.location!r})")

foundit_site = rsr.SITE_BY_KEY["FOUNDIT"]
fo_validated, _ = rsr.validate_and_parse_hit(
    foundit_site, {"title": "Site Reliability Engineer with 5 - 7 Years of Experience at inoptra digital in Bengaluru / Bangalore,India", "url": "https://www.foundit.in/job/site-reliability-engineer-inoptra-digital-bengaluru-bangalore-48221160"}
)
check(fo_validated.company == "inoptra digital", f"4/5/6. Foundit company parsed correctly (got {fo_validated.company!r})")

# Indeed: no company available -- honest UNKNOWN, not a parsing bug.
in_validated, _ = rsr.validate_and_parse_hit(indeed_site, tracked_hit)
check(in_validated.company == "", "7. Indeed's own titles never carry a company -- honestly left UNKNOWN (empty), never guessed")


# ---------------------------------------------------------------------
# 8. Malformed result handling
# ---------------------------------------------------------------------

malformed_hits = [
    {},  # no url, no title at all
    {"url": None},
    {"title": "Some Title", "url": "https://in.linkedin.com/jobs/view/some-title-1"},  # too short a numeric id to match \d{8,}
]
for hit in malformed_hits:
    try:
        validated, reason = rsr.validate_and_parse_hit(linkedin_site, hit)
        check(validated is None, f"8. malformed hit {hit!r} is safely rejected, never raises")
    except Exception as error:
        check(False, f"8. malformed hit {hit!r} raised {error!r} instead of being rejected cleanly")


# ---------------------------------------------------------------------
# 9. Duplicate search hits (same job id appears twice in one response)
# ---------------------------------------------------------------------

dup_query = rsr.build_site_query(linkedin_site, ROLE, LOCATION)
dup_fixture = {
    dup_query: [
        {"title": "Rattle hiring Site Reliability Engineer in Bengaluru", "url": "https://in.linkedin.com/jobs/view/x-3713981489"},
        {"title": "Rattle hiring Site Reliability Engineer in Bengaluru", "url": "https://in.linkedin.com/jobs/view/x-3713981489?trk=other"},
    ]
}
try:
    spa.set_search_provider(search_provider.ReplayProvider(fixture_data=dup_fixture))
    jobs = spa.LinkedInSearchProviderAdapter().search(SearchQuery(role=ROLE, location=LOCATION))
    check(len(jobs) == 1, f"9. two hits resolving to the same job id are de-duplicated within one search() call (got {len(jobs)})")
finally:
    spa.clear_search_provider()


# ---------------------------------------------------------------------
# 10. Cross-source dedup reuses the EXISTING canonical_job/cross_source_
# dedup pipeline -- no second dedup implementation.
# ---------------------------------------------------------------------

naukri_job = normalize_job(
    {"source": "NAUKRI", "company": "Acme Fintech Pvt Ltd", "title": "Sr. SRE", "location": "Bangalore", "jd_text": "Reliability engineering role at Acme Fintech handling production incidents and SLOs for our payments platform."},
    1,
)
linkedin_job = normalize_job(
    {
        "source": "LINKEDIN",
        "job_source": "LINKEDIN",
        "discovery_source": "SEARCH_PROVIDER:SERPER",
        "company": "Acme Fintech",
        "title": "Senior Site Reliability Engineer",
        "location": "Bangalore",
        "jd_text": "Reliability engineering role at Acme Fintech handling production incidents and SLOs for our payments platform.",
        "job_url": "https://www.linkedin.com/jobs/view/4012345678/",
    },
    1,
)
canonical_naukri = derive_canonical_job(naukri_job)
canonical_linkedin = derive_canonical_job(linkedin_job)
duplicates = find_cross_source_duplicate_candidates([canonical_naukri, canonical_linkedin])
check(len(duplicates) == 1, f"10. the EXISTING cross_source_dedup module (unmodified) detects a Naukri job and a search-provider LinkedIn job for the same real-world posting as duplicate candidates (got {len(duplicates)})")
if duplicates:
    check({duplicates[0].job_a.source, duplicates[0].job_b.source} == {"NAUKRI", "LINKEDIN"}, "10. the reported duplicate candidate correctly names both sources")


# ---------------------------------------------------------------------
# 11. Provider error handling -> AdapterBlockedError (not a crash)
# ---------------------------------------------------------------------

class _AlwaysErrorsProvider:
    name = "broken"

    def search(self, query, num=10, recency=None):
        raise RuntimeError("simulated provider failure")


try:
    spa.set_search_provider(_AlwaysErrorsProvider())
    try:
        spa.LinkedInSearchProviderAdapter().search(SearchQuery(role=ROLE, location=LOCATION))
        check(False, "11. a provider error must raise AdapterBlockedError, not propagate silently or return []")
    except AdapterBlockedError:
        check(True, "11. a provider error is surfaced as the existing AdapterBlockedError vocabulary")
finally:
    spa.clear_search_provider()


# ---------------------------------------------------------------------
# 12. 429 / timeout handling inside SerperProvider's own retry loop
# ---------------------------------------------------------------------

import urllib.error


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen_429_then_success(*args, **kwargs):
    if not hasattr(_urlopen_429_then_success, "calls"):
        _urlopen_429_then_success.calls = 0
    _urlopen_429_then_success.calls += 1
    if _urlopen_429_then_success.calls == 1:
        raise urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)
    return _FakeResponse(json.dumps({"organic": [{"title": "T", "link": "https://example.com/1", "snippet": "s"}]}).encode())


with patch("time.sleep", return_value=None):
    with patch("urllib.request.urlopen", side_effect=_urlopen_429_then_success):
        provider = search_provider.SerperProvider(api_key="fake-key-for-this-test")
        results = provider.search("test query", num=1, recency=None)
        check(len(results) == 1 and _urlopen_429_then_success.calls == 2, f"12. SerperProvider retries once after a 429 and succeeds on the next attempt (calls={_urlopen_429_then_success.calls})")


def _urlopen_always_times_out(*args, **kwargs):
    raise TimeoutError("simulated timeout")


with patch("time.sleep", return_value=None):
    with patch("urllib.request.urlopen", side_effect=_urlopen_always_times_out):
        provider = search_provider.SerperProvider(api_key="fake-key-for-this-test")
        try:
            provider.search("test query", num=1, recency=None)
            check(False, "12. SerperProvider must raise after exhausting retries on a persistent timeout")
        except search_provider.ProviderSearchError as error:
            # Phase 14.2: the shared retry helper now raises a
            # structured, classifiable ProviderSearchError (error_type=
            # TIMEOUT) instead of a plain RuntimeError -- this is what
            # lets search_provider_manager.py distinguish TIMEOUT from
            # AUTH_FAILED/QUOTA_EXHAUSTED/RATE_LIMITED for failover
            # decisions (Part 5). The OLD plain-RuntimeError shape is
            # gone; this is a deliberate upgrade, not a regression.
            check(error.error_type == search_provider.ProviderErrorType.TIMEOUT, f"12. SerperProvider raises a classified ProviderSearchError(error_type=TIMEOUT) after exhausting retries on a persistent timeout (got {error.error_type})")


# ---------------------------------------------------------------------
# 12b. Malformed JSON response (2026-09-28 hardening pass) --
# json.loads(response.read()) previously escaped _http_request_with_
# retry() as a raw JSONDecodeError (not a URLError/OSError/TimeoutError
# subclass, so none of the existing except clauses caught it), which
# would propagate uncaught out of the entire shared provider layer and
# abort a whole multi-source search batch instead of being isolated to
# one provider/query -- see the exception-boundary audit. Same
# retry-then-raise shape already proven for NETWORK_ERROR/TIMEOUT above.
# ---------------------------------------------------------------------


def _urlopen_malformed_json_always(*args, **kwargs):
    return _FakeResponse(b"<html>502 Bad Gateway</html>")


with patch("time.sleep", return_value=None):
    with patch("urllib.request.urlopen", side_effect=_urlopen_malformed_json_always):
        provider = search_provider.SerperProvider(api_key="fake-key-for-this-test")
        try:
            provider.search("test query", num=1, recency=None)
            check(False, "12b-A/B. SerperProvider must raise after exhausting retries on a persistently malformed (non-JSON) response, not let JSONDecodeError escape")
        except search_provider.ProviderSearchError as error:
            check(True, "12b-A. malformed JSON response does not escape as a raw json.JSONDecodeError -- caught and converted")
            check(error.error_type == search_provider.ProviderErrorType.UNKNOWN, f"12b-B. converted to the existing ProviderSearchError contract (error_type=UNKNOWN), got {error.error_type}")
        except json.JSONDecodeError:
            check(False, "12b-A. a raw json.JSONDecodeError escaped _http_request_with_retry() uncaught")


def _urlopen_malformed_json_then_success(*args, **kwargs):
    if not hasattr(_urlopen_malformed_json_then_success, "calls"):
        _urlopen_malformed_json_then_success.calls = 0
    _urlopen_malformed_json_then_success.calls += 1
    if _urlopen_malformed_json_then_success.calls == 1:
        return _FakeResponse(b"not json at all")
    return _FakeResponse(json.dumps({"organic": [{"title": "T", "link": "https://example.com/1", "snippet": "s"}]}).encode())


with patch("time.sleep", return_value=None):
    with patch("urllib.request.urlopen", side_effect=_urlopen_malformed_json_then_success):
        provider = search_provider.SerperProvider(api_key="fake-key-for-this-test")
        results = provider.search("test query", num=1, recency=None)
        check(len(results) == 1 and _urlopen_malformed_json_then_success.calls == 2, f"12b. existing retry behavior preserved -- a malformed body on attempt 1 retries and succeeds on attempt 2 (calls={_urlopen_malformed_json_then_success.calls})")


def _urlopen_valid_json_success(*args, **kwargs):
    return _FakeResponse(json.dumps({"organic": [{"title": "T2", "link": "https://example.com/2", "snippet": "s2"}]}).encode())


with patch("urllib.request.urlopen", side_effect=_urlopen_valid_json_success):
    provider = search_provider.SerperProvider(api_key="fake-key-for-this-test")
    results = provider.search("test query", num=1, recency=None)
    check(len(results) == 1 and results[0]["title"] == "T2", "12b-D. a well-formed JSON response is completely unaffected by this fix -- parses and returns exactly as before")


# ---------------------------------------------------------------------
# 13. Missing API key handling
# ---------------------------------------------------------------------

with patch.dict(os.environ, {}, clear=False):
    saved = os.environ.pop("SERPER_API_KEY", None)
    try:
        check(search_provider.is_serper_configured() is False, "13. is_serper_configured() is False when SERPER_API_KEY is absent")
        try:
            search_provider.SerperProvider()
            check(False, "13. SerperProvider() must refuse to construct without an API key")
        except RuntimeError:
            check(True, "13. SerperProvider() raises RuntimeError (never a fake/successful provider) without an API key")
    finally:
        if saved is not None:
            os.environ["SERPER_API_KEY"] = saved

check(search_provider.is_serper_configured() is True, "13. is_serper_configured() is True once SERPER_API_KEY is set (this file's own placeholder)")


# ---------------------------------------------------------------------
# 14. Replay fixture support (RecordingProvider / ReplayProvider)
# ---------------------------------------------------------------------

tmp_fixture_path = Path(tempfile.mktemp(suffix=".json"))
try:
    recorder = search_provider.RecordingProvider(search_provider.ReplayProvider(fixture_data=REPLAY_FIXTURE), str(tmp_fixture_path))
    query_text = rsr.build_site_query(linkedin_site, ROLE, LOCATION)
    recorded = recorder.search(query_text, num=10, recency=None)
    check(tmp_fixture_path.exists(), "14. RecordingProvider writes a fixture file after a search")

    replay = search_provider.ReplayProvider(fixture_path=str(tmp_fixture_path))
    replayed = replay.search(query_text, num=10, recency=None)
    check(replayed == recorded, "14. ReplayProvider served from a just-recorded fixture reproduces the exact same results")
    check(replay.search("a query never recorded", num=10, recency=None) == [], "14. ReplayProvider returns [] (never fabricates) for an unrecorded query")
finally:
    tmp_fixture_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------
# 15. Arbitrary role/location query generation -- never hardcoded to SRE
# ---------------------------------------------------------------------

pm_query = rsr.build_site_query(linkedin_site, "Product Manager", "Mumbai")
check(pm_query == 'site:linkedin.com/jobs/view "Product Manager" Mumbai', f"15. build_site_query() works for an arbitrary, non-SRE profession with zero hardcoding (got {pm_query!r})")

de_query = rsr.build_site_query(rsr.SITE_BY_KEY["INDEED"], "Data Engineer", "Hyderabad")
check(de_query == 'site:in.indeed.com/viewjob "Data Engineer" Hyderabad', f"15. build_site_query() generalizes across sites too (got {de_query!r})")


# ---------------------------------------------------------------------
# 16. Freshness UNKNOWN handling (no fabricated date, no auto-reject)
# ---------------------------------------------------------------------

no_date_assessment = classify_freshness("")
check(no_date_assessment.category.value == "UNKNOWN", "16. a search-provider job with no date hint at all classifies as UNKNOWN freshness")

relative_date_assessment = classify_freshness("3 days ago", reference_date=__import__("datetime").date(2026, 9, 21))
check(relative_date_assessment.category.value == "HOT" or relative_date_assessment.age_days == 3, f"16. a relative-date hint a provider DOES supply (e.g. '3 days ago') is still correctly parsed by the EXISTING freshness classifier, unmodified (got {relative_date_assessment})")

# Safety semantics preserved: UNKNOWN freshness must never satisfy the
# existing APPLY_TODAY <=3-day gate (generate_run_report.py's own
# MAX_JOB_AGE_DAYS_FOR_APPLY check, unmodified -- verified structurally
# here since freshness_age_days is None for UNKNOWN).
check(no_date_assessment.age_days is None, "16. UNKNOWN freshness has age_days=None, which generate_run_report.py's existing APPLY_TODAY gate (freshness_age_days is not None and <= 3) already excludes -- unmodified, re-verified here")


# ---------------------------------------------------------------------
# 17. discover_from_sources() registry integration (status gate)
# ---------------------------------------------------------------------

check(source_registry.get_adapter_status("LINKEDIN_SEARCH") == AdapterStatus.ENABLED, "17. LINKEDIN_SEARCH reads ENABLED in this test process (SERPER_API_KEY placeholder set)")

try:
    spa.set_search_provider(search_provider.ReplayProvider(fixture_data=REPLAY_FIXTURE))
    run_report = []
    raw_jobs = source_registry.discover_from_sources(
        [("LINKEDIN_SEARCH", SearchQuery(role=ROLE, location=LOCATION)), ("FOUNDIT_SEARCH", SearchQuery(role=ROLE, location=LOCATION))],
        run_report=run_report,
    )
    check(len(raw_jobs) >= 2, f"17. discover_from_sources() successfully dispatches to two search-provider adapters and collects their jobs (got {len(raw_jobs)})")
    check(all(state.not_enabled is False for state in run_report), "17. neither adapter was skipped as not_enabled (both genuinely ENABLED + queried)")
finally:
    spa.clear_search_provider()

# When SERPER_API_KEY is absent (the real, current state of this
# repository outside this test's placeholder), the source must be
# skipped with ZERO network calls -- verified with a minimal, plain
# JobSourceAdapter (NOT a SearchProviderAdapterBase subclass: Phase
# 14.2's hot-refresh fix gave that family's __init__ its own dynamic
# status recomputation, which would just override a class-level
# NOT_ENABLED here with the real configured-pool state -- this test's
# actual target is discover_from_sources()'s generic skip mechanism,
# not that family's own status logic, which has its own dedicated
# hot-refresh tests in test_phase14_2_hot_refresh.py).
class _NotConfiguredAdapter(JobSourceAdapter):
    name = "LINKEDIN"
    status = AdapterStatus.NOT_ENABLED

    def search(self, query):
        raise AssertionError("must never be called -- this adapter is NOT_ENABLED")


source_registry.ADAPTERS["_TEST_NOT_CONFIGURED"] = _NotConfiguredAdapter
try:
    run_report = []
    raw_jobs = source_registry.discover_from_sources([("_TEST_NOT_CONFIGURED", SearchQuery(role=ROLE, location=LOCATION))], run_report=run_report)
    check(raw_jobs == [] and run_report[0].not_enabled is True, "17. a NOT_ENABLED search-provider adapter is skipped before any network call, exactly like every other not-yet-configured source")
finally:
    source_registry.ADAPTERS.pop("_TEST_NOT_CONFIGURED", None)


# ---------------------------------------------------------------------
# 18. Quality gate (Part 14): >=80% parse rate for six of seven sites
# using REAL captured evidence; Indeed explicitly exempted (documented,
# not silently discarded) and Shine has zero real data to gate on.
# ---------------------------------------------------------------------

for site_key in ["LINKEDIN", "FOUNDIT", "INSTAHYRE", "CUTSHORT", "WELLFOUND"]:
    site = rsr.SITE_BY_KEY[site_key]
    results = REAL_RESULTS_BY_SITE[site_key]
    valid = 0
    parsed = 0
    for title, url in results:
        v, _ = rsr.validate_and_parse_hit(site, {"title": title, "url": url})
        if v:
            valid += 1
            if v.company:
                parsed += 1
    rate = parsed / valid if valid else 0.0
    check(valid >= 1, f"18. {site_key}: at least 1 valid job-detail URL from real evidence (got {valid})")
    check(rate >= 0.8, f"18. {site_key}: parse rate >= 80% on real evidence (got {rate:.0%})")

# Indeed: valid URLs exist (real, on-domain, detail-page), but company
# parse rate is genuinely 0% -- this must be preserved as LIMITED, not
# silently dropped from the source list (Part 14's explicit instruction).
indeed_results = REAL_RESULTS_BY_SITE["INDEED"]
indeed_valid = sum(1 for t, u in indeed_results if rsr.validate_and_parse_hit(indeed_site, {"title": t, "url": u})[0] is not None)
check(indeed_valid == len(indeed_results), f"18. INDEED: every real search-provider URL is still a valid, on-domain job-detail URL (got {indeed_valid}/{len(indeed_results)}) even though company parsing is 0%")
import source_capabilities as sc
# Phase 14.2 replaced the LIMITED final_status with the board-level
# priority vocabulary (ENABLED / AVAILABLE_VIA_SEARCH_PROVIDER /
# SEARCH_PROVIDER_NOT_CONFIGURED / MANUAL_IMPORT_AVAILABLE /
# NOT_AVAILABLE) -- Indeed's own company-parsing caveat is preserved
# separately in search_provider_quality_note, never silently dropped.
indeed_cap = sc.get_source_capability("INDEED")
check(indeed_cap["final_status"] == "AVAILABLE_VIA_SEARCH_PROVIDER", f"18. INDEED is reported AVAILABLE_VIA_SEARCH_PROVIDER once a provider is configured, never silently excluded from the source list (got {indeed_cap['final_status']})")
check(indeed_cap["search_provider_quality_note"] is not None, "18. INDEED's known company-parsing gap is preserved as a separate quality note, not lost when the vocabulary changed")


# ---------------------------------------------------------------------
# 19. Report integration: Discovered_Via / Discovery_Query / Completeness
# ---------------------------------------------------------------------

import init_tracker
import migrate_v2_schema
import tracker as tracker_module
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from score_job import score_job, PROFILE as SAROJ_PROFILE
from job_eligibility import assess_job_eligibility
from search_worker import upsert_candidate_job_match
import generate_run_report

tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase14_report_"))
tmp_db = tmp_dir / "jobos_test.db"
init_tracker.DATA_DIR = tmp_dir
init_tracker.DB_PATH = tmp_db
init_tracker.main()

conn = sqlite3.connect(tmp_db)
conn.execute("PRAGMA foreign_keys = ON")
migrate_v2_schema._create_new_tables(conn)
migrate_v2_schema._ensure_job_columns(conn)
conn.commit()

now = "2026-09-20T00:00:00+00:00"
raw_profile = {
    "identity": {"candidate_id": "cand_phase14", "name": "Phase 14 Test Candidate"},
    "professional_summary": {"total_experience_years": 11},
    "skills": {
        "cloud": [{"name": "AWS"}],
        "containers_orchestration": [{"name": "Kubernetes"}, {"name": "EKS"}, {"name": "AKS"}],
        "infrastructure_iac": [{"name": "Terraform"}],
        "cicd": [{"name": "Jenkins"}, {"name": "ArgoCD"}],
        "observability": [{"name": "Prometheus"}, {"name": "Grafana"}],
    },
    "job_preferences": {"target_roles": ["Senior SRE"], "target_locations": ["Bangalore"]},
}
confirmed_profile = promote_to_confirmed(normalize_candidate_profile(raw_profile))
conn.execute(
    "INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) VALUES (?,?,?,?,?,?,?)",
    ("cand_phase14", "Phase 14 Test Candidate", None, None, now, now, "ACTIVE"),
)
conn.execute(
    "INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)",
    ("cand_phase14", json.dumps(serialize_candidate_profile(confirmed_profile)), now, now),
)
conn.commit()

sp_job = normalize_job(
    {
        "source": "LINKEDIN",
        "job_source": "LINKEDIN",
        "discovery_source": "SEARCH_PROVIDER:SERPER",
        "discovery_query": 'site:linkedin.com/jobs/view "Senior SRE" Bangalore',
        "completeness": 0.67,
        "company": "Acme2",
        "title": "Senior SRE",
        "jd_text": "kubernetes terraform aws eks aks jenkins argocd prometheus grafana slo sla incident rca production cloud platform",
        "job_url": "https://www.linkedin.com/jobs/view/999999999/",
    },
    1,
)
scoring = score_job(sp_job, SAROJ_PROFILE)
tracker_module.upsert_job(conn, sp_job, scoring)
conn.commit()
elig = assess_job_eligibility(sp_job, SAROJ_PROFILE)
upsert_candidate_job_match(conn, "cand_phase14", sp_job, scoring, elig, None)
conn.commit()
conn.close()

out_path = tmp_dir / "report.xlsx"
generate_run_report.generate(str(tmp_db), "cand_phase14", str(out_path))

from openpyxl import load_workbook

wb = load_workbook(out_path)
check(set(wb.sheetnames) == {"APPLY_TODAY", "ALL_MATCHING_JOBS", "NEW_JOBS", "ALREADY_APPLIED", "REJECTED_EXCLUDED", "DUPLICATES", "APPLICATION_TRACKER", "SOURCE_HEALTH", "RUN_SUMMARY"}, f"19. still exactly the existing 9 sheets, no 10th sheet added (got {wb.sheetnames})")

ws = wb["ALL_MATCHING_JOBS"]
headers = [c.value for c in ws[1]]
check({"Discovered_Via", "Discovery_Query", "Completeness"} <= set(headers), "19. the 3 new Phase 14 columns are present")
check(headers.index("Discovered_Via") > headers.index("Resume Variant"), "19. the new columns are appended at the end, not inserted mid-list (no existing column shifted)")

found_row = None
for row in ws.iter_rows(min_row=2, values_only=True):
    d = dict(zip(headers, row))
    if d.get("Source") == "LINKEDIN":
        found_row = d
        break
check(found_row is not None, "19. the search-provider-discovered LinkedIn job appears in the report")
if found_row:
    check(found_row["Discovered_Via"] == "SEARCH_PROVIDER:SERPER", f"19. Discovered_Via shows the real provider attribution (got {found_row['Discovered_Via']!r})")
    check(found_row["Discovery_Query"] == 'site:linkedin.com/jobs/view "Senior SRE" Bangalore', "19. Discovery_Query shows the exact query used")
    check(found_row["Completeness"] == 0.67, "19. Completeness shows the real computed value")


# ---------------------------------------------------------------------
# 20. Existing direct sources (Naukri/Hirist/IIMJobs/Apna) unaffected
# ---------------------------------------------------------------------

from search_profile import _default_sources

# NOTE: THIS test file deliberately sets SERPER_API_KEY (a placeholder)
# for its own testing purposes, so in THIS process the seven *_SEARCH
# adapters genuinely read ENABLED and correctly join _default_sources()
# -- that is the intended, working architecture (a candidate's default
# search plan automatically gains search-provider sources once
# SERPER_API_KEY is really configured, with zero changes to
# search_profile.py/query_planner.py). What must never regress is that
# the four pre-existing direct sources are STILL present alongside them.
# The genuinely-unconfigured baseline (no *_SEARCH sources at all) is
# separately verified in test_phase13_broad_discovery.py, which never
# sets SERPER_API_KEY.
default_sources_here = set(_default_sources())
check({"NAUKRI", "HIRIST", "IIMJOBS", "APNA"} <= default_sources_here, f"20. the four existing direct sources remain in the default source list unchanged (got {sorted(default_sources_here)})")
check(default_sources_here == {"NAUKRI", "HIRIST", "IIMJOBS", "APNA", "LINKEDIN_SEARCH", "INDEED_SEARCH", "FOUNDIT_SEARCH", "INSTAHYRE_SEARCH", "CUTSHORT_SEARCH", "WELLFOUND_SEARCH", "SHINE_SEARCH"}, f"20. once SERPER_API_KEY is configured, all seven search-provider sources automatically join the default source list too, via the EXISTING, unmodified _default_sources()/AdapterStatus mechanism (got {sorted(default_sources_here)})")

direct_job = normalize_job({"source": "NAUKRI", "company": "Acme", "title": "SRE"}, 1)
check(direct_job["discovery_source"] == "NAUKRI" and direct_job["discovery_query"] == "" and direct_job["completeness"] is None, "20. a direct-adapter job's new fields default exactly as before (discovery_source=source, no query, no completeness) -- zero behavior change")


production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"21. production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
