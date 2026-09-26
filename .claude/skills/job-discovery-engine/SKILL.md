---
name: job-discovery-engine
description: Standalone, ad-hoc, NON-PERSISTING job discovery script (scripts/discover_jobs.py) that queries every enabled source for generic criteria and returns raw normalized jobs -- it does not score, deduplicate cross-source, or write to the database. Use ONLY for one-off/exploratory "just show me what's out there" requests that don't need to be saved or tracked. For a real JobOS candidate's tracked, scored, dashboard-visible search, use jobos-orchestrator's direct API calls (POST /api/candidates/{id}/searches + /run) instead -- this skill's output never appears there.
---

# Job Discovery Engine

This Skill is a thin **orchestration** layer. It owns exactly three
responsibilities and nothing else:

1. Turn generic search criteria into a bounded, deterministic list of
   `(source, SearchQuery)` pairs (`references/search-query-strategy.md`).
2. Dispatch those queries to whichever acquisition mechanism each
   source actually uses today (`references/source-strategy.md`).
3. Validate and normalize whatever comes back into the project's
   existing CommonJob shape (`references/job-schema.md`,
   `references/quality-rules.md`) before handing off.

**It does not score, deduplicate, classify freshness, or generate
reports.** Those are `scripts/score_job.py`, `scripts/canonical_job.py`
/ `scripts/cross_source_dedup.py`, `scripts/freshness.py`, and
`scripts/generate_run_report.py` respectively -- already built, already
tested. **Correction (caught on review): this Skill's own
`discover_jobs.py` does NOT call into or get called by
`scripts/search_worker.py`** -- they are two separate code paths that
happen to share the same underlying `scripts/source_registry.
discover_from_sources()` and adapter layer. `data/reports/
phase9_job_discovery_engine.md` documents API/schema work done during
the same project phase this Skill was built in, not a code-level import
between the two. See "When NOT to use this Skill" below.

## When to use this Skill

**Only** for an ad-hoc, non-persisted "what's out there" look — the
results are never scored, never deduplicated across sources, and never
written to the database, so they will not appear on any candidate's
`/dashboard` or `/searches/{id}/results` page. The criteria are always
the generic shape in `references/job-schema.md`'s "SearchCriteria"
section, never assumed to be SRE/DevOps-specific.

## When NOT to use this Skill

For a real candidate's actual search — "find me jobs matching my
profile," anything that should show up in their results/dashboard/
export — use `jobos-orchestrator`'s direct calls to
`POST /api/candidates/{id}/searches` and
`POST /api/searches/{id}/run` instead (`scripts/search_worker.py`).
That path and this Skill's `discover_jobs.py` both independently call
the same underlying `scripts/source_registry.discover_from_sources()`,
but only the API path scores, persists, and dedups the results — this
Skill's script does not, and nothing here writes to it automatically.
They are two separate code paths today, not one pipeline with two
entry points.

## How to run it

```
.venv/bin/python3 .claude/skills/job-discovery-engine/scripts/discover_jobs.py --criteria-json '<criteria>'
```

or, from Python, import `discover_jobs.discover(criteria)` directly.
See `references/discovery-policy.md` for the full source-selection
policy this applies before making any call.

## Source hierarchy (see `references/source-strategy.md` for detail,
and `docs/JOB_SOURCES.md` for the full current status table)

1. **Existing, ENABLED direct adapters** (currently: `NAUKRI`,
   `HIRIST`, `IIMJOBS`, `APNA`) --
   `scripts/source_registry.discover_from_sources()`, unchanged.
2. **Search-provider-backed sources** (currently: `LINKEDIN`, `INDEED`,
   `FOUNDIT`, `INSTAHYRE`, `CUTSHORT`, `WELLFOUND`, `SHINE`) --
   `ENABLED` only once at least one provider API key
   (`YOU_API_KEY`/`TAVILY_API_KEY`/`EXA_API_KEY`/`BRAVE_API_KEY`/
   `SERPER_API_KEY`) is configured; discovered through that provider's
   own search API, never a direct crawler for these boards.
3. **Public ATS/career-page providers** -- `scripts/greenhouse_adapter.py`,
   `lever_adapter.py`, `ashby_adapter.py`; real, working code against
   each platform's public JSON API, kept `NOT_ENABLED` until a company
   board is configured in `config/career_pages.json` AND this
   project's phased live-validation process is actually run.
4. **Web Search discovery** -- `scripts/web_search_discovery_adapter.py`.
   **This backend process cannot call Claude Code's own WebSearch tool
   directly** -- see that module's docstring for the exact boundary.
   This Skill still builds the bounded, source-specific search-query
   strings a web-search backend would need
   (`references/search-query-strategy.md`), so that once a backend is
   wired in (`set_web_search_backend()`), no further redesign is
   needed.

Every source's actual availability is read from
`scripts/source_capabilities.py` (never hardcoded here) --
`enabled=false` sources are surfaced to the GUI as "Unavailable
sources" with their `reason`, never silently dropped.

## Non-negotiable constraints (inherited from CLAUDE.md, restated here for this Skill)

- Never bypass CAPTCHA, robots.txt, login walls, anti-bot systems, or
  rate limits.
- Never fabricate a job, URL, salary, experience range, or posted date.
  Unavailable = `null`/`"UNKNOWN"`, never guessed.
- Never submit an application. This Skill's output ends at "here are
  candidate jobs" -- nothing downstream of it may click Apply.
- Never hardcode a profession, skill, location, or salary figure as a
  product default.
