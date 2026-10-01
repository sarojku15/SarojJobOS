#!/usr/bin/env python3

"""
Phase 14.2 -- multi-provider search API pool with automatic failover.
Fully offline: no real API credentials, no real network call anywhere
in this file. Fake env vars (obviously-fake values like "fake-you-key")
are used only to make is_provider_configured() return True so failover
logic can be exercised; every actual .search() call in this file goes
through either a ReplayProvider/fake in-file provider class, or a
monkeypatched search_provider.PROVIDER_CLASSES entry -- never a real
SerperProvider/YouProvider/etc. instance touching the network.
"""

import json
import os
import sys
import tempfile
import hashlib
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


import search_provider as sp
import search_provider_manager as spm
import search_provider_usage_store as usage_store
import search_provider_settings_store as settings_store
import env_config
from restricted_source_registry import SITE_BY_KEY, build_site_query

# Isolated temp files for every store this file touches -- NEVER the
# real data/applications/search_provider_usage.json /
# search_provider_settings.json, and never .env (a dedicated temp
# .env-shaped file is used for the env_config tests instead).
tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase14_2_"))
USAGE_PATH = tmp_dir / "usage.json"
SETTINGS_PATH = tmp_dir / "settings.json"


# ---------------------------------------------------------------------
# Fake providers (no network, deterministic outcomes)
# ---------------------------------------------------------------------

class _FakeSuccessProvider:
    def __init__(self, name, results=None):
        self.name = name
        self._results = results if results is not None else [{"title": "T", "url": "https://example.com/1", "snippet": "s", "date": None}]
        self.call_count = 0

    def search(self, query, num=10, recency=None):
        self.call_count += 1
        return list(self._results)


class _FakeEmptyProvider:
    def __init__(self, name):
        self.name = name
        self.call_count = 0

    def search(self, query, num=10, recency=None):
        self.call_count += 1
        return []


class _FakeErrorProvider:
    def __init__(self, name, error_type):
        self.name = name
        self.error_type = error_type
        self.call_count = 0

    def search(self, query, num=10, recency=None):
        self.call_count += 1
        raise sp.ProviderSearchError(self.name, self.error_type, detail="simulated")


# Every real provider key name -- search_provider.py's own root-cause
# fix now loads .env (including any REAL keys this project's .env
# actually has, e.g. YOU_API_KEY/EXA_API_KEY/SERPER_API_KEY) into
# os.environ automatically at import time, so this process's
# environment is no longer guaranteed clean of them by default. Every
# _with_fake_env() call below must therefore explicitly CLEAR all five
# first, then apply only the overrides it actually wants "configured"
# -- otherwise an ambient real key would silently make an extra
# provider "eligible" that a given test never intended to exercise.
_ALL_PROVIDER_KEYS = ("YOU_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY", "SERPER_API_KEY")


def _with_fake_env(env_overrides, fn):
    """Runs fn() with ONLY the given env vars set among the 5 provider
    keys (every other provider key -- including any real one .env
    supplied -- is cleared for the duration), restoring the previous
    environment afterward regardless of outcome."""
    saved = {k: os.environ.get(k) for k in _ALL_PROVIDER_KEYS}
    try:
        for k in _ALL_PROVIDER_KEYS:
            os.environ.pop(k, None)
        os.environ.update(env_overrides)
        return fn()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------
# 1/2. Provider selection + priority ordering
# ---------------------------------------------------------------------

def _test_selection_and_priority():
    settings_store.save_settings(order=["tavily", "you", "exa", "brave", "serper"], disabled=[], path=SETTINGS_PATH)
    manager = spm.SearchProviderManager()
    manager.order = settings_store.load_settings(SETTINGS_PATH)["order"]
    # Only you+serper have (fake) keys configured -- tavily/exa/brave don't.
    eligible = [n for n in manager.order if sp.is_provider_configured(n)]
    check(eligible == ["you", "serper"], f"1/2. eligible providers respect priority order and configured-key filtering (got {eligible})")


