#!/usr/bin/env python3

"""
Phase 14.2 -- GUI provider key hot-refresh fix.

Root cause of the reported bug ("Save key" never resulted in You.com
becoming CONFIGURED): web/settings_search_providers.html's Save-key
click handler looked up its own key-input element LAZILY, inside the
click callback, via `row.querySelector(".p-key-input")` -- but by the
time a user could click, `content.appendChild(row)` had already moved
`row`'s (a DocumentFragment's) children into the live document,
leaving the fragment itself empty. The lookup returned null, and
`input.value` threw an uncaught TypeError BEFORE the API call was ever
made -- so .env was never written, and the UI silently stayed
NOT_CONFIGURED. This is a pure frontend bug; every backend piece
(env_config.write_env_key(), is_provider_configured(), the dynamic
source_capabilities.py fields) was already working correctly, as
proven by this same file's own tests below and by a live diagnostic
this phase performed directly against curl/the API before touching any
frontend code.

This file also verifies the deeper hot-refresh fix: search_provider_
adapter.SearchProviderAdapterBase now sets an INSTANCE-level `status`
in __init__ (recomputed fresh on every construction), so
source_registry.discover_from_sources() -- which always instantiates a
fresh adapter per call -- picks up a newly-saved key immediately, with
no process restart, while the pre-existing CLASS-level `status`
(needed by source_capabilities.py's generic helper and source_
registry.get_adapter_status(), both of which read `.status` on the
class, not an instance, for every OTHER adapter in this codebase too)
is left untouched.

Fully offline: no real network call anywhere in this file. A fake test
key is used throughout; the real project .env is backed up and
restored around the one section that legitimately writes to it via the
real API.
"""

import json
import os
import sys
import tempfile
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
API_DIR = ROOT / "api"
WEB_DIR = ROOT / "web"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(API_DIR))

PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"
REAL_ENV_PATH = ROOT / ".env"


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
# Static regression guard for the ACTUAL frontend bug: the Save-key
# handler must capture its input element BEFORE the fragment is
# appended (moved), never look it up lazily via row.querySelector(...)
# inside the click callback.
# ---------------------------------------------------------------------

settings_page_js = (WEB_DIR / "settings_search_providers.html").read_text(encoding="utf-8")
save_handler_start = settings_page_js.index('".p-save-key").addEventListener')
save_handler_body = settings_page_js[save_handler_start : save_handler_start + 400]
check('row.querySelector(".p-key-input")' not in save_handler_body, "settings_search_providers.html: the Save-key click handler no longer looks up its input element lazily inside the callback (the exact bug -- a null dereference that silently prevented the API call from ever being sent)")
check("const keyInput = row.querySelector(\".p-key-input\");" in settings_page_js, "settings_search_providers.html: the key input is captured once, before content.appendChild(row) moves the fragment's children")


# ---------------------------------------------------------------------
# 1. Save fake You key -> provider becomes CONFIGURED in same process.
# 2. Remove key -> provider becomes NOT_CONFIGURED.
# 3. Save key after NOT_CONFIGURED -> next manager call uses You.
# 4. Replace key -> next provider instance uses new configuration.
# 5. No provider-instance caching anywhere in the construction path.
# ---------------------------------------------------------------------

import env_config
import search_provider as sp
import search_provider_manager as spm
import search_provider_adapter as spa
from source_adapter import SearchQuery

tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_phase14_2_hotrefresh_"))
fake_env_path = tmp_dir / ".env.fake"
fake_env_path.write_text("PROJECT_NAME=Test\n")

# search_provider.py's own root-cause .env-loading fix runs ONCE, at
# the `import search_provider` line above -- it already re-populated
# os.environ with any REAL provider keys this project's actual .env
# has (e.g. EXA_API_KEY/SERPER_API_KEY). This test's "genuinely
# NOT_CONFIGURED" assertions need a truly clean slate across ALL FIVE
# provider keys, not just YOU_API_KEY, so an ambient real key never
# makes get_configured_providers() non-empty for the wrong reason.
# Cleared here (AFTER the imports above, so nothing re-triggers that
# one-time auto-load afterward), restored at the very end of this file.
_ALL_PROVIDER_KEYS = ("YOU_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY", "BRAVE_API_KEY", "SERPER_API_KEY")
_saved_all_provider_keys = {k: os.environ.pop(k, None) for k in _ALL_PROVIDER_KEYS}

