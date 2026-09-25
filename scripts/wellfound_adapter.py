#!/usr/bin/env python3

"""
Wellfound adapter -- OFFLINE SKELETON ONLY (Phase 4 multi-source adapter
architecture work). Registered in source_registry.ADAPTERS so the rest
of the pipeline (query planner, search profile, worker) can reason about
this source's existence, capabilities, and status structurally -- but it
performs NO live search, NO live health check, and NO network request of
any kind. health_check() and search() both raise AdapterNotEnabledError
immediately, never a fake empty/successful result.

Per CLAUDE.md's "Planned adapter sequence" and "Source Adapter
Principle": before this source is actually implemented, its current live
website behavior must be inspected, config/site_adapters.json's ethical
automation policy reviewed, and a real, evidence-based, phased
validation run (Phase 1: offline implementation + tests, Phase 2: one
live query, Phase 3: controlled multi-query) -- exactly the process
scripts/naukri_adapter.py already went through. None of that has
happened yet for Wellfound. `capabilities` is deliberately empty until
real implementation work begins; nothing here should be read as a
promise about what Wellfound will or won't support once implemented.

Do not invent selectors, APIs, endpoints, URLs, or scraping behavior for
this source.

Phase 12 investigation (data/reports/phase12_public_source_expansion.md):
the job DETAIL page (e.g. /jobs/<id>-<slug>) is cleanly accessible via a
plain HTTP GET with a full, clean schema.org JobPosting JSON-LD block
and no anti-bot markers -- but the SEARCH/role listing page (e.g.
/role/r/<role-slug>), the mechanism a real adapter would need to
DISCOVER job URLs in the first place, carries active Cloudflare
Turnstile/hCaptcha challenge markup even on a plain, unauthenticated
fetch (not merely a headless-browser fingerprinting artifact). Remains
NOT_ENABLED: a working discovery mechanism cannot be built without
touching that protected surface, and this project never solves/bypasses
a CAPTCHA/Turnstile challenge.
"""

from source_adapter import (
    JobSourceAdapter,
    SearchQuery,
    AdapterStatus,
    AdapterNotEnabledError,
)


class WellfoundAdapter(JobSourceAdapter):
    name = "WELLFOUND"
    status = AdapterStatus.NOT_ENABLED
    capabilities = frozenset()

    def health_check(self):
        raise AdapterNotEnabledError(
            self.name,
            self.status,
            detail=(
                "Wellfound adapter is a registered skeleton only -- not yet "
                "implemented. No live request was made."
            ),
        )

    def search(self, query: SearchQuery):
        raise AdapterNotEnabledError(
            self.name,
            self.status,
            detail=(
                "Wellfound adapter is a registered skeleton only -- not yet "
                "implemented. No live request was made."
            ),
        )
