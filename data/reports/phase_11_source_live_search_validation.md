# Controlled Live Validation — Normal Search, All 11 Logical Sources

One real `submit_search()` submission (the actual "normal search" path —
never a manual per-adapter call), role="Senior Site Reliability Engineer",
location="Bengaluru", candidate experience=11.0, explicit `sources=` naming
all 11 real registry keys (never `sources=None`). Isolated temp DB only.
Production DB confirmed byte-identical before and after (SHA256
`174fc2e7...c87dc8`, 114688 bytes). Raw JSON: `phase_11_source_live_search_validation.json`.

This validation ran *after* the root-cause fix (see CLAUDE.md's "IMPORTANT
gotcha" note under Source Adapter Principle) — before that fix, this exact
script would have shown all 7 `*_SEARCH` sources as `NOT_ENABLED` and
`submit_search()` would have silently dropped them from the plan.

## The mandatory diagnostic table

| SOURCE | BOARD | SELECTED | PLANNED | QUERIES | ATTEMPTED | HEALTH | RAW* | ELIGIBLE | DISPLAYED |
|---|---|---|---|---|---|---|---:|---:|---:|
| NAUKRI | NAUKRI | True | True | 1 | YES | reachable, succeeded=1, failed=0 | 21 | 5 | 5 |
| HIRIST | HIRIST | True | True | 1 | YES | reachable, succeeded=1, failed=0 | 10 | 7 | 7 |
| IIMJOBS | IIMJOBS | True | True | 1 | YES | reachable, succeeded=1, failed=0 | 4 | 3 | 3 |
| APNA | APNA | True | True | 1 | YES | reachable, succeeded=1, failed=0 | 75 | 69 | 69 |
| LINKEDIN_SEARCH | LINKEDIN | True | True | 1 | YES | reachable, succeeded=1, failed=0 | 6 | 6 | 6 |
| INDEED_SEARCH | INDEED | True | True | 1 | YES | reachable, succeeded=1, failed=0 | 0 | 0 | 0 |
| FOUNDIT_SEARCH | FOUNDIT | True | True | 1 | YES | reachable, succeeded=1, failed=0 | 0 | 0 | 0 |
| INSTAHYRE_SEARCH | INSTAHYRE | True | True | 1 | YES | reachable, succeeded=1, failed=0 | 10 | 10 | 10 |
| CUTSHORT_SEARCH | CUTSHORT | True | True | 1 | YES | reachable, succeeded=1, failed=0 | 0 | 0 | 0 |
| WELLFOUND_SEARCH | WELLFOUND | True | True | 1 | YES | reachable, succeeded=1, failed=0 | 10 | 10 | 10 |
| SHINE_SEARCH | SHINE | True | True | 1 | YES | reachable, succeeded=1, failed=0 | 2 | 2 | 2 |

\* RAW = jobs actually persisted to the `jobs` table for that board (post
in-batch dedup). `WorkItemResult.raw_count=170` is the pipeline-wide
pre-dedup total across all 11 sources combined.

`WorkItemResult`: `status=COMPLETED`, `matches_created=112`,
`unimplemented_sources=[]`, `blocked_sources=[]`.

## What this proves

- **Every one of the 11 sources was genuinely attempted** — not just
  "available." `ATTEMPTED=YES` with a real `health_check()` result and
  exactly 1 real query for every source, direct and provider-backed alike.
- **8 of 11 sources returned real jobs**: Naukri, Hirist, iimjobs, Apna
  (direct) and LinkedIn, Instahyre, Wellfound, Shine (via the search
  provider). **3 returned genuinely zero results** (Indeed, Foundit,
  Cutshort) — reported honestly as zero, not fabricated, not silently
  dropped, not confused with "not searched."
- **ELIGIBLE == DISPLAYED for every source** — nothing that passed
  eligibility got lost between the DB and the API-shaped results.
- The active search provider for this run was **You** (first in
  `DEFAULT_PROVIDER_ORDER`, and the one with a real configured key that
  was tried first) — no unnecessary calls to Tavily/Exa/Brave/Serper.
- Production DB untouched throughout.

## Known limitation surfaced by this exercise

`search_runs` (the only place a completed run's stats persist after the
worker process exits) has never had per-source granularity — it stores
one aggregated row per *submission*, not per source. `WorkItemResult`'s
rich per-source detail (the exact table above) lives only in-memory during
one worker invocation and is discarded once that call returns; only
coarse aggregates (`jobs_discovered`, `blocked_queries` as a *count*, not
*which* sources) survive. This means the UI currently has no persisted way
to distinguish "this source was searched and returned zero" from "this
source was never searched" after the fact — both would look identical
once you're looking at yesterday's run days later. Producing this
session's diagnostic table required intercepting `discover_from_sources()`
directly; a production feature to expose this needs new schema work
(e.g. a `search_run_sources` table), which was not implemented this
session — see the final report for why.
