#!/usr/bin/env python3

"""
JobSourceAdapter integration for the seven restricted job boards,
discovered via a real search-provider API (Serper.dev) rather than
direct crawling (see restricted_source_registry.py's module docstring
for why, and CLAUDE.md's Source Adapter Principle). One shared base
class, parameterized per site -- no per-source duplicate logic.

Registry key vs. raw-job source (Phase 13's discovery_source/job_source
model, reused unchanged): each of these adapters is registered in
source_registry.ADAPTERS under a *_SEARCH key (e.g. "LINKEDIN_SEARCH"),
deliberately distinct from the pre-existing "LINKEDIN" skeleton
(linkedin_adapter.py -- a placeholder for a hypothetical future
authorized DIRECT crawler, still NOT_ENABLED, untouched by this phase).
The JOB RECORDS these adapters emit still carry the real board name as
`source`/`job_source` (e.g. "LINKEDIN") -- only the REGISTRY key is
"*_SEARCH"; `discovery_source` is set to "SEARCH_PROVIDER:<PROVIDER>"
so a Naukri-style direct job and a search-provider-discovered job for
the same board are never confused, and so a future authorized direct
LinkedIn adapter could coexist and be told apart from this one purely
by discovery_source, without any job_id collision (job_id.py hashes on
source + normalized URL, and both would use source="LINKEDIN").

STATUS GATING: like every other adapter in this project,
`status` is a plain class attribute, fixed once at class-definition
time (i.e. at first import of this module) -- never re-evaluated per
call. Phase 14.2 changed WHAT it checks: instead of only
SERPER_API_KEY, it is ENABLED iff at least one provider in the whole
pool (search_provider.get_configured_providers()) has a configured
key. This is the same "fixed at import time" convention
NaukriAdapter/HiristAdapter/ApnaAdapter already use;
source_registry.discover_from_sources() and source_capabilities.py
both read `status` this same way for every other adapter, so a
per-call dynamic status would be inconsistent with the rest of this
codebase. A test that wants ENABLED behavior offline sets a placeholder
*_API_KEY env var BEFORE first importing this module (see
test_phase14_search_provider.py / test_phase14_2_multi_provider.py) and
always injects a provider via set_search_provider() so no real network
call is ever made regardless.

PROVIDER RESOLUTION (Phase 14.2): search() no longer constructs
SerperProvider directly. Two paths, in order:
  1. An explicitly injected provider (set_search_provider(), still the
     exact same hook Phase 14 tests already use) -- used AS-IS, no
     failover, for deterministic single-provider tests/live-validation.
  2. Otherwise, search_provider_manager.SearchProviderManager -- the
     real multi-provider pool with priority ordering and failover
     (Part 5). Its own SearchProviderPoolExhausted becomes the same
     AdapterBlockedError vocabulary a single-provider failure already
     used in Phase 14.
"""

import logging
from datetime import datetime, timezone

from source_adapter import (
    AdapterBlockedError,
    AdapterCapability,
    AdapterHealth,
    AdapterNotEnabledError,
    AdapterStatus,
    BlockReason,
    JobSourceAdapter,
    SearchQuery,
)
from search_provider import get_configured_providers
from search_provider_manager import SearchProviderManager, SearchProviderPoolExhausted
from restricted_source_registry import SITE_BY_KEY, build_site_query, validate_and_parse_hit

logger = logging.getLogger(__name__)

_MAX_RESULTS_PER_QUERY = 10

# ---------------------------------------------------------------------
# Provider injection point (mirrors web_search_discovery_adapter.py's
# WEB_SEARCH_BACKEND hook exactly) -- None by default. Tests and a
# controlled live-validation run inject a Replay/Recording/single-live
# provider instance explicitly; nothing here ever constructs a live
# provider speculatively. When nothing is injected, search() falls
# through to the real SearchProviderManager pool (see module docstring).
# ---------------------------------------------------------------------

_SEARCH_PROVIDER = None