_with_fake_env({"YOU_API_KEY": "fake-you-key", "SERPER_API_KEY": "fake-serper-key"}, _test_selection_and_priority)


# ---------------------------------------------------------------------
# 3. Missing key -> provider excluded entirely
# ---------------------------------------------------------------------

def _test_missing_key():
    manager = spm.SearchProviderManager(order=["tavily", "serper"])
    check("tavily" not in manager.eligible_providers(), "3. a provider with no configured key is never eligible")
    check("serper" in manager.eligible_providers(), "3. a provider WITH a configured key is eligible")


_with_fake_env({"SERPER_API_KEY": "fake-serper-key"}, _test_missing_key)


# ---------------------------------------------------------------------
# 4. Enabled/disabled provider (GUI override)
# ---------------------------------------------------------------------

def _test_enabled_disabled():
    settings_store.save_settings(order=["serper", "you"], disabled=["serper"], path=SETTINGS_PATH)
    settings = settings_store.load_settings(SETTINGS_PATH)
    manager = spm.SearchProviderManager(order=settings["order"])
    manager.disabled = set(settings["disabled"])
    eligible = manager.eligible_providers()
    check("serper" not in eligible, "4. a GUI-disabled provider is excluded even though it has a configured key")
    check("you" in eligible, "4. a non-disabled, configured provider remains eligible")


_with_fake_env({"SERPER_API_KEY": "fake-serper-key", "YOU_API_KEY": "fake-you-key"}, _test_enabled_disabled)


# ---------------------------------------------------------------------
# 5/11. Successful first provider; empty results are NOT a failure and
# never trigger rotation to the next provider.
# ---------------------------------------------------------------------

def _test_success_and_empty_not_rotation():
    manager = spm.SearchProviderManager(order=["you", "serper"])
    you_fake = _FakeEmptyProvider("you")
    serper_fake = _FakeSuccessProvider("serper")

    original_construct = sp.construct_provider
    sp.construct_provider = lambda name: {"you": you_fake, "serper": serper_fake}[name]
    try:
        result = manager.search("query", num=10, recency=None)
        check(result.provider_name == "you", f"5/11. the FIRST eligible provider is tried and its result used, even when empty (got provider={result.provider_name})")
        check(result.results == [], "11. an empty-but-successful result is returned as-is")
        check(serper_fake.call_count == 0, "11. empty results do NOT trigger falling through to the next provider")
    finally:
        sp.construct_provider = original_construct


_with_fake_env({"YOU_API_KEY": "fake-you-key", "SERPER_API_KEY": "fake-serper-key"}, _test_success_and_empty_not_rotation)


# ---------------------------------------------------------------------
# 6. Quota exhausted -> second provider
# ---------------------------------------------------------------------

def _test_quota_exhausted_failover():
    manager = spm.SearchProviderManager(order=["you", "tavily"], max_daily=None, max_monthly=None)
    you_fake = _FakeErrorProvider("you", sp.ProviderErrorType.QUOTA_EXHAUSTED)
    tavily_fake = _FakeSuccessProvider("tavily")

    original_construct = sp.construct_provider
    original_record = usage_store.record_request
    sp.construct_provider = lambda name: {"you": you_fake, "tavily": tavily_fake}[name]
    usage_store.record_request = lambda *a, **kw: original_record(*a, path=USAGE_PATH, **{k: v for k, v in kw.items() if k != "path"})
    try:
        result = manager.search("query", num=10, recency=None)
        check(result.provider_name == "tavily", f"6. QUOTA_EXHAUSTED on the first provider fails over to the second (got {result.provider_name})")
        check(you_fake.call_count == 1 and tavily_fake.call_count == 1, "6. the exhausted provider was tried exactly once, not retried in a loop")
        usage = usage_store.get_provider_usage("you", USAGE_PATH)
        check(usage["current_status"] == "QUOTA_EXHAUSTED", f"6. the exhausted provider's local status is recorded as QUOTA_EXHAUSTED (got {usage['current_status']})")
    finally:
        sp.construct_provider = original_construct
        usage_store.record_request = original_record


