# Company Research

## Starting it

From a job's workspace page, confirm/enter the company name and click
**"Research Company."** This runs a real search through whichever
search-provider you've configured at Settings → Search Providers (see
[API_PROVIDER_SETUP.md](API_PROVIDER_SETUP.md)) — it reuses the exact
same multi-provider search layer the job-discovery sources use, never
a second, separate implementation.

## What it actually gathers

Whatever a real web search surfaces about the company (e.g. a
description, size, industry) — nothing beyond what a real source
returned. **Every fact is paired with the source URL it came from** —
a fact is never shown without its source link.

## Honest status values

| Status | Meaning |
|---|---|
| `NOT_ATTEMPTED` | No provider key configured — research was never even attempted. |
| `FAILED` | Every configured provider errored. |
| `PARTIAL` | A search ran, but nothing was confidently identified. |
| `SUCCESS` | Real information found, with sources. |

These are never fabricated — a `FAILED` or `PARTIAL` result is shown
as such, never silently upgraded to look like success.

## Persistence

Every research attempt creates a new, version-numbered record (`GET
.../company-research`) — re-running research for the same company
doesn't overwrite the previous attempt.

## Relationship to a job/application

Research is attached to the job it was run from. Use it before
deciding whether to shortlist/approve, and it's one of the optional
inputs to [Interview Preparation](INTERVIEW_PREP.md) (pick an existing
research record when generating prep for the same job).

## What's not implemented

No automatic, scheduled, or background research — it only ever runs
when you click the button for a specific job.
