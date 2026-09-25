#!/usr/bin/env python3

"""
Phase 14.2 (capability-UI fix) -- board-level capability aggregation,
the new final_status priority vocabulary, and the GUI/API wiring that
actually uses it (the bug this phase fixed: source_capabilities.py had
the right data but web/app.js was still rendering the stale, pre-
Phase-14 `reason` text). Fully offline: no real network call, no real
provider key.

A placeholder SERPER_API_KEY is set BEFORE any project-module import
(the same convention test_phase14_search_provider.py/test_phase14_2_
multi_provider.py already use) so the search-provider adapters' own
AdapterStatus -- fixed once at first import, like every adapter's
status in this codebase -- reads ENABLED for this whole process. The
"no provider configured" board-level LOGIC is still fully exercised,
via direct calls that force active_provider=None (source_capabilities.
py's _search_provider_status()/_compute_final_status() both accept an
explicit override precisely so this is testable without relying on
real env-var timing) -- this is a cleaner, timing-independent unit
test of the branching logic than toggling os.environ mid-process ever
could be, and it is corroborated by test_phase14_2_multi_provider.py's
own "no providers configured" checks, which run in a genuinely
unconfigured process.
"""

import json
import os

# Block search_provider.py's own root-cause .env-loading fix (it loads
# real *_API_KEY values from THIS project's actual .env the moment
# search_provider/search_provider_adapter is first imported below) from
# seeding YOU/TAVILY/EXA/BRAVE with a real key -- pre-set to "" so its
# setdefault() sees them as already-present and skips them; an empty
# string is correctly treated as "not configured" everywhere
# (search_provider.get_provider_api_key() strips and checks truthiness).
# Serper alone is meant to be "configured" for this file's fixture.
for _k in ("YOU_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY"):
    os.environ.setdefault(_k, "")
os.environ["SERPER_API_KEY"] = "test-fixture-key-do-not-use"

import sys
import tempfile
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
API_DIR = ROOT / "api"
WEB_DIR = ROOT / "web"
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


import source_capabilities as sc
import source_registry


RESTRICTED_BOARDS = ["LINKEDIN", "INDEED", "FOUNDIT", "INSTAHYRE", "CUTSHORT", "WELLFOUND", "SHINE"]
DIRECT_BOARDS = ["NAUKRI", "HIRIST", "IIMJOBS", "APNA"]
INTERNAL_SEARCH_REGISTRY_KEYS = {f"{b}_SEARCH" for b in RESTRICTED_BOARDS}


# ---------------------------------------------------------------------
# Board-level capability aggregation: exactly one row per board, no
# *_SEARCH rows leak into the GUI-facing listing. (Note: the real,
# pre-existing "WEB_SEARCH" source is NOT one of these internal keys --
# checked against INTERNAL_SEARCH_REGISTRY_KEYS explicitly, not a naive
# endswith("_SEARCH"), which would false-positive on it.)
# ---------------------------------------------------------------------

caps = {c["source_name"]: c for c in sc.list_source_capabilities()}
check(all(b in caps for b in RESTRICTED_BOARDS + DIRECT_BOARDS), "board-level: every one of the 11 boards has exactly one capability record")
check(not (set(caps) & INTERNAL_SEARCH_REGISTRY_KEYS), f"board-level: no internal *_SEARCH registry key ever appears in the GUI-facing listing (found: {set(caps) & INTERNAL_SEARCH_REGISTRY_KEYS})")
check("WEB_SEARCH" in caps, "board-level: the real, pre-existing WEB_SEARCH source is untouched and still listed (distinct from the new internal *_SEARCH keys)")
check(len(caps) == len(set(caps)), "board-level: no duplicate rows for any source")

for board in RESTRICTED_BOARDS:
    check(all(k in caps[board] for k in ("direct_status", "search_provider_status", "search_provider", "final_status")), f"board-level: {board}'s record carries direct_status/search_provider_status/search_provider/final_status")


# ---------------------------------------------------------------------
# Board-level priority logic, tested directly (timing-independent):
# no provider eligible -> SEARCH_PROVIDER_NOT_CONFIGURED; direct_status
# NEVER claims ENABLED for a restricted board.
# ---------------------------------------------------------------------