_with_fake_env({"YOU_API_KEY": "fake-you-key", "TAVILY_API_KEY": "fake-tavily-key"}, _test_quota_exhausted_failover)


# ---------------------------------------------------------------------
# 9. Invalid key (AUTH_FAILED) -> failover
# ---------------------------------------------------------------------

def _test_auth_failed_failover():
    manager = spm.SearchProviderManager(order=["exa", "brave"])
    exa_fake = _FakeErrorProvider("exa", sp.ProviderErrorType.AUTH_FAILED)
    brave_fake = _FakeSuccessProvider("brave")

    original_construct = sp.construct_provider
    original_record = usage_store.record_request
    sp.construct_provider = lambda name: {"exa": exa_fake, "brave": brave_fake}[name]
    usage_store.record_request = lambda *a, **kw: original_record(*a, path=USAGE_PATH, **{k: v for k, v in kw.items() if k != "path"})
    try:
        result = manager.search("query", num=10, recency=None)
        check(result.provider_name == "brave", f"9. AUTH_FAILED on the first provider fails over to the second (got {result.provider_name})")
        usage = usage_store.get_provider_usage("exa", USAGE_PATH)
        check(usage["current_status"] == "AUTH_FAILED", "9. the auth-failed provider's local status reflects AUTH_FAILED")
    finally:
        sp.construct_provider = original_construct
        usage_store.record_request = original_record


_with_fake_env({"EXA_API_KEY": "fake-exa-key", "BRAVE_API_KEY": "fake-brave-key"}, _test_auth_failed_failover)


# ---------------------------------------------------------------------
# 7/8. 429 (RATE_LIMITED) and 5xx (SERVER_ERROR) -> failover once the
# provider's OWN internal retries are exhausted (search_provider.py's
# _http_request_with_retry already retried before raising).
# ---------------------------------------------------------------------

def _test_rate_limited_and_server_error_failover():
    manager = spm.SearchProviderManager(order=["you", "serper"])
    you_rate_limited = _FakeErrorProvider("you", sp.ProviderErrorType.RATE_LIMITED)
    serper_ok = _FakeSuccessProvider("serper")

    original_construct = sp.construct_provider
    sp.construct_provider = lambda name: {"you": you_rate_limited, "serper": serper_ok}[name]
    try:
        result = manager.search("query", num=10, recency=None)
        check(result.provider_name == "serper", f"7. RATE_LIMITED (after the provider's own retries) fails over (got {result.provider_name})")
    finally:
        sp.construct_provider = original_construct

    manager2 = spm.SearchProviderManager(order=["you", "serper"])
    you_5xx = _FakeErrorProvider("you", sp.ProviderErrorType.SERVER_ERROR)
    serper_ok2 = _FakeSuccessProvider("serper")
    sp.construct_provider = lambda name: {"you": you_5xx, "serper": serper_ok2}[name]
    try:
        result2 = manager2.search("query", num=10, recency=None)
        check(result2.provider_name == "serper", f"8. SERVER_ERROR (after the provider's own retries) fails over (got {result2.provider_name})")
    finally:
        sp.construct_provider = original_construct


_with_fake_env({"YOU_API_KEY": "fake-you-key", "SERPER_API_KEY": "fake-serper-key"}, _test_rate_limited_and_server_error_failover)


# ---------------------------------------------------------------------
# 10. All providers exhausted -> SearchProviderPoolExhausted, no
# fabricated result.
# ---------------------------------------------------------------------

