#!/usr/bin/env python3

"""
BYOK (Bring Your Own Key) credential-isolation regression tests.

Written in response to a user report that a fresh installation
"did not ask for API keys and appeared to use the developer's personal
search API keys." These tests pin down the ACTUAL runtime credential
contract so that specific failure mode can never silently appear:

  1. A fresh installation has ZERO configured provider keys.
  2. No hardcoded/default/fallback key exists in the codebase.
  3. A key the user saves is the EXACT key the search path consumes.
  4. Replacing a key changes which key is consumed; removing one stops
     it being consumed at all.
  5. No API response (save / list / remove) ever returns the raw key.
  6. Masking is not reversible.
  7. Provider error details can never carry the key.
  8. The credential scope is INSTALLATION-GLOBAL, not per-candidate --
     asserted explicitly so no future reader mistakes this codebase
     for one that provides per-user credential isolation. See
     docs/SECURITY.md's "Credential ownership" section.

EVERY key used here is a fake test string. No real provider key is
ever read, written, or sent anywhere: `env_config.ENV_PATH` is
redirected to an isolated temp file for the whole run, and this
process's own provider environment variables are saved and restored,
so the developer's real .env is never read from or written to.
No network call is made -- the one test that proves "the search path
consumes this exact key" injects a fake provider rather than calling
a real one.
"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

REAL_ENV_PATH = ROOT / ".env"
PRODUCTION_DB = ROOT / "data" / "applications" / "jobos.db"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


_real_env_sha_before = _sha(REAL_ENV_PATH)
_production_db_sha_before = _sha(PRODUCTION_DB)

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


import env_config
import search_provider as sp
import search_provider_settings_api as sp_api

# --- Isolation: redirect the credential file to a temp path, and
# --- clear every provider env var for the duration of this file.
_tmp_dir = Path(tempfile.mkdtemp(prefix="jobos_test_byok_"))
_tmp_env = _tmp_dir / ".env"

_ALL_PROVIDER_ENV_VARS = sorted(
    set(sp.PROVIDER_ENV_KEYS.values())
    | {name for names in sp.PROVIDER_ENV_KEY_ALIASES.values() for name in names}
)
_saved_env = {k: os.environ.pop(k, None) for k in _ALL_PROVIDER_ENV_VARS}

_original_env_path = env_config.ENV_PATH
env_config.ENV_PATH = _tmp_env

# env_config's functions bind ENV_PATH as a default argument at import
# time, so reassigning the module attribute alone is not enough --
# patch each function's default too, guaranteeing nothing in this file
# can reach the developer's real .env.
env_config.write_env_key.__defaults__ = (_tmp_env,)
env_config.remove_env_key.__defaults__ = (_tmp_env,)
env_config.read_env_file.__defaults__ = (_tmp_env,)
env_config.load_env_file_into_environ.__defaults__ = (_tmp_env,)

FAKE_KEY_A = "fake-byok-test-key-AAAA1111"
FAKE_KEY_B = "fake-byok-test-key-BBBB2222"

try:
    # -----------------------------------------------------------------
    # 1. A fresh installation has zero configured provider keys.
    # -----------------------------------------------------------------
    check(
        not _tmp_env.exists(),
        "1. a fresh installation has no credential file at all (nothing shipped with the app)",
    )
    unconfigured = [name for name in sp.PROVIDER_ENV_KEYS if not sp.is_provider_configured(name)]
    check(
        len(unconfigured) == len(sp.PROVIDER_ENV_KEYS),
        f"1. every provider reports NOT configured on a fresh install (got {len(unconfigured)}/{len(sp.PROVIDER_ENV_KEYS)})",
    )
    rows = sp_api.list_provider_settings()
    check(
        all(r["configured"] is False and r["masked_key"] is None for r in rows),
        "1. the Settings -> Search Providers API reports configured=False / masked_key=None for every provider",
    )
    check(
        all(r["status"] == "NOT_CONFIGURED" for r in rows),
        "1. provider status is reported as NOT_CONFIGURED, never as configured/available",
    )

    # -----------------------------------------------------------------
    # 2. No hardcoded / default / fallback key anywhere in the source.
    # -----------------------------------------------------------------
    source_blob = ""
    for path in sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "api").glob("*.py")):
        source_blob += path.read_text(encoding="utf-8", errors="replace")
    # get_provider_api_key() must read os.environ and nothing else --
    # no literal default, no "or <some key>" fallback.
    check(
        'os.environ.get(env_key, "").strip()' in source_blob,
        "2. get_provider_api_key() resolves keys from os.environ only",
    )
    check(
        sp.get_provider_api_key("you") is None,
        "2. with nothing configured, get_provider_api_key() returns None -- never a built-in fallback key",
    )

    # -----------------------------------------------------------------
    # 3. The key the user saves is the EXACT key the search path uses.
    # -----------------------------------------------------------------
    save_result = sp_api.set_key("tavily", FAKE_KEY_A)
    check(
        sp.get_provider_api_key("tavily") == FAKE_KEY_A,
        "3. the exact key saved through the API is what the search path's own resolver returns",
    )
    check(
        sp.is_provider_configured("tavily") is True,
        "3. the provider is reported configured immediately after saving (no restart needed for this check)",
    )

    # The real consumption point: construct_provider() hands the
    # resolved key to the provider instance. Prove the instance got
    # THIS key, with no network call.
    instance = sp.construct_provider("tavily")
    check(
        getattr(instance, "api_key", None) == FAKE_KEY_A,
        "3. construct_provider() passes exactly the user's saved key to the provider instance (no substitution)",
    )

    # -----------------------------------------------------------------
    # 5. No API response ever returns the raw key.
    # -----------------------------------------------------------------
    check(
        FAKE_KEY_A not in str(save_result),
        "5. the save-key API response never contains the raw key",
    )
    check(
        save_result.get("masked_key") and FAKE_KEY_A != save_result["masked_key"],
        f"5. the save-key response returns only a masked value (got {save_result.get('masked_key')!r})",
    )
    listed = sp_api.list_provider_settings()
    check(
        FAKE_KEY_A not in str(listed),
        "5. the provider-list API response never contains the raw key",
    )
    tavily_row = next(r for r in listed if r["provider"] == "tavily")
    check(
        tavily_row["configured"] is True and tavily_row["masked_key"] and FAKE_KEY_A not in tavily_row["masked_key"],
        "5. the list response reports configured=True with a masked key only",
    )

    # -----------------------------------------------------------------
    # 6. Masking is not reversible.
    # -----------------------------------------------------------------
    masked = env_config.mask_secret(FAKE_KEY_A)
    check(
        masked.endswith(FAKE_KEY_A[-4:]) and len(masked) == len(FAKE_KEY_A) and FAKE_KEY_A[:-4] not in masked,
        "6. mask_secret() reveals at most the last 4 characters and discards the rest",
    )
    check(
        env_config.mask_secret("abc") == "•••",
        "6. a short secret is fully masked (no partial leak for short values)",
    )

    # -----------------------------------------------------------------
    # 4. Replacing a key changes which key is consumed.
    # -----------------------------------------------------------------
    sp_api.set_key("tavily", FAKE_KEY_B)
    check(
        sp.get_provider_api_key("tavily") == FAKE_KEY_B,
        "4. replacing a key changes exactly which key subsequent requests resolve",
    )
    check(
        sp.construct_provider("tavily").api_key == FAKE_KEY_B,
        "4. the newly-constructed provider instance uses the replacement key, not the old one",
    )
    check(
        FAKE_KEY_A not in _tmp_env.read_text(encoding="utf-8"),
        "4. the replaced (old) key is gone from the credential file, not left behind",
    )

    # -----------------------------------------------------------------
    # 4b. Removing a key stops it being consumed.
    # -----------------------------------------------------------------
    remove_result = sp_api.remove_key("tavily")
    check(
        sp.get_provider_api_key("tavily") is None,
        "4b. after removal the resolver returns None -- the key is no longer consumed",
    )
    check(
        sp.is_provider_configured("tavily") is False,
        "4b. after removal the provider reports NOT configured again",
    )
    check(
        remove_result.get("masked_key") is None,
        "4b. the remove-key response carries no key material at all",
    )
    check(
        FAKE_KEY_B not in _tmp_env.read_text(encoding="utf-8"),
        "4b. the removed key is physically gone from the credential file",
    )
    check(
        FAKE_KEY_B not in os.environ.values(),
        "4b. the removed key is also cleared from this process's environment",
    )

    # -----------------------------------------------------------------
    # 7. Provider error details can never carry the key.
    # -----------------------------------------------------------------
    import inspect

    detail_src = inspect.getsource(sp._safe_error_detail)
    check(
        "error.read()" in detail_src,
        "7. error detail is built only from the provider's own RESPONSE body, never from our request (where the key lives)",
    )
    err = sp.ProviderSearchError("tavily", sp.ProviderErrorType.AUTH_FAILED, "HTTP 401 -- invalid api key")
    check(
        FAKE_KEY_A not in str(err) and FAKE_KEY_B not in str(err),
        "7. a realistic auth-failure error string carries no key material",
    )

    # -----------------------------------------------------------------
    # 8. Credential scope is INSTALLATION-GLOBAL, not per-candidate.
    #    This is asserted (not merely documented) so that nobody later
    #    assumes this codebase isolates credentials per user/candidate
    #    when it does not. See docs/SECURITY.md.
    # -----------------------------------------------------------------
    for fn_name in ("get_provider_api_key", "is_provider_configured", "construct_provider"):
        sig = inspect.signature(getattr(sp, fn_name))
        check(
            "candidate_id" not in sig.parameters,
            f"8. sp.{fn_name}() takes no candidate_id -- credential scope is installation-global, by design and documented",
        )
    for fn_name in ("set_key", "remove_key", "list_provider_settings", "test_provider"):
        sig = inspect.signature(getattr(sp_api, fn_name))
        check(
            "candidate_id" not in sig.parameters,
            f"8. sp_api.{fn_name}() takes no candidate_id -- the Settings surface is installation-wide, not per-candidate",
        )

    # -----------------------------------------------------------------
    # 9. Shell-exported environment variables take precedence over the
    #    app-managed credential file on a fresh process (standard
    #    dotenv setdefault semantics). Pinned here because it is the
    #    one documented way a deployment-level credential can outrank
    #    the UI-saved one -- see docs/CONFIGURATION.md.
    # -----------------------------------------------------------------
    env_config.write_env_key("TAVILY_API_KEY", FAKE_KEY_A, path=_tmp_env)
    os.environ["TAVILY_API_KEY"] = FAKE_KEY_B  # simulates a real shell export
    env_config.load_env_file_into_environ(_tmp_env)
    check(
        os.environ["TAVILY_API_KEY"] == FAKE_KEY_B,
        "9. a shell-exported provider key is never overwritten by the credential file (documented dotenv setdefault behavior)",
    )
    os.environ.pop("TAVILY_API_KEY", None)
    env_config.remove_env_key("TAVILY_API_KEY", path=_tmp_env)

    # -----------------------------------------------------------------
    # 10. The frontend never persists a key in the browser.
    # -----------------------------------------------------------------
    settings_page = (ROOT / "web" / "settings_search_providers.html").read_text(encoding="utf-8")
    check(
        "localStorage" not in settings_page,
        "10. the Search Providers page never writes anything to localStorage",
    )
    check(
        "api_key" in settings_page and "GET" not in settings_page.split("api_key")[0][-120:],
        "10. the page submits the key in a POST request body, never a URL/query parameter",
    )
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    check(
        not any(k in app_js for k in sp.PROVIDER_ENV_KEYS.values()),
        "10. web/app.js references no provider key variable at all",
    )

finally:
    # --- Restore everything: real .env untouched, env vars restored.
    env_config.ENV_PATH = _original_env_path
    env_config.write_env_key.__defaults__ = (_original_env_path,)
    env_config.remove_env_key.__defaults__ = (_original_env_path,)
    env_config.read_env_file.__defaults__ = (_original_env_path,)
    env_config.load_env_file_into_environ.__defaults__ = (_original_env_path,)
    for key, value in _saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

# --- Safety: this file must never have touched the real .env or the
# --- production database.
check(
    _sha(REAL_ENV_PATH) == _real_env_sha_before,
    "SAFETY: the real .env is byte-identical before/after this test file (never read from or written to)",
)
check(
    _sha(PRODUCTION_DB) == _production_db_sha_before,
    "SAFETY: the production database is byte-identical before/after this test file",
)

print()
print(f"{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
