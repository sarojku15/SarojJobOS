#!/usr/bin/env python3

"""
Declarative source capability matrix (Phase L). Wraps
source_registry.py's existing ENABLED/NOT_ENABLED truth (never
duplicates or overrides it) with the extra descriptive metadata the
GUI/API need to explain, per source, WHY it is or isn't usable right
now: acquisition_method, what it structurally supports, and -- for an
unavailable source -- an explicit machine-readable reason code plus a
one-line human explanation. Nothing here changes any adapter's actual
AdapterStatus; this is read-only descriptive metadata layered on top.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import source_registry
from source_adapter import AdapterCapability, AdapterStatus

# Phase 13 broad-discovery: which of the seven sources investigated this
# phase have real, dated search-provider evidence captured this session
# (scripts/web_search_evidence_capture.py) -- i.e. a real WebSearch call,
# run by the agent operating this repository, actually returned public
# job URLs for that source. This is NOT the same as the deployed backend
# being able to repeat that search itself (see web_search_discovery_
# adapter.py's WEB_SEARCH_BACKEND boundary) -- it proves the acquisition
# PATH is viable and the pipeline can consume its output, nothing more.
# A source not in this dict was not (re-)investigated for a search-
# provider path this phase.
_SEARCH_PROVIDER_EVIDENCE = {
    "LINKEDIN": {"status": "EVIDENCE_CAPTURED", "normalizable_jobs": 9, "urls_found": 9, "captured": "2026-09-21"},
    "INDEED": {"status": "EVIDENCE_CAPTURED_URL_ONLY", "normalizable_jobs": 0, "urls_found": 9, "captured": "2026-09-21"},
    "FOUNDIT": {"status": "EVIDENCE_CAPTURED", "normalizable_jobs": 5, "urls_found": 6, "captured": "2026-09-21"},
    "INSTAHYRE": {"status": "EVIDENCE_CAPTURED", "normalizable_jobs": 9, "urls_found": 9, "captured": "2026-09-21"},
    "CUTSHORT": {"status": "EVIDENCE_CAPTURED", "normalizable_jobs": 5, "urls_found": 5, "captured": "2026-09-21"},
    "WELLFOUND": {"status": "EVIDENCE_CAPTURED", "normalizable_jobs": 3, "urls_found": 3, "captured": "2026-09-21"},
    "SHINE": {"status": "ATTEMPTED_NO_RESULTS", "normalizable_jobs": 0, "urls_found": 0, "captured": "2026-09-21"},
}

# Sources for which a human pasting in a publicly-viewable job's own
# details (title/company/URL/etc.) is a genuinely usable fallback --
# i.e. a real job board a candidate would actually be looking at, as
# opposed to MOCK/CAREER_PAGE/WEB_SEARCH/the three ATS providers, which
# are either internal fixtures or already have their own direct
# discovery path. See scripts/manual_import.py.
_MANUAL_IMPORT_CAPABLE = {
    "LINKEDIN", "INDEED", "FOUNDIT", "INSTAHYRE", "CUTSHORT", "WELLFOUND", "SHINE",
    "NAUKRI", "HIRIST", "IIMJOBS", "APNA",
}

# Static descriptive metadata per source. `reason` is populated only
# for sources this project has actually investigated and found
# unavailable/not-ready -- never a guess, and always matching what
# each adapter's own module docstring says.
_METADATA = {
    "NAUKRI": {
        "acquisition_method": "adapter",
        "supports_pagination": True,
        "supports_detail_pages": True,
        "supports_native_freshness": True,
        "reason": None,
    },
    "MOCK": {
        "acquisition_method": "adapter",
        "supports_pagination": False,
        "supports_detail_pages": False,
        "supports_native_freshness": False,
        "reason": "Development/test fixture source, not a real job board.",
    },
    "LINKEDIN": {
        "acquisition_method": "web_search_discovery/provider",
        "reason": "NO_AUTHORIZED_DIRECT_JOB_DISCOVERY -- robots.txt disallows unrecognized agents; "
        "no authorized API/provider credential is configured (see data/reports/linkedin_phase5_inspection.md).",
    },
    "HIRIST": {
        "acquisition_method": "adapter",
        "supports_pagination": True,
        "supports_detail_pages": True,
        "reason": None,  # Phase 11: ENABLED -- see phase11_public_multisource_completion.md
    },
    "INDEED": {
        "acquisition_method": "web_search_discovery/provider",
        "reason": "NOT_AUTHORIZED -- robots.txt explicitly names ClaudeBot/anthropic-ai/Claude-User/"
        "Claude-SearchBot (among other AI crawlers) and disallows them from /jobs -- a direct, "
        "unambiguous signal against exactly this kind of automated discovery (checked fresh this phase).",
    },
    "FOUNDIT": {
        "acquisition_method": "web_search_discovery/provider",
        "reason": "NOT_AUTHORIZED -- robots.txt explicitly names ClaudeBot (among other AI crawlers) and "
        "disallows it from /jobs/ and /search/; a live fetch of the user's own real, provided search "
        "URL additionally returned HTTP 403 Forbidden outright (re-confirmed Phase 12).",
    },
    "INSTAHYRE": {
        "acquisition_method": "provider",
        "reason": "NOT_AUTHORIZED -- a live fetch of the user's own real, publicly-viewable job detail URL "
        "returned HTTP 403 with an active Cloudflare challenge-platform response (checked this "
        "phase, Phase 12) -- an actual server-side block, not merely a parser-engineering gap. "
        "robots.txt itself is permissive, but the live behavior is not.",
    },
    "CUTSHORT": {
        "acquisition_method": "provider",
        "reason": "NOT_AUTHORIZED -- robots.txt permits generic-agent crawling, and the initial SSR HTML "
        "carries a __NEXT_DATA__ React Query 'dehydratedState' that is genuinely null (job data is "
        "fetched client-side only); a live network-request capture found active Cloudflare "
        "challenge-platform requests firing during a normal automated page load (checked this "
        "phase, Phase 12) -- building a production adapter here risks triggering a real CAPTCHA "
        "challenge, which this project must never solve.",
    },
    "WELLFOUND": {
        "acquisition_method": "provider",
        "reason": "NOT_AUTHORIZED -- robots.txt disallows /search; the rendered page also embeds active "
        "Cloudflare Turnstile and hCaptcha challenge infrastructure (checked fresh this phase) -- "
        "reliable automated access risks triggering a CAPTCHA challenge this project must never solve.",
    },
    "SHINE": {
        "acquisition_method": "provider",
        "reason": "SOURCE_NOT_READY -- robots.txt disallows most job-search and numeric job-ID paths for "
        "generic agents (checked fresh this phase); not prioritized further this phase.",
    },
    "TIMESJOBS": {
        "acquisition_method": "provider",
        "reason": "SOURCE_NOT_READY -- robots.txt is permissive (checked fresh this phase), but the site's "
        "own TLS certificate fails standard verification; not investigated further this phase.",
    },
    "IIMJOBS": {
        "acquisition_method": "adapter",
        "supports_pagination": True,
        "supports_detail_pages": True,
        "reason": None,  # Phase 11: ENABLED -- shares Hirist's platform, see phase11_public_multisource_completion.md
    },
    "APNA": {
        "acquisition_method": "adapter",
        "supports_pagination": True,
        "reason": None,  # Phase 12: ENABLED -- see phase12_public_source_expansion.md
    },
    "CAREER_PAGE": {"acquisition_method": "provider", "reason": "SOURCE_NOT_READY -- generic career-page provider not yet implemented/validated."},
    "GREENHOUSE": {
        "acquisition_method": "provider",
        "reason": "SOURCE_NOT_READY -- working implementation exists against Greenhouse's public API, but zero "
        "company boards are configured (config/career_pages.json) and it has not been through this "
        "project's phased live-validation process yet.",
    },
    "LEVER": {
        "acquisition_method": "provider",
        "reason": "SOURCE_NOT_READY -- working implementation exists against Lever's public API, but zero "
        "company boards are configured and it has not been through phased live-validation yet.",
    },
    "ASHBY": {
        "acquisition_method": "provider",
        "reason": "SOURCE_NOT_READY -- working implementation exists against Ashby's public API, but zero "
        "company boards are configured and it has not been through phased live-validation yet.",
    },
    "WEB_SEARCH": {
        "acquisition_method": "web_search_discovery",
        "reason": "NO_WEB_SEARCH_BACKEND_CONFIGURED -- this backend process has no way to invoke a web-search "
        "tool at runtime (see scripts/web_search_discovery_adapter.py's docstring for the exact boundary). "
        "The provider interface exists; wiring in a real backend is a configuration change, not a redesign.",
    },
}

_DEFAULT_METADATA = {"acquisition_method": "provider", "reason": "SOURCE_NOT_READY -- not yet implemented or validated."}


def _capability_support(adapter_cls, metadata):
    caps = adapter_cls.capabilities
    return {
        "supports_search": AdapterCapability.SEARCH in caps or adapter_cls.status == AdapterStatus.ENABLED,
        "supports_freshness": AdapterCapability.NATIVE_FRESHNESS in caps,
        "supports_location": AdapterCapability.LOCATION_FILTER in caps or True,  # every adapter takes a SearchQuery.location
        "supports_experience": False,  # no adapter today filters server-side by experience; SearchQuery carries it, none use it yet
        "supports_salary": AdapterCapability.SALARY in caps,
        "supports_remote": AdapterCapability.REMOTE_FILTER in caps,
        "supports_pagination": metadata.get("supports_pagination", AdapterCapability.PAGINATION in caps),
        "supports_detail_pages": metadata.get("supports_detail_pages", AdapterCapability.DETAIL in caps),
        "supports_web_search_discovery": adapter_cls.__name__ == "WebSearchDiscoveryAdapter",
    }


# Phase 13 broad-discovery final-status vocabulary (Part 14/18). Layered
# strictly on top of the pre-existing enabled/authorized/status/reason
# fields above -- nothing here changes what those mean or how any other
# caller (search_store.enabled_sources(), the GUI's existing
# renderSourceStatus()) already reads them.
_ATS_PROVIDER_SOURCES = {"GREENHOUSE", "LEVER", "ASHBY"}

# Phase 14: each restricted board's real search-provider ADAPTER is
# registered under a separate "*_SEARCH" key in source_registry.ADAPTERS
# (see search_provider_adapter.py) -- deliberately distinct from the
# board's own key here (still reserved for a hypothetical future
# authorized DIRECT crawler). This maps board name -> its search-
# provider sibling's registry key, so this board's ONE capability
# record can honestly reflect whether THAT sibling is actually usable
# (AdapterStatus.ENABLED iff SERPER_API_KEY is configured), without
# the GUI ever seeing two separate, confusing rows for "LinkedIn."
_SEARCH_PROVIDER_REGISTRY_KEYS = {
    "LINKEDIN": "LINKEDIN_SEARCH",
    "INDEED": "INDEED_SEARCH",
    "FOUNDIT": "FOUNDIT_SEARCH",
    "INSTAHYRE": "INSTAHYRE_SEARCH",
    "CUTSHORT": "CUTSHORT_SEARCH",
    "WELLFOUND": "WELLFOUND_SEARCH",
    "SHINE": "SHINE_SEARCH",
}

# Boards this project has direct evidence (Phase 13/14) never yield a
# usable company field from the search provider even when it IS
# configured (Indeed's own search-result titles carry no company name
# at all -- verified against 9 real results). Phase 14.2's board-level
# vocabulary (search_provider_status/final_status) intentionally does
# NOT carry a separate "quality-limited" state -- see
# _search_provider_status()'s docstring -- so this caveat is preserved
# honestly as a SEPARATE note field instead
# (search_provider_quality_note), never silently dropped, never
# upgraded into a false AVAILABLE-with-no-caveat.
_SEARCH_PROVIDER_QUALITY_NOTES = {
    "INDEED": "Indeed's own search-result titles never include a company name (verified against real data) -- job URLs are discovered correctly, but company will show as UNKNOWN unless the candidate manually imports the job.",
}

# The eleven job BOARDS this project discovers jobs from (Phase 14.2,
# Part 2) -- the only sources the board-level final_status priority
# logic (_compute_final_status()) applies to. Every other registered
# source (the three ATS providers, WEB_SEARCH, CAREER_PAGE, MOCK,
# TIMESJOBS) keeps its pre-existing, unrelated status vocabulary.
_BOARDS = {
    "NAUKRI", "HIRIST", "IIMJOBS", "APNA",
    "LINKEDIN", "INDEED", "FOUNDIT", "INSTAHYRE", "CUTSHORT", "WELLFOUND", "SHINE",
}

# Phase 14.2: human-facing display names for the provider pool (Part
# 16's GUI examples use "You.com"/"Tavily", not the internal lowercase
# registry keys "you"/"tavily").
_PROVIDER_DISPLAY_NAMES = {
    "you": "You.com",
    "tavily": "Tavily",
    "exa": "Exa",
    "brave": "Brave",
    "serper": "Serper",
}


def _active_provider_name(source_name):
    """The provider (e.g. "you") that would actually be used for this
    board's next search RIGHT NOW, per SearchProviderManager's own
    priority ordering + configured-key + enabled + local-budget checks
    -- or None if no provider in the pool is currently eligible. Only
    meaningful for a board with a search-provider sibling registered at
    all (see _SEARCH_PROVIDER_REGISTRY_KEYS)."""
    if source_name not in _SEARCH_PROVIDER_REGISTRY_KEYS:
        return None
    import search_provider_manager

    eligible = search_provider_manager.SearchProviderManager().eligible_providers()
    return eligible[0] if eligible else None


def _search_provider_status(source_name, active_provider="__unset__"):
    """None for a board with no search-provider sibling registered at
    all; otherwise Part 7's bare vocabulary: NOT_CONFIGURED / AVAILABLE
    (DISABLED / AUTH_FAILED / RATE_LIMITED / QUOTA_EXHAUSTED /
    TEMPORARY_ERROR are per-PROVIDER states -- see
    search_provider_usage_store.py -- surfaced at the board level only
    when every eligible provider in the pool is currently in one of
    those states; NOT_CONFIGURED/AVAILABLE cover the two states that
    matter for whether this board can be searched at all right now).

    Deliberately derived from the SAME live check as
    _active_provider_name() (SearchProviderManager.eligible_providers(),
    which reads is_provider_configured() fresh every call) -- NOT from
    the search-provider adapter's own AdapterStatus, which (like every
    adapter's status in this codebase) is fixed once at that module's
    first import and only changes on a process restart. Using the
    stale, import-time status here would let this field disagree with
    _active_provider_name() the moment a key is added mid-process (e.g.
    via the Settings page's "Save key", which updates os.environ
    immediately) -- a real inconsistency this phase's own test suite
    caught. search_provider_active_provider/search_provider are already
    meant to be a live "would work right now" description (this
    module's own docstring: "explain... WHY it is or isn't usable
    right now"), so this field matches that same freshness."""
    if source_name not in _SEARCH_PROVIDER_REGISTRY_KEYS:
        return None
    if active_provider == "__unset__":
        active_provider = _active_provider_name(source_name)
    return "AVAILABLE" if active_provider else "NOT_CONFIGURED"


def _direct_status(source_name, enabled, metadata):
    """The board's OWN direct-adapter status, independent of any
    search-provider path -- Part 3's explicit requirement that this
    project never claims direct_status=ENABLED for a board it cannot
    actually crawl directly."""
    if enabled:
        return "ENABLED"
    if source_name in _BOARDS and source_name in _SEARCH_PROVIDER_REGISTRY_KEYS:
        # One of the seven restricted boards -- Phase 12/13 evidence
        # (robots.txt/named-crawler-disallow/active anti-bot response)
        # is specifically that direct automated access is NOT
        # authorized, not merely "not implemented yet".
        return "NOT_AUTHORIZED"
    reason = metadata.get("reason") or ""
    if reason.startswith("NOT_AUTHORIZED") or reason.startswith("NO_AUTHORIZED_DIRECT_JOB_DISCOVERY"):
        return "NOT_AUTHORIZED"
    return "NOT_ENABLED"


def _compute_final_status(source_name, enabled, active_provider="__unset__"):
    if enabled:
        return "ENABLED"

    # Non-board sources (the three ATS providers, WEB_SEARCH,
    # CAREER_PAGE, MOCK, TIMESJOBS) keep their own, unrelated
    # vocabulary -- Part 4's priority logic below applies only to the
    # eleven job boards (Part 2).
    if source_name in _ATS_PROVIDER_SOURCES:
        return "ATS_FALLBACK"
    if source_name in ("WEB_SEARCH", "CAREER_PAGE", "MOCK", "TIMESJOBS"):
        return "NOT_CONFIGURED"
    if source_name.endswith("_SEARCH") and source_name in source_registry.ADAPTERS:
        # A *_SEARCH registry key's OWN record (diagnostic use only --
        # list_enabled_sources()/list_unavailable_sources() exclude
        # these from the GUI-facing listing) mirrors its own plain
        # AdapterStatus honestly rather than borrowing the board-level
        # vocabulary below.
        return "NOT_CONFIGURED"

    # Part 4's exact board-level priority logic:
    #   1. direct enabled -> ENABLED (handled above)
    #   2. a real, configured, capable search provider -> AVAILABLE_VIA_SEARCH_PROVIDER
    #   3. a search-provider PATH exists but no key configured -> SEARCH_PROVIDER_NOT_CONFIGURED
    #   4. manual import available -> MANUAL_IMPORT_AVAILABLE
    #   5. otherwise -> NOT_AVAILABLE
    search_provider_status = _search_provider_status(source_name, active_provider)
    if search_provider_status == "AVAILABLE":
        return "AVAILABLE_VIA_SEARCH_PROVIDER"
    if search_provider_status == "NOT_CONFIGURED":
        return "SEARCH_PROVIDER_NOT_CONFIGURED"

    if source_name in _MANUAL_IMPORT_CAPABLE:
        return "MANUAL_IMPORT_AVAILABLE"
    return "NOT_AVAILABLE"


def get_source_capability(source_name):
    """One board/source's full capability record.

    Phase 14.2 board-level fields (Part 16's exact API shape):
    `source` (alias of source_name), `direct_status` (ENABLED /
    NOT_AUTHORIZED / NOT_ENABLED -- NEVER claims ENABLED for a board
    this project cannot actually crawl directly), `search_provider_status`
    (NOT_CONFIGURED / AVAILABLE), `search_provider` (the uppercase name
    of the provider that would actually answer right now, e.g. "TAVILY",
    or None), `final_status` (Part 4's priority-ordered vocabulary:
    ENABLED / AVAILABLE_VIA_SEARCH_PROVIDER / SEARCH_PROVIDER_NOT_CONFIGURED
    / MANUAL_IMPORT_AVAILABLE / NOT_AVAILABLE for the eleven boards;
    ATS_FALLBACK/NOT_CONFIGURED for every other registered source, an
    unrelated vocabulary), `manual_import_available`.

    Pre-existing fields (unchanged): `source_name`, `acquisition_method`,
    `enabled`, `authorized`, `status`, `reason`, `search_provider_evidence`,
    `search_provider_active_provider`/`_display` (lowercase key / human
    display name, kept alongside the new uppercase `search_provider` for
    the GUI), `search_provider_quality_note` (a caveat preserved
    separately from the primary vocabulary -- see
    _SEARCH_PROVIDER_QUALITY_NOTES), `supports_*`."""
    adapter_cls = source_registry.ADAPTERS[source_name]
    metadata = _METADATA.get(source_name, _DEFAULT_METADATA)
    status = adapter_cls.status
    enabled = status == AdapterStatus.ENABLED
    active_provider = _active_provider_name(source_name)

    return {
        "source_name": source_name,
        "source": source_name,
        "acquisition_method": metadata["acquisition_method"],
        "enabled": enabled,
        "authorized": enabled and source_name != "LINKEDIN",
        "status": status.value,
        "reason": None if enabled else metadata.get("reason"),
        "direct_status": _direct_status(source_name, enabled, metadata),
        "search_provider_status": _search_provider_status(source_name, active_provider),
        "search_provider": active_provider.upper() if active_provider else None,
        "search_provider_active_provider": active_provider,
        "search_provider_active_provider_display": _PROVIDER_DISPLAY_NAMES.get(active_provider),
        "search_provider_quality_note": _SEARCH_PROVIDER_QUALITY_NOTES.get(source_name),
        "final_status": _compute_final_status(source_name, enabled, active_provider),
        "search_provider_evidence": _SEARCH_PROVIDER_EVIDENCE.get(source_name),
        "manual_import_available": source_name in _MANUAL_IMPORT_CAPABLE,
        **_capability_support(adapter_cls, metadata),
    }


# Phase 14 registry keys are an internal implementation detail (the
# actual JobSourceAdapter the pipeline queries) -- the GUI/API-facing
# capability listing shows exactly one row per BOARD (e.g. "LINKEDIN",
# whose record already reflects its search-provider sibling via
# search_provider_status/final_status above), never a second row for
# "LINKEDIN_SEARCH". get_source_capability("LINKEDIN_SEARCH") still
# works directly for diagnostics/tests.
_INTERNAL_REGISTRY_KEYS = frozenset(_SEARCH_PROVIDER_REGISTRY_KEYS.values())


def list_source_capabilities():
    return [
        get_source_capability(name)
        for name in source_registry.list_sources()
        if name not in _INTERNAL_REGISTRY_KEYS
    ]


# Part 11: a board that is usable at all right now -- via a direct
# adapter OR a configured search provider -- belongs in the GUI's
# "Available" bucket, not "Unavailable/Not configured", even though its
# own direct AdapterStatus (the `enabled` field) is still False for the
# search-provider case. This changes ONLY these two GUI-facing listing
# functions; `enabled` itself (the raw AdapterStatus fact) is untouched,
# and search_store.enabled_sources() -- the actual query-execution gate
# -- reads AdapterStatus directly, never these two functions.
_AVAILABLE_FINAL_STATUSES = {"ENABLED", "AVAILABLE_VIA_SEARCH_PROVIDER"}


def list_enabled_sources():
    return [
        c for c in list_source_capabilities()
        if c["final_status"] in _AVAILABLE_FINAL_STATUSES and c["source_name"] != "MOCK"
    ]


def list_unavailable_sources():
    return [c for c in list_source_capabilities() if c["final_status"] not in _AVAILABLE_FINAL_STATUSES]


if __name__ == "__main__":
    import json

    print(json.dumps(list_source_capabilities(), indent=2))
