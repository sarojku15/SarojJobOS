#!/usr/bin/env python3

"""
Web Search discovery adapter -- the integration boundary for using
Claude's (or any) web-search capability as a job-discovery source when
no direct adapter/API exists for a site.

WHY THIS IS A BOUNDARY, NOT A WORKING IMPLEMENTATION:

This adapter runs inside the backend API process (a plain Python
FastAPI/uvicorn worker thread -- see api/search_store.py). That process
has no way to invoke Claude Code's own WebSearch tool: WebSearch is a
capability of the AGENT session that is *driving* this repository (via
Claude Code), not a library this Python process can import or call.
There is currently no runtime-supplied HTTP web-search API (no search
API key/endpoint is configured anywhere in this project's config/.env),
so this adapter has nothing it can legitimately call.

WHAT THIS FILE ACTUALLY PROVIDES:

A `WEB_SEARCH_BACKEND` module-level hook -- any zero-argument-plus-query
callable matching `search_backend(query: SearchQuery) -> list[dict]`
(each dict in the existing CommonJob raw-job shape, see
source_adapter.MockJobSourceAdapter.search() for the reference shape).
When `WEB_SEARCH_BACKEND` is None (the default, and the current state of
this environment), health_check()/search() raise AdapterNotEnabledError
with a message naming exactly what is missing -- never a fabricated
empty/successful result, and never invented job data. Once a real
backend becomes available (e.g. a configured search-API credential, or
this adapter being driven from an agent context that CAN call web
search and hands results in through set_web_search_backend()), wiring
it in is a one-line configuration change here -- no redesign of this
adapter, source_registry.py, or the pipeline downstream of it.

QUALITY CONTROL (Phase J): every raw job dict a backend returns is
still required to pass validate_discovered_job.py's checks (title,
company, source, a usable job_url) before it is treated as an
actionable job anywhere downstream -- this adapter does not weaken that
gate. A web-search backend must never fabricate a URL, salary,
experience, or posted date it did not actually observe; missing fields
must be left empty/None, never guessed.
"""

from source_adapter import (
    AdapterHealth,
    AdapterNotEnabledError,
    AdapterStatus,
    BlockReason,
    JobSourceAdapter,
    SearchQuery,
)

# The integration point. None in every environment where no backend has
# been configured -- see set_web_search_backend().
WEB_SEARCH_BACKEND = None

MAX_QUERIES_PER_SEARCH = 8  # Phase K bound -- see the discovery skill's search-strategy.md


def set_web_search_backend(backend):
    """Register a callable(query: SearchQuery) -> list[dict] as the
    live web-search backend. Intended to be called once, at process
    startup, by whatever runtime actually has web-search access
    available (a configured search-API client, or an agent-driven
    ingestion script) -- never by this module itself."""
    global WEB_SEARCH_BACKEND
    WEB_SEARCH_BACKEND = backend


def clear_web_search_backend():
    global WEB_SEARCH_BACKEND
    WEB_SEARCH_BACKEND = None


class WebSearchDiscoveryAdapter(JobSourceAdapter):
    name = "WEB_SEARCH"
    status = AdapterStatus.NOT_ENABLED
    capabilities = frozenset()

    def health_check(self):
        if WEB_SEARCH_BACKEND is None:
            raise AdapterNotEnabledError(
                self.name,
                self.status,
                detail=(
                    "No web-search backend is configured in this runtime "
                    "(WEB_SEARCH_BACKEND is None). This backend process "
                    "cannot invoke Claude Code's own WebSearch tool "
                    "directly, and no search-API credential is configured. "
                    "Call web_search_discovery_adapter.set_web_search_backend() "
                    "with a real callable to enable this source."
                ),
            )
        return AdapterHealth(source=self.name, reachable=True, block_reason=BlockReason.NONE)

    def search(self, query: SearchQuery):
        if WEB_SEARCH_BACKEND is None:
            raise AdapterNotEnabledError(
                self.name,
                self.status,
                detail=(
                    "No web-search backend is configured in this runtime -- "
                    "see this module's docstring for the integration boundary."
                ),
            )
        return WEB_SEARCH_BACKEND(query)

    # ------------------------------------------------------------------
    # Convenience methods (Phase 13 broad-discovery, Part 9)
    # ------------------------------------------------------------------
    # These do not add any new capability -- every one of them still
    # goes through the exact same WEB_SEARCH_BACKEND is None check above
    # and raises the exact same AdapterNotEnabledError when no backend
    # is configured (the honest, current state of this runtime: no
    # search-API credential is configured anywhere in this project's
    # config/.env). They exist purely as the documented shape a future
    # real backend integrates against, per Part 9's explicit interface
    # request -- "PROVIDER_INTERFACE_READY / BACKEND_NOT_CONFIGURED".
    #
    # IMPORTANT DISTINCTION, restated from the module docstring: the
    # interactive agent session that develops/operates this repository
    # (via Claude Code) DOES have its own WebSearch tool -- but that is
    # a capability of the AGENT, not of this deployed backend process,
    # and the two must never be conflated. A human/agent operator can
    # use their own web-search capability to gather job URLs and hand
    # them to JobOS via set_web_search_backend() for a one-off,
    # supervised capture (see scripts/web_search_evidence_capture.py),
    # or via the manual-import flow (scripts/manual_import.py) -- but
    # this backend process itself still cannot invoke web search
    # autonomously, and search()/search_site()/search_jobs()/
    # search_recent_jobs() must never pretend otherwise.

    def search_site(self, site: str, query: SearchQuery):
        """Search scoped to one site (e.g. site="linkedin.com/jobs/view")
        -- the shape the discovery skill's `site:` query strategy needs
        (see Part 10). Delegates to the same WEB_SEARCH_BACKEND, passing
        the site restriction through query.extra["site"] so a real
        backend can build a `site:<site> ...` query however it likes;
        this method never constructs a query string itself."""
        if WEB_SEARCH_BACKEND is None:
            raise AdapterNotEnabledError(
                self.name,
                self.status,
                detail=(
                    f"No web-search backend is configured in this runtime -- cannot "
                    f"search_site({site!r})."
                ),
            )
        scoped_query = SearchQuery(
            role=query.role,
            location=query.location,
            experience_years=query.experience_years,
            exclude_keywords=list(query.exclude_keywords),
            extra={**query.extra, "site": site},
            max_job_age_days=query.max_job_age_days,
        )
        return WEB_SEARCH_BACKEND(scoped_query)

    def search_jobs(self, role: str, location: str, max_job_age_days: int | None = None):
        """Convenience constructor: build a plain SearchQuery(role,
        location) and run it through search(). Not site-scoped -- a
        backend is free to fan this out across multiple sites itself."""
        return self.search(SearchQuery(role=role, location=location, max_job_age_days=max_job_age_days))

    def search_recent_jobs(self, role: str, location: str, max_age_days: int):
        """Same as search_jobs(), naming the freshness constraint
        explicitly (Part 9's requested method name) -- max_age_days is
        required here, unlike search_jobs()'s optional max_job_age_days."""
        return self.search_jobs(role=role, location=location, max_job_age_days=max_age_days)
