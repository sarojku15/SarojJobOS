---
name: company-research
description: Run and review persisted, source-backed research about a company behind a JobOS job listing, using the real company-research pipeline (scripts/company_research.py) — reuses the existing multi-provider search layer, never a second web-search implementation, never scoring/matching input. Use when the user asks to research a company, refresh existing research, or review what was previously found — never as an automatic step in discovery/scoring.
---

# Company Research

Wraps the real, persisted company-research pipeline
(`scripts/company_research.py`, via
`POST /api/candidates/{id}/company-research`). This reuses the SAME
multi-provider search layer already used for the 7 search-provider-only
job boards (`scripts/search_provider_manager.py` — You/Tavily/Exa/
Brave/Serper) — there is no second, separate web-search implementation
for this feature, and it never feeds scoring/matching (`score_job.py`
only ever reads the job's own JD text and the candidate's profile).

**Every research call is honest by construction.** `status` is always
one of:
- `NOT_ATTEMPTED` — no search-provider API key is configured at all.
  Tell the user plainly that a key must be added on the Search
  Providers settings page (`/settings/search-providers`) before this
  can produce a real result.
- `FAILED` — a search was attempted but every configured provider
  failed (real error message is persisted).
- `PARTIAL` — a search succeeded but no official site/description
  could be confidently identified from the results.
- `SUCCESS` — a website and/or description were extracted, each with
  a real source URL retained alongside it.

A field is never populated without its own supporting `source_urls`
entry, and a company with no confident result is never filled in with
a plausible-sounding guess.

## When to use

- "Research this company." / "What do you know about Acme Corp for
  this job?"
- "Refresh the research on this company" (a new call always creates a
  new, additive version — never overwrites the previous one).
- "Show me the research you already have on this company."

## When not to use

- As an automatic step in job discovery, matching, or scoring — it
  isn't wired into either, and must not become an input to `score_job`
  without a real, explicit decision to do so.
- Presenting anything as fact without checking the persisted
  `source_urls` — if the record's `status` is `NOT_ATTEMPTED` or
  `FAILED`, say so plainly rather than answering from general/training
  knowledge as if it were freshly researched.

## Inputs

`candidate_id`, `company_name` (usually from a job result the user is
looking at), and optionally `job_id` to associate the research with a
specific job.

## Workflow

1. `POST /api/candidates/{id}/company-research` with
   `{"company_name": ..., "job_id": ...}` — always returns 200 with a
   real `status`, even when no provider is configured (`NOT_ATTEMPTED`
   is not an error).
2. Report the real `status`, `website`/`description` (each paired with
   its source URL), `recent_info` entries (each with their own URL),
   and `version` from the response — never paraphrase a `NOT_ATTEMPTED`
   or `FAILED` result into something that sounds like real findings.
3. To review history: `GET /api/candidates/{id}/company-research?company_name=...`
   lists every version (newest first) — each version's outcome is
   preserved, never overwritten.
4. To look up one specific record:
   `GET /api/candidates/{id}/company-research/{company_research_id}`.

## APIs / Tools

`POST /api/candidates/{id}/company-research`,
`GET /api/candidates/{id}/company-research`,
`GET /api/candidates/{id}/company-research/{id}`.

## Safety

- Never presents a fact without its persisted source URL.
- Never fabricates a fallback company fact (industry, size, funding,
  etc.) when the search results don't confidently support it — those
  fields are left `null` by the pipeline itself; do not fill them in
  from general knowledge and present it as researched.
- Never uses a research result as scoring/matching input.
- Never claims research succeeded when `status` is `NOT_ATTEMPTED` or
  `FAILED` — explain the real reason (no provider configured, or every
  provider failed) instead.

## Output

The real, persisted research record — `status`, `website`,
`description` (with its source), `recent_info` (each with its own
source), `version`, and `researched_at` — or a plain explanation that
no search-provider key is configured yet.

## Examples

- "Tell me about this company before I apply." → run/fetch real
  research, present only what's persisted with its sources, and the
  honest `status` if nothing confident was found.
- "Is there a search provider configured?" → check
  `GET /api/settings/search-providers`; if not, tell the user research
  will return `NOT_ATTEMPTED` until one is added.
