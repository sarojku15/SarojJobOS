---
name: company-research
description: Best-effort, ad-hoc research about a company behind a JobOS job listing (what they do, size, funding, recent news) using general web search — NOT a built JobOS pipeline feature. No company data is stored, scored, or persisted anywhere in this project. Use only when the user explicitly asks about a company, never as part of scoring or matching.
---

# Company Research (Optional / Best-Effort)

**Honesty first**: unlike every other skill in this project, this one
is not backed by a tested JobOS pipeline component. There is no
company-data adapter, no cache, no scoring integration — it's a thin
wrapper around whatever general web-research tool is available to
Claude in this session (e.g. WebSearch), scoped to a specific company
name the user asks about.

## When to use

The user explicitly asks something like "tell me about this company"
or "what do you know about Akamai" in the context of a job they're
looking at.

## When not to use

- As an automatic step in job discovery, matching, or scoring — it
  isn't, and must not become one without a real, tested implementation
  and an explicit decision to add it (see `CLAUDE.md`'s working style:
  don't build ahead of an explicit request).
- If no web-research tool is available in the current session — say so
  plainly rather than answering from possibly-stale training data
  presented as current fact.

## Inputs

A company name (from a job result the user is looking at, or given
directly).

## Workflow

1. Confirm a web-research tool is actually available this session.
   If not, say so and stop — do not answer from memory as if it were
   researched just now.
2. Search for the company's own site, recent news, and (if relevant)
   review-aggregator pages the user names.
3. Summarize only what the search actually returned, with sources.
   Flag anything you're not confident about rather than smoothing it
   into a confident-sounding paragraph.

## APIs / Tools

Claude's general web-search tool (session-dependent — not a JobOS
script or API route). Nothing in `api/` or `scripts/` is called.

## Safety

- Never presents unverified or recalled information as freshly
  researched fact.
- Never uses this as an input to scoring/matching — those only ever
  read the job's own JD text and the candidate's own profile.
- Never stores results anywhere in the JobOS database.

## Output

A short, sourced summary — explicitly labeled as informal research,
not a JobOS data point.

## Examples

- "Tell me about this company before I apply." → ad-hoc web summary,
  clearly caveated as unverified/informal.
