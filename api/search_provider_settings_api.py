"""
Phase 14.2 API-layer logic for Settings -> Search Providers. Thin
wrapper over the scripts/ modules that actually implement provider
config/usage/masking -- no second implementation of any of that here,
mirroring how search_store.py/profile_store.py wrap scripts/ logic for
every other page.

NAMING NOTE: this file is deliberately named search_provider_settings_
API_ (not "..._store") to avoid colliding, as a Python module name,
with scripts/search_provider_settings_store.py -- both scripts/ and
api/ are on sys.path, and two same-named modules would silently shadow
each other depending on import order (a real bug hit once during this
phase's own testing: api/main.py's `import search_provider_settings_
store` resolved to the scripts/ module, not this one, because
scripts/search_provider_manager.py had already imported the scripts/
one first under that exact name).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import env_config
import search_provider as sp
import search_provider_manager as spm
import search_provider_settings_store as settings_store  # scripts/ module -- order/enabled, non-secret
import search_provider_usage_store as usage_store


class SearchProviderSettingsError(ValueError):
    pass


def list_provider_settings():
    """One row per provider (Part 3): name, display name, whether a
    key is configured (never the key itself), masked key, enabled,
    priority position, current status + usage from the local advisory
    store, and the ADVISORY-labeled free-tier note (Part 7) -- never
    used by any runtime logic, display only."""
    settings = settings_store.load_settings()
    order = settings["order"]
    disabled = set(settings["disabled"])

    rows = []
    for position, name in enumerate(order):
        # Phase 14.3: env_key here is the env var ACTUALLY in use right
        # now (get_active_env_key_name() -- e.g. "YDC_API_KEY" if a
        # user set that instead of the GUI's own save target), so
        # masked_key always reflects whichever variable is really set.
        # The Save action itself still writes the canonical
        # PROVIDER_ENV_KEYS[name] ("YOU_API_KEY") -- no GUI migration.
        env_key = sp.get_active_env_key_name(name)
        configured = sp.is_provider_configured(name)
        masked = env_config.get_masked(env_key) if configured else None
        usage = usage_store.get_provider_usage(name)

        rows.append(
            {
                "provider": name,
                "display_name": {"you": "You.com", "tavily": "Tavily", "exa": "Exa", "brave": "Brave", "serper": "Serper"}[name],
                "env_key": env_key,
                "configured": configured,
                "masked_key": masked,
                "enabled": name not in disabled,
                "priority": position,
                "status": usage["current_status"] if configured else "NOT_CONFIGURED",
                "usage": {
                    "request_count_daily": usage["request_count_daily"],
                    "successful_count_daily": usage["successful_count_daily"],
                    "failure_count_daily": usage["failure_count_daily"],
                    "quota_failures_daily": usage["quota_failures_daily"],
                    "request_count_monthly": usage["request_count_monthly"],
                    "successful_count_monthly": usage["successful_count_monthly"],
                    "failure_count_monthly": usage["failure_count_monthly"],
                    "quota_failures_monthly": usage["quota_failures_monthly"],
                },
                "last_request_at": usage["last_request_at"],
                "last_success_at": usage["last_success_at"],
                "last_error_at": usage["last_error_at"],
                "last_error": usage["last_error"],
                "advisory_free_tier": sp.PROVIDER_ADVISORY_FREE_TIER.get(name),
            }
        )
    return rows


def set_order(order):
    unknown = [p for p in order if p not in sp.PROVIDER_ENV_KEYS]
    if unknown:
        raise SearchProviderSettingsError(f"Unknown provider(s): {unknown}. Known: {sorted(sp.PROVIDER_ENV_KEYS)}")
    settings_store.save_settings(order=order)
    return list_provider_settings()


def set_enabled(provider, enabled):
    if provider not in sp.PROVIDER_ENV_KEYS:
        raise SearchProviderSettingsError(f"Unknown provider: {provider!r}")
    settings_store.set_provider_enabled(provider, enabled)
    return list_provider_settings()


def set_key(provider, api_key):
    if provider not in sp.PROVIDER_ENV_KEYS:
        raise SearchProviderSettingsError(f"Unknown provider: {provider!r}")
    if not api_key or not api_key.strip():
        raise SearchProviderSettingsError("api_key must not be empty.")
    env_key = sp.PROVIDER_ENV_KEYS[provider]
    env_config.write_env_key(env_key, api_key.strip())
    # Never return the key itself -- only a masked confirmation. The
    # underlying AdapterStatus (search_provider_adapter.py's *_SEARCH
    # classes) was fixed at process start, per this project's existing
    # convention for every adapter -- callers see a restart_required
    # note so the GUI can say so honestly.
    return {
        "provider": provider,
        "masked_key": env_config.get_masked(env_key),
        "stored_in": ".env",
        "restart_required_for_full_activation": True,
    }


def remove_key(provider):
    if provider not in sp.PROVIDER_ENV_KEYS:
        raise SearchProviderSettingsError(f"Unknown provider: {provider!r}")
    env_config.remove_env_key(sp.PROVIDER_ENV_KEYS[provider])
    return {"provider": provider, "masked_key": None}


def test_provider(provider):
    """A REAL, single, minimal live call (Part 3's [Test] button) --
    only ever reachable if a key is actually configured. Never called
    automatically; always a direct user action. Classifies and records
    the outcome via the same usage_store every real search does."""
    if provider not in sp.PROVIDER_ENV_KEYS:
        raise SearchProviderSettingsError(f"Unknown provider: {provider!r}")
    if not sp.is_provider_configured(provider):
        return {"provider": provider, "status": "NOT_CONFIGURED", "detail": "No API key configured for this provider."}

    try:
        instance = sp.construct_provider(provider)
        results = instance.search("site reliability engineer test connection", num=1, recency=None)
        usage_store.record_request(provider, "success")
        return {"provider": provider, "status": "AVAILABLE", "detail": f"Connection OK ({len(results)} result(s) returned)."}
    except sp.ProviderSearchError as error:
        usage_store.record_request(provider, "failure", error_type=error.error_type.value, error_detail=error.detail)
        return {"provider": provider, "status": error.error_type.value, "detail": error.detail}
    except Exception as error:  # noqa: BLE001 -- a Test-connection button must never 500, always report a status
        usage_store.record_request(provider, "failure", error_type="UNKNOWN", error_detail=str(error))
        return {"provider": provider, "status": "ERROR", "detail": str(error)}


def reset_usage(provider, window=None):
    if provider not in sp.PROVIDER_ENV_KEYS:
        raise SearchProviderSettingsError(f"Unknown provider: {provider!r}")
    if window not in (None, "daily", "monthly"):
        raise SearchProviderSettingsError("window must be 'daily', 'monthly', or omitted (resets both).")
    usage_store.reset_provider_usage(provider, window=window)
    return list_provider_settings()