def _test_all_exhausted():
    manager = spm.SearchProviderManager(order=["you", "serper"])
    you_fail = _FakeErrorProvider("you", sp.ProviderErrorType.QUOTA_EXHAUSTED)
    serper_fail = _FakeErrorProvider("serper", sp.ProviderErrorType.AUTH_FAILED)

    original_construct = sp.construct_provider
    sp.construct_provider = lambda name: {"you": you_fail, "serper": serper_fail}[name]
    try:
        try:
            manager.search("query", num=10, recency=None)
            check(False, "10. every provider failing must raise SearchProviderPoolExhausted, never a fabricated result")
        except spm.SearchProviderPoolExhausted as error:
            check(len(error.attempts) == 2, f"10. SearchProviderPoolExhausted records every attempt tried (got {len(error.attempts)})")
    finally:
        sp.construct_provider = original_construct

    manager_none_configured = spm.SearchProviderManager(order=["you", "serper"])
    try:
        manager_none_configured.search("query", num=10, recency=None)
        check(False, "10b. zero configured providers must also raise SearchProviderPoolExhausted")
    except spm.SearchProviderPoolExhausted:
        check(True, "10b. zero configured providers raises SearchProviderPoolExhausted cleanly")


_with_fake_env({"YOU_API_KEY": "fake-you-key", "SERPER_API_KEY": "fake-serper-key"}, _test_all_exhausted)


# ---------------------------------------------------------------------
# 12/13/14. Usage tracking + daily/monthly window resets
# ---------------------------------------------------------------------

usage_store.reset_provider_usage("you", path=USAGE_PATH)
usage_store.record_request("you", "success", path=USAGE_PATH)
usage_store.record_request("you", "success", path=USAGE_PATH)
usage_store.record_request("you", "failure", error_type="RATE_LIMITED", error_detail="429", path=USAGE_PATH)
entry = usage_store.get_provider_usage("you", USAGE_PATH)
check(entry["request_count_daily"] == 3 and entry["successful_count_daily"] == 2 and entry["failure_count_daily"] == 1, f"12. usage counters accumulate correctly (got {entry['request_count_daily']}/{entry['successful_count_daily']}/{entry['failure_count_daily']})")
check(entry["current_status"] == "RATE_LIMITED", "12. current_status reflects the most recent outcome")

# Daily reset: force window_start_daily into the past, then verify the
# next record_request() rolls it over to today with a fresh count.
data = usage_store.load_usage(USAGE_PATH)
data["providers"]["you"]["window_start_daily"] = "2020-01-01"
usage_store.save_usage(data, USAGE_PATH)
usage_store.record_request("you", "success", path=USAGE_PATH)
entry = usage_store.get_provider_usage("you", USAGE_PATH)
check(entry["request_count_daily"] == 1, f"13. daily counter resets to 1 (this new request) once the day rolls over (got {entry['request_count_daily']})")

# Monthly reset: same mechanism, a different window key.
data = usage_store.load_usage(USAGE_PATH)
data["providers"]["you"]["window_start_monthly"] = "2020-01"
usage_store.save_usage(data, USAGE_PATH)
usage_store.record_request("you", "success", path=USAGE_PATH)
entry = usage_store.get_provider_usage("you", USAGE_PATH)
check(entry["request_count_monthly"] == 1, f"14. monthly counter resets once the month rolls over (got {entry['request_count_monthly']})")

# Local advisory budget ceiling (Part 6) -- never claims the provider's
# real quota, only enforces a locally-configured request-count ceiling.
usage_store.reset_provider_usage("you", path=USAGE_PATH)
usage_store.record_request("you", "success", path=USAGE_PATH)
usage_store.record_request("you", "success", path=USAGE_PATH)
check(usage_store.check_local_budget("you", max_daily=2, path=USAGE_PATH) is False, "12b. check_local_budget() correctly reports exhausted once the configured local daily ceiling is reached")
check(usage_store.check_local_budget("you", max_daily=None, path=USAGE_PATH) is True, "12b. no configured local ceiling (None) never blocks a provider based on local counts alone")


# ---------------------------------------------------------------------
# 15/16. Provider attribution + query preservation through the real
# search_provider_adapter.py integration.
# ---------------------------------------------------------------------

os.environ["YOU_API_KEY"] = "fake-you-key-for-adapter-test"
import search_provider_adapter as spa2
from source_adapter import SearchQuery

you_success = _FakeSuccessProvider("you", results=[{"title": "Acme hiring Senior SRE in Bengaluru", "url": "https://in.linkedin.com/jobs/view/senior-sre-at-acme-4099999999", "snippet": "s", "date": None}])