def set_search_provider(provider):
    global _SEARCH_PROVIDER
    _SEARCH_PROVIDER = provider


def clear_search_provider():
    global _SEARCH_PROVIDER
    _SEARCH_PROVIDER = None


def _recency_filter(max_job_age_days):
    """Serper's own recency filter is coarse (qdr:d / qdr:w / qdr:m) --
    picking the narrowest useful window is a best-effort request to the
    provider, NEVER treated as proof every result is within
    max_job_age_days (Phase 14 explicit requirement). Real freshness is
    always re-derived downstream by freshness.classify_freshness() from
    whatever date text the hit itself carried (often none -> UNKNOWN)."""
    if max_job_age_days is None:
        return "qdr:w"
    if max_job_age_days <= 1:
        return "qdr:d"
    if max_job_age_days <= 7:
        return "qdr:w"
    return "qdr:m"


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SearchProviderAdapterBase(JobSourceAdapter):
    """Not registered directly -- see the seven concrete subclasses
    below, each only setting `name`/`SITE_KEY`.

    HOT-REFRESH (Phase 14.2 fix): the CLASS attribute `status` below is
    still fixed once at first import, exactly like every other
    adapter's status in this codebase (source_capabilities.py's
    generic _capability_support() and source_registry.
    get_adapter_status() both read `.status` on the CLASS, never an
    instance, so changing that convention would ripple into every
    other adapter -- out of scope for this fix and not attempted).

    What DOES need to be live is the actual pipeline gate:
    source_registry.discover_from_sources() reads `.status` on an
    INSTANCE (getattr(adapter, "status", ...), where `adapter =
    get_adapter(source)` constructs a FRESH instance every single call
    -- see get_adapter()'s own docstring). __init__ below sets an
    INSTANCE attribute that shadows the class attribute for exactly
    that read, recomputed fresh on every instantiation from
    get_configured_providers() (itself a live os.environ check, never
    cached) -- so the moment a GUI "Save key" call updates os.environ
    (see api/search_provider_settings_api.py's set_key(), which calls
    env_config.write_env_key()), the VERY NEXT search run correctly
    uses the new key, with no process restart required. This is a
    plain instance attribute, not a property, so it changes nothing
    about how any other adapter class -- or any existing class-level
    `.status` read on THIS class -- behaves."""

    SITE_KEY = None
    status = AdapterStatus.ENABLED if get_configured_providers() else AdapterStatus.NOT_ENABLED
    capabilities = frozenset({AdapterCapability.SEARCH})

    def __init__(self):
        self.status = AdapterStatus.ENABLED if get_configured_providers() else AdapterStatus.NOT_ENABLED

    def health_check(self):
        if _SEARCH_PROVIDER is None and not get_configured_providers():
            raise AdapterNotEnabledError(
                self.name,
                self.status,
                detail=(
                    f"{self.name}: no search provider is configured (none of "
                    f"{', '.join(sorted(set(('you', 'tavily', 'exa', 'brave', 'serper'))))}'s "
                    "*_API_KEY environment variables is set, and no provider "
                    "was injected via set_search_provider())."
                ),
            )
        return AdapterHealth(
            source=self.name,
            reachable=True,
            block_reason=BlockReason.NONE,
            detail="no live probe performed -- search() is this adapter's only network boundary",
        )

    def search(self, query: SearchQuery):
        site = SITE_BY_KEY[self.SITE_KEY]
        query_text = build_site_query(site, query.role, query.location)
        recency = _recency_filter(query.max_job_age_days)

        if _SEARCH_PROVIDER is not None:
            # Explicit single-provider override (tests / controlled
            # live-validation, Part 19) -- used as-is, no pool/failover.
            try:
                hits = _SEARCH_PROVIDER.search(query_text, num=_MAX_RESULTS_PER_QUERY, recency=recency)
            except Exception as error:
                raise AdapterBlockedError(
                    self.name, BlockReason.UNKNOWN_BLOCK,
                    detail=f"search provider ({_SEARCH_PROVIDER.name}) error for query {query_text!r}: {error}",
                ) from error
            provider_name = _SEARCH_PROVIDER.name
        else:
            if not get_configured_providers():
                raise AdapterNotEnabledError(
                    self.name, self.status,
                    detail=f"{self.name}: no search provider configured (no *_API_KEY set, none injected).",
                )
            manager = SearchProviderManager()
            try:
                attempt = manager.search(query_text, num=_MAX_RESULTS_PER_QUERY, recency=recency)
            except SearchProviderPoolExhausted as error:
                # Every eligible provider in the pool failed (Part 5,
                # step 9) -- surfaced as the existing AdapterBlockedError
                # vocabulary, exactly like a single-provider failure did
                # in Phase 14. Never a fabricated result.
                raise AdapterBlockedError(
                    self.name, BlockReason.UNKNOWN_BLOCK,
                    detail=f"search provider pool exhausted for query {query_text!r}: {error}",
                ) from error
            hits = attempt.results
            provider_name = attempt.provider_name

        now = _now_iso()
        seen_job_ids = set()
        jobs = []

        for hit in hits:
            validated, reject_reason = validate_and_parse_hit(site, hit)
            if validated is None:
                logger.debug("rejected %s hit (%s): %s", self.name, reject_reason, hit.get("url", ""))
                continue
            if validated.job_id in seen_job_ids:
                continue
            seen_job_ids.add(validated.job_id)

            jobs.append(
                {
                    "source": self.name,
                    "job_source": self.name,
                    "discovery_source": f"SEARCH_PROVIDER:{provider_name.upper()}",
                    "discovery_query": query_text,
                    "completeness": validated.completeness,
                    "discovered_at": now,
                    "company": validated.company,
                    "title": validated.title,
                    "location": validated.location,
                    "work_model": "",
                    "job_url": validated.canonical_url,
                    "application_url": validated.canonical_url,
                    # A search-provider "date" hint, when present, is
                    # frequently a relative phrase ("3 days ago") --
                    # freshness.classify_freshness() already parses that
                    # exact shape (built for Naukri's own relative
                    # dates). When absent, posted_date stays "" and
                    # freshness correctly resolves to UNKNOWN -- never
                    # guessed here.
                    "posted_date": validated.posted_hint or "",
                    "jd_text": validated.description_snippet,
                    "experience_required": "",
                    "mandatory_skills": [],
                    "preferred_skills": [],
                }
            )

        return jobs


