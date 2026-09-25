#!/usr/bin/env python3

"""
SearchProviderManager (Phase 14.2, Part 5) -- selects among the
configured search-provider pool and fails over between them. This is
the ONE place failover policy lives; search_provider_adapter.py calls
into this instead of constructing a provider directly (Phase 14's
prior direct SerperProvider construction is replaced by this, but
SerperProvider itself, and every other provider class, is unchanged --
see search_provider.py).

Failover policy (Part 5), exactly as specified:
  1. Try providers in priority order, skipping any without a
     configured key, explicitly disabled (Part 3's GUI), or over its
     configured local advisory budget (Part 6).
  2. A successful call (even one returning an EMPTY list) returns
     immediately -- empty results are NOT a failure and never trigger
     trying the next provider ("do not switch provider merely because
     results are empty").
  3. AUTH_FAILED / QUOTA_EXHAUSTED -> mark that provider's local status
     accordingly, move to the next provider.
  4. RATE_LIMITED that survived the provider's own internal retry loop
     (search_provider.py already respects Retry-After there) -> move
     to the next provider.
  5. TIMEOUT / NETWORK_ERROR / SERVER_ERROR that survived the
     provider's own retries -> move to the next provider.
  6. If every eligible provider fails -> raise
     SearchProviderPoolExhausted (never a fabricated empty/successful
     result).

Every outcome is recorded via search_provider_usage_store.py (Part 6),
which is a small local JSON file, never the production database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import search_provider as sp
import search_provider_settings_store as settings_store
import search_provider_usage_store as usage_store


class SearchProviderPoolExhausted(Exception):
    """Raised when every eligible provider in the pool failed (or none
    was eligible at all) -- the manager's caller (search_provider_
    adapter.py) converts this into the existing AdapterBlockedError
    vocabulary, exactly like a single-provider failure did in Phase
    14. Never triggers a fabricated result."""

    def __init__(self, attempts):
        self.attempts = attempts  # list of (provider_name, reason) tried
        detail = "; ".join(f"{name}: {reason}" for name, reason in attempts) or "no eligible provider configured"
        super().__init__(f"search provider pool exhausted -- {detail}")


def _max_daily():
    import os

    raw = os.environ.get("SEARCH_PROVIDER_MAX_DAILY_REQUESTS")
    return int(raw) if raw and raw.strip().isdigit() else None


def _max_monthly():
    import os

    raw = os.environ.get("SEARCH_PROVIDER_MAX_MONTHLY_REQUESTS")
    return int(raw) if raw and raw.strip().isdigit() else None


@dataclass
class SearchAttemptResult:
    results: list
    provider_name: str
    attempts: list = field(default_factory=list)  # [(provider_name, outcome_or_reason), ...] -- for diagnostics/reporting


class SearchProviderManager:
    def __init__(self, order=None, max_daily=None, max_monthly=None):
        settings = settings_store.load_settings()
        self.order = order if order is not None else settings["order"]
        self.disabled = set(settings["disabled"])
        self.max_daily = max_daily if max_daily is not None else _max_daily()
        self.max_monthly = max_monthly if max_monthly is not None else _max_monthly()

    def eligible_providers(self):
        """Providers in priority order that have a configured key, are
        not GUI-disabled, and are within their local advisory budget --
        never includes one without a real key."""
        eligible = []
        for name in self.order:
            if name in self.disabled:
                continue
            if not sp.is_provider_configured(name):
                continue
            if not usage_store.check_local_budget(name, self.max_daily, self.max_monthly):
                continue
            eligible.append(name)
        return eligible

    def search(self, query: str, num: int = 10, recency: str | None = None) -> SearchAttemptResult:
        attempts = []

        for provider_name in self.eligible_providers():
            try:
                provider = sp.construct_provider(provider_name)
            except RuntimeError as error:
                attempts.append((provider_name, f"could not construct: {error}"))
                continue

            try:
                results = provider.search(query, num=num, recency=recency)
                usage_store.record_request(provider_name, "success")
                attempts.append((provider_name, f"success ({len(results)} results)"))
                return SearchAttemptResult(results=results, provider_name=provider_name, attempts=attempts)
            except sp.ProviderSearchError as error:
                usage_store.record_request(
                    provider_name, "failure",
                    error_type=error.error_type.value, error_detail=error.detail,
                )
                attempts.append((provider_name, f"{error.error_type.value}: {error.detail}"))
                # Every ProviderSearchError type -> move to next
                # provider (Part 5, steps 3-5). A provider's own
                # internal retry loop (search_provider.py) already
                # handled the "retry same provider" cases before this
                # exception was ever raised.
                continue

        raise SearchProviderPoolExhausted(attempts)


def get_manager():
    """Convenience constructor reading current settings/env fresh --
    cheap (small JSON file reads), safe to call per search()."""
    return SearchProviderManager()