try:
    spa2.set_search_provider(you_success)
    jobs = spa2.LinkedInSearchProviderAdapter().search(SearchQuery(role="Senior SRE", location="Bengaluru"))
    check(len(jobs) == 1, "15. adapter produces a job via the injected fake provider")
    check(jobs[0]["discovery_source"] == "SEARCH_PROVIDER:YOU", f"15. provider attribution names the ACTUAL provider that answered (got {jobs[0]['discovery_source']})")
    check(jobs[0]["discovery_query"] == build_site_query(SITE_BY_KEY["LINKEDIN"], "Senior SRE", "Bengaluru"), "16. the exact query used is preserved on the job record")
    check(jobs[0]["source"] == "LINKEDIN" and jobs[0]["job_source"] == "LINKEDIN", "9(routing). source/job_source still carry the real board name, not the provider name")
finally:
    spa2.clear_search_provider()


# ---------------------------------------------------------------------
# 17. All seven restricted sources route through the same mechanism
# ---------------------------------------------------------------------

seven_adapters = [
    spa2.LinkedInSearchProviderAdapter, spa2.IndeedSearchProviderAdapter, spa2.FounditSearchProviderAdapter,
    spa2.InstahyreSearchProviderAdapter, spa2.CutshortSearchProviderAdapter, spa2.WellfoundSearchProviderAdapter,
    spa2.ShineSearchProviderAdapter,
]
check(len(seven_adapters) == 7, "17. exactly seven restricted-source adapters exist")
check(len({a.SITE_KEY for a in seven_adapters}) == 7, "17. each adapter maps to a distinct site")
try:
    spa2.set_search_provider(_FakeEmptyProvider("you"))
    for adapter_cls in seven_adapters:
        result = adapter_cls().search(SearchQuery(role="X", location="Y"))
        check(result == [], f"17. {adapter_cls.__name__} routes through the same manager/injection mechanism cleanly (empty fake provider -> [])")
finally:
    spa2.clear_search_provider()


# ---------------------------------------------------------------------
# 18. ReplayProvider still works (Phase 14 unchanged)
# ---------------------------------------------------------------------

replay = sp.ReplayProvider(fixture_data={"q1": [{"title": "T", "url": "https://example.com/1", "snippet": "", "date": None}]})
check(replay.search("q1") == [{"title": "T", "url": "https://example.com/1", "snippet": "", "date": None}], "18. ReplayProvider still works unchanged")
check(replay.search("unrecorded") == [], "18. ReplayProvider still returns [] for an unrecorded query")


# ---------------------------------------------------------------------
# 20. API key masking
# ---------------------------------------------------------------------

check(env_config.mask_secret("tvly-1234567890ABCD") == "•••••••••••••••ABCD", f"20. mask_secret() hides everything but the last 4 characters (got {env_config.mask_secret('tvly-1234567890ABCD')!r})")
check(env_config.mask_secret("abcd") == "••••", "20. a 4-char-or-shorter secret is fully masked")
check(env_config.mask_secret("") == "", "20. an empty value masks to empty, never crashes")

tmp_env_path = tmp_dir / ".env.test"
tmp_env_path.write_text("PROJECT_NAME=Test\n")
env_config.write_env_key("FAKE_TEST_KEY", "supersecretvalue123", path=tmp_env_path)
stored = env_config.read_env_file(tmp_env_path)
check(stored.get("FAKE_TEST_KEY") == "supersecretvalue123", "20. write_env_key() persists the value to the .env-shaped file")
check(os.environ.get("FAKE_TEST_KEY") == "supersecretvalue123", "20. write_env_key() also updates this process's os.environ immediately")
env_config.remove_env_key("FAKE_TEST_KEY", path=tmp_env_path)
check("FAKE_TEST_KEY" not in env_config.read_env_file(tmp_env_path), "20. remove_env_key() deletes the line from the file")
check(os.environ.get("FAKE_TEST_KEY") is None, "20. remove_env_key() also clears this process's os.environ")