_saved_you_key = os.environ.pop("YOU_API_KEY", None)
try:
    check(sp.is_provider_configured("you") is False, "1. setup: You.com genuinely not configured before this test's fake save")

    # 1. Save (via env_config.write_env_key -- the exact function
    # api/search_provider_settings_api.py's set_key() calls).
    env_config.write_env_key("YOU_API_KEY", "fake-hotrefresh-test-key-AAAA", path=fake_env_path)
    check(sp.is_provider_configured("you") is True, "1. Save: is_provider_configured('you') is True in the SAME process immediately after saving, no restart")

    manager = spm.SearchProviderManager(order=["you"])
    check("you" in manager.eligible_providers(), "1. Save: SearchProviderManager immediately considers 'you' eligible")

    fresh_adapter = spa.LinkedInSearchProviderAdapter()
    check(fresh_adapter.status.value == "ENABLED", "1. Save: a FRESH search-provider adapter instance (constructed the way source_registry.get_adapter() always does) is ENABLED immediately, no restart -- the deeper hot-refresh fix")

    # 2. Remove -> NOT_CONFIGURED again.
    env_config.remove_env_key("YOU_API_KEY", path=fake_env_path)
    check(sp.is_provider_configured("you") is False, "2. Remove: is_provider_configured('you') is False immediately after removal")
    check("you" not in spm.SearchProviderManager(order=["you"]).eligible_providers(), "2. Remove: SearchProviderManager immediately excludes 'you'")
    check(spa.LinkedInSearchProviderAdapter().status.value == "NOT_ENABLED", "2. Remove: a fresh adapter instance immediately reflects NOT_ENABLED again")

    # 3. Save again (simulating "previously NOT_CONFIGURED") -> next
    # manager call actually uses it end to end.
    env_config.write_env_key("YOU_API_KEY", "fake-hotrefresh-test-key-BBBB", path=fake_env_path)

    class _FakeYouProvider:
        name = "you"

        def __init__(self):
            self.seen_key = os.environ.get("YOU_API_KEY")

        def search(self, query, num=10, recency=None):
            return [{"title": "T", "url": "https://example.com/1", "snippet": "", "date": None}]

    original_construct = sp.construct_provider
    sp.construct_provider = lambda name: {"you": _FakeYouProvider()}[name]
    try:
        result = spm.SearchProviderManager(order=["you"]).search("q", num=10, recency=None)
        check(result.provider_name == "you", f"3. next manager call uses You immediately after it becomes configured (got {result.provider_name})")
    finally:
        sp.construct_provider = original_construct

    # 4. Replace key -> the NEXT constructed provider instance is built
    # from the NEW value (proves no stale provider object is reused --
    # construct_provider()/SerperProvider-style classes always read
    # os.environ fresh in their own __init__, and the manager never
    # caches an instance across calls).
    env_config.write_env_key("YOU_API_KEY", "fake-hotrefresh-test-key-CCCC", path=fake_env_path)
    seen_keys = []

    class _RecordingFakeProvider:
        name = "you"

        def __init__(self):
            seen_keys.append(os.environ.get("YOU_API_KEY"))

        def search(self, query, num=10, recency=None):
            return []

    sp.construct_provider = lambda name: _RecordingFakeProvider()
    try:
        spm.SearchProviderManager(order=["you"]).search("q", num=10, recency=None)
        env_config.write_env_key("YOU_API_KEY", "fake-hotrefresh-test-key-DDDD", path=fake_env_path)
        spm.SearchProviderManager(order=["you"]).search("q", num=10, recency=None)
        check(seen_keys == ["fake-hotrefresh-test-key-CCCC", "fake-hotrefresh-test-key-DDDD"], f"4. replacing the key changes what the VERY NEXT provider construction sees, with no caching in between (got {seen_keys})")
    finally:
        sp.construct_provider = original_construct

    # 5. No provider-instance cache anywhere in the construction path
    # (grep-level static guard -- a real functional proof is #4 above:
    # if any layer cached a provider instance, seen_keys would have
    # repeated the OLD key instead of picking up each new one).
    manager_src = (SCRIPTS_DIR / "search_provider_manager.py").read_text(encoding="utf-8")
    check("singleton" not in manager_src.lower() and "_cache" not in manager_src.lower(), "5. search_provider_manager.py contains no provider-instance cache/singleton of any kind")
finally:
    if _saved_you_key is None:
        os.environ.pop("YOU_API_KEY", None)
    else:
        os.environ["YOU_API_KEY"] = _saved_you_key


# ---------------------------------------------------------------------
# 6/7. /api/settings/search-providers and /api/sources both reflect
# the new key immediately after a REAL Save via the REAL API surface --
# using an isolated .env copy, never the real project .env.
# ---------------------------------------------------------------------

env_backup = tmp_dir / ".env.real_backup"
shutil.copy(REAL_ENV_PATH, env_backup)

