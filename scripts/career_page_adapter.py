#!/usr/bin/env python3

"""
Generic company career-page adapter -- OFFLINE SKELETON ONLY (Phase 4
multi-source adapter architecture work). Registered in
source_registry.ADAPTERS so the rest of the pipeline (query planner,
search profile, worker) can reason about this source's existence,
capabilities, and status structurally -- but it performs NO live search,
NO live health check, and NO network request of any kind.
health_check() and search() both raise AdapterNotEnabledError
immediately, never a fake empty/successful result.

Unlike the named job boards (LinkedIn, Indeed, Foundit, Instahyre,
Wellfound), "direct company career pages" is not one site but an
open-ended, per-company category (CLAUDE.md's planned adapter sequence,
item 12). A single generic adapter cannot honestly claim any search/
parsing capability for an arbitrary, unknown-in-advance career-page HTML
structure -- this skeleton exists only as the registry placeholder for
that category, so future per-company or template-based implementations
have a documented home and a consistent NOT_ENABLED starting point,
never as a promise that generic scraping across arbitrary company sites
is planned or safe. Per CLAUDE.md: "For sites where automated
interaction is restricted, implement compliant discovery and human
handoff rather than attempting to bypass controls" -- any real future
work here is expected to lean toward human-handoff-assisted discovery,
not blind automated scraping.

Do not invent selectors, APIs, endpoints, URLs, or scraping behavior for
this source.
"""

from source_adapter import (
    JobSourceAdapter,
    SearchQuery,
    AdapterStatus,
    AdapterNotEnabledError,
)


class CareerPageAdapter(JobSourceAdapter):
    name = "CAREER_PAGE"
    status = AdapterStatus.NOT_ENABLED
    capabilities = frozenset()

    def health_check(self):
        raise AdapterNotEnabledError(
            self.name,
            self.status,
            detail=(
                "Generic company career-page adapter is a registered "
                "skeleton only -- not yet implemented. No live request "
                "was made."
            ),
        )

    def search(self, query: SearchQuery):
        raise AdapterNotEnabledError(
            self.name,
            self.status,
            detail=(
                "Generic company career-page adapter is a registered "
                "skeleton only -- not yet implemented. No live request "
                "was made."
            ),
        )
