# Search

## Creating a search

`/searches/new`. Fields (from the actual form, `web/search_new.html`):

| Field | What it does |
|---|---|
| Search name | Just a label for you. |
| Job title / keywords (comma-separated) | What you're looking for. |
| Locations (comma-separated) | Where you'll accept a role. |
| Min / Max experience (years) | Eligibility filter. |
| Maximum job age (days) | Freshness cutoff. |
| Min / Max salary + Currency | Salary filter, if the job states one. |
| Work model (comma-separated: Remote, Hybrid, Onsite) | Filter. |
| Skills / criteria (comma-separated) | Feeds scoring/eligibility. |
| Employment type | Filter. |
| Minimum match score (0-100) | Jobs scoring below this are excluded entirely — see [MATCHING.md](MATCHING.md). |
| Sources | Which of the currently-available sources to use (see [JOB_SOURCES.md](JOB_SOURCES.md) for what's real right now). |
| Resume/Profile version | Optionally pin to a past resume instead of your current one. |

## Running it

Click **"Run now"** (or `POST /api/searches/{id}/run`). This starts in
the background and returns immediately with a `run_id` — the page
polls `GET /api/runs/{run_id}` until the run reaches a terminal state.

## What happens during a run

```
Query planning (role x location x source matrix)
   ↓
Source execution (each selected source, independently)
   ↓
Deduplication (in-batch + against everything you've seen before)
   ↓
Freshness classification (date-based)
   ↓
Eligibility gate (experience + location)
   ↓
Mandatory-skill gate
   ↓
100-point scoring (see MATCHING.md)
   ↓
Results
```

## Source audit statuses

Every source you selected gets one honest status per run, visible on
the results page:

| Status | Meaning |
|---|---|
| `SUCCESS` | Ran, returned real results. |
| `ZERO` | Ran successfully, genuinely found nothing — never confused with a failure. |
| `FAILED` | Attempted, errored (network, parsing, blocked, etc.). |
| `BLOCKED` | The source itself refused automated access. |
| `NOT_CONFIGURED` | A provider-backed source with no API key configured for any provider. |
| `NOT_ATTEMPTED` | Not selected for this search, or skipped for a structural reason. |

A run where some sources succeed and others fail/are-zero is a
**partial run** — it is not treated as a whole-run failure; you still
get the results from whichever sources did work, with the audit
telling you exactly which didn't and why.

## "Why did I get fewer jobs than expected?"

Check the source audit first — a `ZERO`/`FAILED`/`NOT_CONFIGURED`
source is the most common cause. Next, check your minimum match score
and mandatory skills — a tight filter legitimately excludes more jobs.

## "Why did a source fail?"

Click into the run's source audit for the specific reason recorded for
that source (a real error message, not a generic "something went
wrong"). [JOB_SOURCES.md](JOB_SOURCES.md) documents each source's known
failure modes (robots.txt blocks, etc.).

## "Why did a job receive a low score?"

Open the job for its full breakdown — see
[MATCHING.md](MATCHING.md)'s "Gaps" explanation. A low score almost
always traces to specific missing skills/criteria in your own profile
relative to what the job asked for, visible in that breakdown.

## Run history

Every past run for a search remains retrievable — a new run never
erases a previous one's record.

## Editing / deleting a search

`PUT`/`PATCH`/`DELETE` on `/api/searches/{id}` (or the Edit page). No
explicit "cancel an in-progress run" action currently exists — a run
in progress completes on its own; there is no mid-run cancel/abort.