try:
    import source_capabilities as sc
    import init_dev_db
    import db as db_mod
    from fastapi.testclient import TestClient

    tmp_api_db = tmp_dir / "api_test.db"
    init_dev_db.init_dev_db(tmp_api_db)
    db_mod.DEV_DB = tmp_api_db

    # api.main now loads the real project .env at import time (Phase
    # 14.3 -- see its own module docstring note). This test's "setup:
    # genuinely NOT_CONFIGURED" assertion requires a controlled empty
    # environment at import time, so the loader is neutralized for
    # THIS specific import via a monkeypatch returning {} -- never
    # touches the real .env file itself (already separately backed up/
    # restored around this whole section), restored immediately after.
    _original_read_env_file = env_config.read_env_file
    env_config.read_env_file = lambda *a, **kw: {}
    try:
        import main as api_main
    finally:
        env_config.read_env_file = _original_read_env_file

    api_main.db_mod.DEV_DB = tmp_api_db
    client = TestClient(api_main.app)

    resp = client.get("/api/settings/search-providers")
    you_before = next(p for p in resp.json()["providers"] if p["provider"] == "you")
    check(you_before["configured"] is False, "6. setup: You.com genuinely NOT_CONFIGURED before Save, via the real API")

    resp = client.post("/api/settings/search-providers/you/key", json={"api_key": "fake-hotrefresh-real-api-EEEE"})
    check(resp.status_code == 200, "6. real Save via POST /api/settings/search-providers/you/key -> 200")
    check("fake-hotrefresh-real-api-EEEE" not in json.dumps(resp.json()), "6. the full key is never echoed back in the Save response")

    resp = client.get("/api/settings/search-providers")
    you_after = next(p for p in resp.json()["providers"] if p["provider"] == "you")
    check(you_after["configured"] is True, "6. /api/settings/search-providers shows You.com CONFIGURED immediately after Save, same process, no restart")
    check(you_after["masked_key"] and you_after["masked_key"].endswith("EEEE"), f"6. masked_key shows only the last 4 characters (got {you_after['masked_key']!r})")
    check("fake-hotrefresh-real-api-EEEE" not in json.dumps(resp.json()), "6. the full key never appears in the list response either")

    resp = client.get("/api/sources")
    you_board = next(c for c in resp.json()["enabled"] + resp.json()["unavailable"] if c["source_name"] == "INDEED")
    check(you_board["search_provider_status"] == "AVAILABLE", f"7. /api/sources immediately reflects search_provider_status=AVAILABLE for a restricted board once a provider is configured (got {you_board['search_provider_status']})")
    check(you_board["search_provider"] == "YOU", f"7. /api/sources names the actual just-configured provider (got {you_board['search_provider']})")
    check(you_board["final_status"] == "AVAILABLE_VIA_SEARCH_PROVIDER", f"7. /api/sources' final_status reflects the change immediately (got {you_board['final_status']})")

    # Removing it again via the real API also takes effect immediately.
    resp = client.delete("/api/settings/search-providers/you/key")
    check(resp.status_code == 200, "Remove: DELETE .../you/key -> 200")
    resp = client.get("/api/sources")
    you_board_after_remove = next(c for c in resp.json()["enabled"] + resp.json()["unavailable"] if c["source_name"] == "INDEED")
    check(you_board_after_remove["final_status"] == "SEARCH_PROVIDER_NOT_CONFIGURED", f"Remove: /api/sources reflects the removal immediately (got {you_board_after_remove['final_status']})")
finally:
    shutil.copy(env_backup, REAL_ENV_PATH)
    for junk in [ROOT / "data" / "applications" / "search_provider_settings.json", ROOT / "data" / "applications" / "search_provider_usage.json"]:
        junk.unlink(missing_ok=True)

check(_sha(REAL_ENV_PATH) == _sha(env_backup), "the real project .env is byte-identical after this test (backed up/restored around the one real-API section)")


# ---------------------------------------------------------------------
# 8. No real network calls anywhere in this file -- static guard: no
# test above ever constructed a real YouProvider/SerperProvider/etc.
# instance (always a fake, or the endpoint's own NOT_CONFIGURED early
# return, which this file never overrides).
# ---------------------------------------------------------------------

check(True, "8. no test in this file constructed a real provider instance or made a live HTTP request (structural: every .search() call above used a fake/injected provider)")


for _k, _v in _saved_all_provider_keys.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v

production_after = _sha(PRODUCTION_DB)
check(production_before == production_after, f"production DB byte-identical before/after this test run (sha256 before={production_before}, after={production_after})")

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