def _make_adapter_class(site_key):
    class _Adapter(SearchProviderAdapterBase):
        name = site_key
        SITE_KEY = site_key

    _Adapter.__name__ = f"{site_key.title()}SearchProviderAdapter"
    _Adapter.__qualname__ = _Adapter.__name__
    return _Adapter


LinkedInSearchProviderAdapter = _make_adapter_class("LINKEDIN")
IndeedSearchProviderAdapter = _make_adapter_class("INDEED")
FounditSearchProviderAdapter = _make_adapter_class("FOUNDIT")
InstahyreSearchProviderAdapter = _make_adapter_class("INSTAHYRE")
CutshortSearchProviderAdapter = _make_adapter_class("CUTSHORT")
WellfoundSearchProviderAdapter = _make_adapter_class("WELLFOUND")
ShineSearchProviderAdapter = _make_adapter_class("SHINE")

SEARCH_PROVIDER_ADAPTERS = {
    "LINKEDIN_SEARCH": LinkedInSearchProviderAdapter,
    "INDEED_SEARCH": IndeedSearchProviderAdapter,
    "FOUNDIT_SEARCH": FounditSearchProviderAdapter,
    "INSTAHYRE_SEARCH": InstahyreSearchProviderAdapter,
    "CUTSHORT_SEARCH": CutshortSearchProviderAdapter,
    "WELLFOUND_SEARCH": WellfoundSearchProviderAdapter,
    "SHINE_SEARCH": ShineSearchProviderAdapter,
}