for board in RESTRICTED_BOARDS:
    status = sc._search_provider_status(board, active_provider=None)
    final = sc._compute_final_status(board, enabled=False, active_provider=None)
    direct = sc._direct_status(board, enabled=False, metadata=sc._METADATA.get(board, sc._DEFAULT_METADATA))
    check(direct != "ENABLED", f"unconfigured: {board}'s direct_status is never ENABLED (got {direct})")
    check(direct == "NOT_AUTHORIZED", f"unconfigured: {board}'s direct_status is NOT_AUTHORIZED, matching the real robots.txt/anti-bot evidence (got {direct})")
    check(status == "NOT_CONFIGURED", f"unconfigured: {board}'s search_provider_status is NOT_CONFIGURED with no eligible provider (got {status})")
    check(final == "SEARCH_PROVIDER_NOT_CONFIGURED", f"restricted board remains SEARCH_PROVIDER_NOT_CONFIGURED with no provider (got {final} for {board})")
    check(board in sc._MANUAL_IMPORT_CAPABLE, f"unconfigured: {board} still offers manual import as a fallback")

for board in DIRECT_BOARDS:
    cap = sc.get_source_capability(board)
    check(cap["final_status"] == "ENABLED" and cap["direct_status"] == "ENABLED", f"direct sources remain ENABLED, untouched by this phase (got {cap['final_status']}/{cap['direct_status']} for {board})")


# ---------------------------------------------------------------------
# Configured pool (this file's placeholder SERPER_API_KEY, set before
# any import): every restricted board -> AVAILABLE_VIA_SEARCH_PROVIDER,
# search_provider names the real provider, board joins the "enabled"
# GUI bucket, its direct_status is STILL never claimed ENABLED.
# ---------------------------------------------------------------------

for board in RESTRICTED_BOARDS:
    cap = sc.get_source_capability(board)
    check(cap["final_status"] == "AVAILABLE_VIA_SEARCH_PROVIDER", f"restricted board becomes AVAILABLE_VIA_SEARCH_PROVIDER once a provider is configured (got {cap['final_status']} for {board})")
    check(cap["search_provider_status"] == "AVAILABLE", f"configured: {board}'s search_provider_status is AVAILABLE (got {cap['search_provider_status']})")
    check(cap["search_provider"] == "SERPER", f"configured: {board}'s search_provider names the actual provider uppercase (got {cap['search_provider']})")
    check(cap["direct_status"] == "NOT_AUTHORIZED", f"configured: {board}'s direct_status is STILL never claimed ENABLED just because a search provider is available (got {cap['direct_status']})")

enabled_names = {c["source_name"] for c in sc.list_enabled_sources()}
check(set(RESTRICTED_BOARDS) <= enabled_names, f"configured: all seven restricted boards now appear in the GUI 'enabled/available' bucket (got {enabled_names})")
unavailable_names = {c["source_name"] for c in sc.list_unavailable_sources()}
check(not (set(RESTRICTED_BOARDS) & unavailable_names), f"configured: none of the seven restricted boards remain in the 'unavailable' bucket once a provider is configured (found: {set(RESTRICTED_BOARDS) & unavailable_names})")
check(not (unavailable_names & INTERNAL_SEARCH_REGISTRY_KEYS), "configured: no internal *_SEARCH key appears in the unavailable bucket either")


# ---------------------------------------------------------------------
# Indeed's known caveat is preserved (not silently dropped by the new
# vocabulary) without corrupting the primary status fields.
# ---------------------------------------------------------------------

indeed_cap = sc.get_source_capability("INDEED")
check(indeed_cap["search_provider_quality_note"] is not None, "Indeed's company-parsing caveat is present as a separate note")
check(indeed_cap["final_status"] == "AVAILABLE_VIA_SEARCH_PROVIDER", "Indeed's final_status is not degraded by its own quality note -- the note is additive, never a status downgrade")


# ---------------------------------------------------------------------
# app.js's sourceStatusLine()/renderSourceStatus() actually use
# final_status as the primary label, not the raw `reason` text -- a
# static-source regression guard for the exact bug this phase fixed
# (the backend had the data, the frontend was ignoring it).
# ---------------------------------------------------------------------

app_js_text = (WEB_DIR / "app.js").read_text(encoding="utf-8")
check("s.final_status" in app_js_text, "app.js: renderSourceStatus()/sourceStatusLine() branches on final_status")
check("Automated discovery not authorized" not in app_js_text and "Automated access not authorized" not in app_js_text, "app.js: the stale pre-Phase-14 hardcoded wording is gone")
check("AVAILABLE_VIA_SEARCH_PROVIDER" in app_js_text, "app.js: recognizes the new AVAILABLE_VIA_SEARCH_PROVIDER status")
check("Search provider not configured" in app_js_text, "app.js: shows the new, accurate 'Search provider not configured' wording")
check("function shortReason" not in app_js_text, "app.js: the old reason-code-based shortReason() function was removed, not left dangling unused")

search_new_text = (WEB_DIR / "search_new.html").read_text(encoding="utf-8")
check("_SEARCH" in search_new_text and "sources.enabled" in search_new_text, "search_new.html: builds its checkbox list from board-level sources.enabled, computing the *_SEARCH registry key only as the submitted VALUE, never the visible label text")


