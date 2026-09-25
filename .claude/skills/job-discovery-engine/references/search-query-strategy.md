# Search Query Strategy (Phase K)

Deterministic, bounded query construction -- the same for every
profession. Implemented in `scripts/discover_jobs.py`'s
`build_query_plan()`.

## For adapter/provider sources (Naukri, Greenhouse, Lever, Ashby, ...)

Unchanged: one `SearchQuery(role, location, ...)` per (title x
location) pair, exactly as `query_planner.build_queries_from_search_profile()`
already does, capped at `DEFAULT_MAX_QUERIES_PER_SUBMISSION` (50). This
Skill does not re-implement this -- it calls the existing function for
these sources.

## For Web Search discovery specifically

A free-text search engine benefits from several phrasings per
(title x location) pair, but an unbounded cross-product explodes fast.
Bounded combinations, generated in this fixed order, capped at
`web_search_discovery_adapter.MAX_QUERIES_PER_SEARCH` (8) per
(title, location) pair:

1. `TITLE + LOCATION` -- e.g. `"Senior Cloud Engineer" Bangalore`
2. `TITLE + LOCATION + top 2 skills` -- e.g. `"Senior Cloud Engineer" Bangalore AWS Terraform`
3. `TITLE + REMOTE` -- only if `"REMOTE"` is in the search's work_model list
4. `TITLE + LOCATION + experience range` -- e.g. `"Senior Cloud Engineer" Bangalore 8-12 years`
5. Up to 2 alternate title phrasings (from the search's own `titles`
   list, if more than one was supplied by the candidate) x LOCATION

No title/skill/location is ever invented here -- every token comes
directly from the SearchCriteria the candidate supplied. If fewer than
5 combinations are possible (e.g. no skills given, only one title), the
plan is simply shorter -- never padded with invented terms.

## Cross-query dedup before expensive processing

`discover_jobs.py` deduplicates raw results by `(source, job_url)`
*within a single discovery call*, before handing off to
`scripts/validate_discovered_job.py` / the existing pipeline, so the
same job returned by two overlapping query phrasings is not validated
and normalized twice. This is separate from, and does not replace,
`cross_source_dedup.py`'s cross-SOURCE duplicate detection downstream.

## Pagination

No source in this project currently declares
`AdapterCapability.PAGINATION` other than Naukri's own existing,
unmodified pagination handling. This Skill does not add pagination
logic of its own for any source -- see
`scripts/source_capabilities.py`'s `supports_pagination` field per
source.
