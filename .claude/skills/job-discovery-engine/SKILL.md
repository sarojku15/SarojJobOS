---
name: job-discovery-engine
description: Generic, multi-source job discovery orchestration for SarojJobOS. Builds a bounded set of search queries from generic candidate/search criteria (any profession, not just SRE/DevOps), dispatches them across every currently-usable source (registered adapters today, ATS/career-page providers and Web Search discovery once configured), validates and normalizes the raw results into the existing CommonJob shape, and hands off to the EXISTING dedup/freshness/eligibility/scoring/report pipeline unchanged. Use this skill whenever the task is "find jobs matching this criteria" for any candidate -- never re-implement scoring, dedup, or freshness from scratch.
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
tested, already reused unchanged by the rest of this project (see
`data/reports/phase9_job_discovery_engine.md` for how this Skill wires
into that pipeline via `api/search_store.py` / `scripts/search_worker.py`).

## When to use this Skill

Any time you need to find jobs matching a candidate's criteria --
regardless of the candidate's profession, target role, or location.
The criteria are always the generic shape in
`references/job-schema.md`'s "SearchCriteria" section, never assumed to
be SRE/DevOps-specific.

## How to run it

```
.venv/bin/python3 .claude/skills/job-discovery-engine/scripts/discover_jobs.py --criteria-json '<criteria>'
```

or, from Python, import `discover_jobs.discover(criteria)` directly.
See `references/discovery-policy.md` for the full source-selection
policy this applies before making any call.

## Source hierarchy (see `references/source-strategy.md` for detail)

1. **Existing, ENABLED adapters** (today: Naukri only) --
   `scripts/source_registry.discover_from_sources()`, unchanged.
2. **Official APIs / authorized integrations** -- none configured in
   this environment today; the provider interface exists
   (`scripts/greenhouse_adapter.py`, `scripts/lever_adapter.py`,
   `scripts/ashby_adapter.py`) so adding a credential/board is
   configuration, not a redesign.
3. **Public ATS/career-page providers** -- same three modules; real,
   working code against each platform's public JSON API, kept
   `NOT_ENABLED` until a company board is configured AND this
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