# ---------------------------------------------------------------------
# GUI/API: /api/sources and /api/search-providers, end-to-end via
# FastAPI TestClient against a temp dev DB (this process's placeholder
# SERPER_API_KEY, set at the top of this file, makes LINKEDIN_SEARCH
# etc. genuinely ENABLED here). Checkbox->submission routing (Part 12):
# a board checked in the GUI submits the correct internal registry key,
# and the backend accepts it.
# ---------------------------------------------------------------------

tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase14_2_cap_"))
tmp_db = tmp_dir / "jobos_test.db"

import init_dev_db

init_dev_db.init_dev_db(tmp_db)

import db as db_mod

db_mod.DEV_DB = tmp_db

from fastapi.testclient import TestClient

# api.main now loads the real project .env at import time (Phase 14.3
# -- see its own module docstring note). This test's assertions
# assume a controlled environment (only its own placeholder
# SERPER_API_KEY), so the .env loader is neutralized for THIS
# specific import via a monkeypatch returning {} -- never touches the
# real .env file itself, and is restored immediately after import.
import env_config

_original_read_env_file = env_config.read_env_file
env_config.read_env_file = lambda *a, **kw: {}
try:
    import main as api_main
finally:
    env_config.read_env_file = _original_read_env_file

api_main.db_mod.DEV_DB = tmp_db
client = TestClient(api_main.app)

resp = client.get("/api/sources")
check(resp.status_code == 200, "GET /api/sources -> 200")
body = resp.json()
check({c["source_name"] for c in body["enabled"]} >= set(DIRECT_BOARDS) | set(RESTRICTED_BOARDS), f"GUI source response: with a configured provider, all direct + restricted boards appear enabled (got {[c['source_name'] for c in body['enabled']]})")
linkedin_row = next(c for c in body["enabled"] if c["source_name"] == "LINKEDIN")
check(linkedin_row["final_status"] == "AVAILABLE_VIA_SEARCH_PROVIDER" and linkedin_row["search_provider"] == "SERPER", f"GUI source response: LINKEDIN's row matches Part 16's exact API example shape (got final_status={linkedin_row['final_status']!r}, search_provider={linkedin_row['search_provider']!r})")
all_names = {c["source_name"] for c in body["enabled"] + body["unavailable"]}
check(not (all_names & INTERNAL_SEARCH_REGISTRY_KEYS), f"GUI source response: no LINKEDIN_SEARCH/INDEED_SEARCH/... row ever appears (found: {all_names & INTERNAL_SEARCH_REGISTRY_KEYS})")

resp = client.get("/api/search-providers")
check(resp.status_code == 200 and len(resp.json()["providers"]) == 5, f"GET /api/search-providers (Part 6's exact alias path) -> 200 with 5 providers (got {resp.status_code}, {len(resp.json().get('providers', []))})")

resp = client.post("/api/search-providers/serper/disable")
check(resp.status_code == 200, "POST /api/search-providers/{provider}/disable (Part 6's exact alias path) -> 200")
resp = client.post("/api/search-providers/serper/enable")
check(resp.status_code == 200, "POST /api/search-providers/{provider}/enable (Part 6's exact alias path) -> 200")
resp = client.post("/api/search-providers/you/test")
check(resp.status_code == 200 and resp.json()["status"] == "NOT_CONFIGURED", "POST /api/search-providers/{provider}/test (Part 6's exact alias path) on an unconfigured provider -> 200, NOT_CONFIGURED, no real call")

# Board-selection -> registry-key-submission end-to-end (Part 12): a
# candidate creates a search naming the internal *_SEARCH key exactly
# as search_new.html's fixed checkbox code now submits it.
resp = client.post("/api/candidates", json={"name": "Cap UI Test"})
candidate_id = resp.json()["candidate_id"]
resp = client.post(
    f"/api/candidates/{candidate_id}/searches",
    json={"name": "cap-ui-test", "target_roles": ["Senior SRE"], "target_locations": ["Bangalore"], "sources": ["LINKEDIN_SEARCH", "NAUKRI"]},
)
check(resp.status_code == 200 and resp.json().get("sources") == ["LINKEDIN_SEARCH", "NAUKRI"], f"end-to-end: a saved search naming the board's search-provider registry key is accepted (got {resp.status_code}: {resp.text[:200]})")

# Cleanup: this created a real saved-search row in the temp dev DB only
# (never production, never the real jobos_dev.db) -- nothing further
# needed, the whole temp_dir is disposable.

for junk in [ROOT / "data" / "applications" / "search_provider_settings.json", ROOT / "data" / "applications" / "search_provider_usage.json"]:
    junk.unlink(missing_ok=True)

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
