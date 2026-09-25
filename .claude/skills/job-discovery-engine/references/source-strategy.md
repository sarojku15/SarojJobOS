# Source Strategy

Each source uses exactly one of four acquisition methods. This mapping
is declared, source by source, in `scripts/source_capabilities.py` --
this document explains the four methods themselves.

## 1. `adapter`

A class implementing `source_adapter.JobSourceAdapter`, registered in
`source_registry.ADAPTERS`. Dispatched via
`source_registry.discover_from_sources()`. Today: `NAUKRI` (ENABLED),
`MOCK` (ENABLED, dev/test only).

## 2. `provider` (ATS / career-page / official API)

Also a `JobSourceAdapter` subclass, registered the same way -- the
distinction from (1) is purely about WHAT it queries: a single
company's board (Greenhouse/Lever/Ashby) or an authorized third-party
job-data API, rather than a general job-search engine. Because a
career-page board has no free-text search endpoint, these adapters
fetch a board's full open-jobs list and filter client-side by
`ats_common.role_matches()` -- see `scripts/greenhouse_adapter.py`,
`scripts/lever_adapter.py`, `scripts/ashby_adapter.py`. Configured via
`config/career_pages.json` (empty by default -- no company is
hardcoded). All three are real, working implementations kept
`NOT_ENABLED` pending this project's phased live-validation process.

## 3. `web_search_discovery`

`scripts/web_search_discovery_adapter.py`. See that module's docstring
for the exact "why this can't run yet in this backend process" boundary.
This Skill still builds the bounded query strings a web-search backend
would need (`search-query-strategy.md`) so the boundary is
configuration-only once a backend exists.

## 4. `web_search_discovery/provider/manual`

Sources this project has investigated and found to have no direct
adapter path today (LinkedIn: robots.txt disallow; Hirist: structurally
unreliable title/company extraction; the remaining Phase 4 skeletons:
simply not yet implemented). Each has an explicit `reason` in
`source_capabilities.py` sourced from this project's own prior
investigation reports (`data/reports/linkedin_phase5_inspection.md`,
the Phase 8 Hirist forensics report) -- never a fresh guess.

## Adding a new source

1. Implement a `JobSourceAdapter` subclass (see any existing skeleton
   for the shape). `status = AdapterStatus.NOT_ENABLED` until validated.
2. Register it in `source_registry.ADAPTERS`.
3. Add its entry to `source_capabilities._METADATA` (acquisition
   method + reason).
4. Write offline tests against fixture data (never live calls in
   tests).
5. Run this project's phased live-validation process (offline tests ->
   one live query -> controlled multi-query) before ever setting
   `status = AdapterStatus.ENABLED`.

No GUI, scoring, report, candidate-model, or search-model code needs to
change for a new source to become available -- `/api/sources` and the
search-creation form both read the live registry, not a hardcoded list.