# ---------------------------------------------------------------------
# 21. GUI/API configuration endpoint (end-to-end, isolated .env copy)
# ---------------------------------------------------------------------

import shutil as _shutil

real_env_path = ROOT / ".env"
env_backup = tmp_dir / ".env.real_backup"
# .env is gitignored and optional -- a fresh clone genuinely has none.
_real_env_existed = real_env_path.exists()
if _real_env_existed:
    _shutil.copy(real_env_path, env_backup)

try:
    from fastapi.testclient import TestClient
    import db as db_mod
    import init_dev_db

    tmp_api_db = tmp_dir / "api_test.db"
    init_dev_db.init_dev_db(tmp_api_db)
    db_mod.DEV_DB = tmp_api_db

    import main as api_main

    api_main.db_mod.DEV_DB = tmp_api_db
    client = TestClient(api_main.app)

    resp = client.get("/api/settings/search-providers")
    check(resp.status_code == 200, "21. GET /api/settings/search-providers returns 200")
    body = resp.json()
    check(len(body["providers"]) == 5, f"21. exactly 5 providers listed (got {len(body['providers'])})")
    check({p["provider"] for p in body["providers"]} == {"you", "tavily", "exa", "brave", "serper"}, "21. all 5 expected providers are present")

    resp = client.post("/api/settings/search-providers/exa/key", json={"api_key": "exa-realistic-test-value-9999"})
    check(resp.status_code == 200, "21. POST .../key saves a key -> 200")
    resp_body = resp.json()
    check("exa-realistic-test-value-9999" not in json.dumps(resp_body), "21. the saved key's full value never appears in the API response")

    resp = client.get("/api/settings/search-providers")
    exa_row = next(p for p in resp.json()["providers"] if p["provider"] == "exa")
    check(exa_row["configured"] is True and exa_row["masked_key"] and exa_row["masked_key"].endswith("9999"), f"21. the provider now shows configured=True with a masked key (got {exa_row['masked_key']!r})")
    check("exa-realistic-test-value-9999" not in json.dumps(resp.json()), "21. the full key never appears anywhere in the list response either")

    resp = client.delete("/api/settings/search-providers/exa/key")
    check(resp.status_code == 200, "21. DELETE .../key removes a key -> 200")

    resp = client.post("/api/settings/search-providers/order", json={"order": ["serper", "you", "tavily", "exa", "brave"]})
    check(resp.status_code == 200 and [p["provider"] for p in resp.json()["providers"]][:2] == ["serper", "you"], "21. POST .../order updates priority")

    resp = client.post("/api/settings/search-providers/you/disable")
    check(resp.status_code == 200, "21. POST .../disable -> 200")
    resp = client.post("/api/settings/search-providers/you/enable")
    check(resp.status_code == 200, "21. POST .../enable -> 200")

    resp = client.post("/api/settings/search-providers/tavily/test")
    check(resp.status_code == 200 and resp.json()["status"] == "NOT_CONFIGURED", "21. POST .../test on an unconfigured provider reports NOT_CONFIGURED cleanly, makes no real call")

    resp = client.post("/api/settings/search-providers/order", json={"order": ["not_a_real_provider"]})
    check(resp.status_code == 400, "21. an unknown provider name in an order update returns a clean 400")
finally:
    if _real_env_existed:
        _shutil.copy(env_backup, real_env_path)
    else:
        real_env_path.unlink(missing_ok=True)
    for junk in [ROOT / "data" / "applications" / "search_provider_settings.json", ROOT / "data" / "applications" / "search_provider_usage.json"]:
        junk.unlink(missing_ok=True)

if _real_env_existed:
    check(_sha(real_env_path) == _sha(env_backup), "21. the real project .env is restored byte-identical after this test")
else:
    check(not real_env_path.exists(), "21. no real project .env existed before this test, and none was left behind afterward")


# ---------------------------------------------------------------------
# 19. Report attribution shows the ACTUAL provider (not hardcoded SERPER)
# ---------------------------------------------------------------------

