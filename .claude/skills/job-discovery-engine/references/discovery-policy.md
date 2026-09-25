# Discovery Policy

## Source selection order

For a given search's requested `sources` list (or, if omitted, every
currently-enabled source):

1. Filter to sources that `source_capabilities.get_source_capability()`
   reports `enabled=True` for. A `NOT_ENABLED`/`REQUIRES_AUTH`/
   `BLOCKED`/`UNSUPPORTED` source is never queried, silently or
   otherwise -- it is reported back to the caller as unavailable, with
   its `reason`.
2. For each enabled source, dispatch via
   `source_registry.discover_from_sources()` -- the existing,
   unmodified mechanism. This already handles per-source
   health-checking, blocking, and timeout bookkeeping.
3. If a requested source is `WEB_SEARCH` and a backend has been
   registered (`web_search_discovery_adapter.set_web_search_backend()`),
   it participates exactly like any other adapter through the same
   `discover_from_sources()` call -- no separate code path.
4. If a requested source is `NOT_ENABLED`, it is never silently
   skipped -- callers (the API layer) must surface it under
   "Unavailable sources" with the reason, never omit it entirely from
   what the user sees requested.

## What this Skill will NEVER do

- Retry past a source's own block/timeout signal in a way that looks
  like anti-bot evasion (no rotating user agents to defeat a block, no
  CAPTCHA solving, no session/cookie theft).
- Treat a web-search result snippet as a complete, verified job
  description -- see `quality-rules.md`.
- Invent a job, company, URL, or field value not actually present in
  what a source returned.
- Run an unbounded number of queries -- see `search-query-strategy.md`
  for the hard caps.

## Escalation

If NO enabled source can service a request (e.g. only Naukri is
enabled and the candidate's target_locations are all outside India, or
`WEB_SEARCH` is the only viable mechanism and no backend is
configured), `discover_jobs.py` returns an explicit
`DiscoveryOutcome(jobs=[], unavailable_sources=[...])` rather than
silently returning zero results with no explanation -- the caller
(`api/search_store.py`) surfaces this to the run's status/results.
