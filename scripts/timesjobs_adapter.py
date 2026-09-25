#!/usr/bin/env python3

"""
TimesJobs adapter -- OFFLINE SKELETON ONLY (Phase 4 multi-source adapter
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
happened yet for TimesJobs. `capabilities` is deliberately empty until
real implementation work begins; nothing here should be read as a
promise about what TimesJobs will or won't support once implemented.

Do not invent selectors, APIs, endpoints, cookies, headers, anti-bot
workarounds, or scraping behavior for this source.
"""

from source_adapter import (
    JobSourceAdapter,
    SearchQuery,
    AdapterStatus,
    AdapterNotEnabledError,
)


class TimesJobsAdapter(JobSourceAdapter):
    name = "TIMESJOBS"
    status = AdapterStatus.NOT_ENABLED
    capabilities = frozenset()

    def health_check(self):
        raise AdapterNotEnabledError(
            self.name,
            self.status,
            detail=(
                "TimesJobs adapter is a registered skeleton only -- not "
                "yet implemented. No live request was made."
            ),
        )

    def search(self, query: SearchQuery):
        raise AdapterNotEnabledError(
            self.name,
            self.status,
            detail=(
                "TimesJobs adapter is a registered skeleton only -- not "
                "yet implemented. No live request was made."
            ),
        )