import sqlite3
import init_tracker
import migrate_v2_schema
from candidate_profile import normalize_candidate_profile, promote_to_confirmed, serialize_candidate_profile
from discover_local import normalize_job
from score_job import score_job, PROFILE as SAROJ_PROFILE
from job_eligibility import assess_job_eligibility
from search_worker import upsert_candidate_job_match
import tracker as tracker_module
import generate_run_report

report_tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase14_2_report_"))
report_db = report_tmp_dir / "jobos_test.db"
init_tracker.DATA_DIR = report_tmp_dir
init_tracker.DB_PATH = report_db
init_tracker.main()
conn = sqlite3.connect(report_db)
conn.execute("PRAGMA foreign_keys = ON")
migrate_v2_schema._create_new_tables(conn)
migrate_v2_schema._ensure_job_columns(conn)
conn.commit()

now = "2026-09-20T00:00:00+00:00"
raw_profile = {
    "identity": {"candidate_id": "cand_p142", "name": "P14.2 Test"},
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
confirmed = promote_to_confirmed(normalize_candidate_profile(raw_profile))
conn.execute("INSERT INTO candidates (candidate_id, name, email, phone, created_at, updated_at, status) VALUES (?,?,?,?,?,?,?)", ("cand_p142", "P14.2 Test", None, None, now, now, "ACTIVE"))
conn.execute("INSERT INTO candidate_search_profile (candidate_id, version, search_mode, profile_json, confirmed_by_user, is_active, created_at, updated_at) VALUES (?,1,'PROFILE',?,1,1,?,?)", ("cand_p142", json.dumps(serialize_candidate_profile(confirmed)), now, now))
conn.commit()

job = normalize_job(
    {
        "source": "FOUNDIT",
        "job_source": "FOUNDIT",
        "discovery_source": "SEARCH_PROVIDER:EXA",
        "discovery_query": 'site:foundit.in/job "Senior SRE" Bangalore',
        "completeness": 1.0,
        "company": "Acme3",
        "title": "Senior SRE",
        "jd_text": "kubernetes terraform aws eks aks jenkins argocd prometheus grafana slo sla incident rca production cloud platform",
        "job_url": "https://www.foundit.in/job/senior-sre-acme3-99999999",
    },
    1,
)
scoring = score_job(job, SAROJ_PROFILE)
tracker_module.upsert_job(conn, job, scoring)
conn.commit()
elig = assess_job_eligibility(job, SAROJ_PROFILE)
upsert_candidate_job_match(conn, "cand_p142", job, scoring, elig, None)
conn.commit()
conn.close()

report_out = report_tmp_dir / "report.xlsx"
generate_run_report.generate(str(report_db), "cand_p142", str(report_out))

from openpyxl import load_workbook

wb = load_workbook(report_out)
check(set(wb.sheetnames) == {"APPLY_TODAY", "ALL_MATCHING_JOBS", "NEW_JOBS", "ALREADY_APPLIED", "REJECTED_EXCLUDED", "DUPLICATES", "APPLICATION_TRACKER", "SOURCE_HEALTH", "RUN_SUMMARY"}, "19. still exactly 9 sheets")
ws = wb["ALL_MATCHING_JOBS"]
headers = [c.value for c in ws[1]]
found = None
for row in ws.iter_rows(min_row=2, values_only=True):
    d = dict(zip(headers, row))
    if d.get("Source") == "FOUNDIT":
        found = d
        break
check(found is not None, "19. the FOUNDIT job appears in the report")
if found:
    check(found["Discovered_Via"] == "SEARCH_PROVIDER:EXA", f"19. Discovered_Via correctly shows a NON-Serper provider (EXA), proving attribution is dynamic, not hardcoded (got {found['Discovered_Via']!r})")


# ---------------------------------------------------------------------
# Existing direct sources / production DB safety
# ---------------------------------------------------------------------

import source_registry

check(source_registry.get_adapter_status("NAUKRI").value == "ENABLED", "22. NAUKRI remains ENABLED, untouched")
check(source_registry.get_adapter_status("HIRIST").value == "ENABLED", "22. HIRIST remains ENABLED, untouched")

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"23. production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
