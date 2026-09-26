---
name: job-report
description: Generate or explain a JobOS Excel export (per-search or candidate-wide) for the current candidate — what columns it contains, how it's scoped, and where the file lands. Use when the user asks to download, generate, or export a report, or asks what fields an export contains.
---

# Job Report

Wraps the real Excel export (`scripts/generate_run_report.py`,
served via `GET /api/searches/{search_id}/report` or
`GET /api/candidates/{id}/report`) — never generates report content
itself.

## When to use

- "Generate today's job-search report."
- "Download the Excel report for this search."
- "What columns does the export include?"

## When not to use

- Explaining a specific score/gap in conversation (→ job-matching —
  the export's columns mirror the same real data, but for a quick
  answer read the results API directly instead of generating a file).

## Inputs

`candidate_id`, and optionally a `search_id` (for a report scoped to
exactly that search/run) — omit `search_id` for the older, broader
candidate-wide report.

## Workflow

1. Per-search (recommended — scoped to exactly what the user is
   looking at): `GET /api/searches/{search_id}/report?candidate_id={id}`.
2. Candidate-wide (every job ever discovered across all searches):
   `GET /api/candidates/{id}/report`.
3. The response is the `.xlsx` file itself — save it to disk and tell
   the user the exact path. Do not describe its contents from memory;
   if asked what's inside, either open it (`openpyxl`) or point to the
   real column list below.

## Real export columns (as of this writing — verify against the
current `_JOB_SHEET_COLUMNS` in `scripts/generate_run_report.py` if in
doubt)

Priority, Score, Title, Company, Location, Source, Job URL,
Application URL, Experience Required, Freshness (+ age in days),
Eligible, Report Status, Matched Skills/Strong Points, Gaps,
Exclusion/Gap Reason, Application Status, Previously Seen, Duplicate
flag, First Discovered, Last Seen, Resume Variant, Discovered Via,
Discovery Query, Completeness, Requirement Type, Job ID, Work Model,
JD Text, Search ID, Search Run ID.

**Genuinely not persisted anywhere in the pipeline — never fabricate
these**: salary, employment type. If asked for them, say plainly that
no source adapter currently populates them.

Search ID / Search Run ID are blank for the candidate-wide report (no
single search/run to attribute every row to) and populated only for a
search-scoped export.

## APIs / Tools

`GET /api/searches/{search_id}/report?candidate_id={id}`,
`GET /api/candidates/{id}/report`.

## Safety

- Never fabricates a column value the export doesn't actually contain.
- Never claims the export contains salary/employment-type data.
- The exported job count for a search-scoped report always equals that
  search's own results count — if you ever see a mismatch, report it
  as a real bug rather than silently reconciling it.

## Output

The saved file's path, and (if asked) an accurate description of its
sheets (`ALL_MATCHING_JOBS`, `REJECTED_EXCLUDED`, `ALREADY_APPLIED`,
`RUN_SUMMARY`) and columns from the list above.

## Examples

- "Generate today's job-search report." → candidate-wide or most-recent
  search's report, per what the user means; confirm which if ambiguous.
- "Download the Excel report for this search." → the per-search
  endpoint, scoped to that search_id.
