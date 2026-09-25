# Naukri 5-Query Controlled Live Validation — UA Configuration + 3-Day Freshness

**Objective:** validate that the working Naukri production path (validated
headless User-Agent configuration, genuinely headless, `channel='chromium'`)
remains reliable across five controlled queries, and empirically confirm
whether the native `jobAge=3` freshness filter actually constrains the
returned listings.

**Result: SUCCESS — all five queries succeeded.** HTTP 200 on all 106
requests made. Classifier reached `VALID_RESULTS` for all five search
pages. `jobAge=3` is now **empirically validated by returned listings**,
not just by URL/UI inspection. Production DB is byte-identical before and
after. No retries.

## Execution Method

Real production path via `search_worker.run_once()` — one `search_run`
containing exactly five query entries, processed in a single
`process_queue_item()` call (the same multi-query-per-source batching
`discover_from_sources()` already performs unmodified: one
`health_check()`, then `adapter.search()` once per query, in order).

Two runtime-only observation wrappers were installed in this validation
process (never touching any file on disk) to capture per-query detail
that `WorkItemResult` only aggregates: a pass-through wrapper around
`naukri_adapter.classify_search_page` and around
`NaukriAdapter.search` — both call the real, unmodified function/method
and return/raise its exact result; they exist only to record what already
happened, in call order. This is the same technique used in every earlier
diagnostic task in this project's Naukri investigation.

The temporary validation DB was created by copying
`data/applications/jobos.db` (read of production, write only to a new
temp file) so the real candidate/profile rows were reused as-is.

`JOBOS_BROWSER_HEADLESS` and `JOBOS_NAUKRI_USER_AGENT` were both
explicitly unset before the run, guaranteeing true defaults (headless,
validated default UA) — **the implementation from the prior task was not
modified.**

## Pre-Flight

| Check | Result |
|---|---|
| Production DB SHA-256 | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production row counts | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| Candidate | `saroj`, ACTIVE |

## FACTS OBSERVED — Per-Query Results

All five queries used `max_job_age_days=3`, the real `_build_search_url()`
production function, and were executed with **zero retries**.

| # | Location | Search URL (as sent) | Final URL | HTTP Status | Classifier | Raw jobs | Elapsed |
|---|---|---|---|---|---|---|---|
| 1 | Bangalore | `.../infrastructure-engineer-jobs-in-bangalore?jobAge=3` | identical (no redirect) | 200 | VALID_RESULTS | 20 | 220.7s |
| 2 | Bengaluru | `.../infrastructure-engineer-jobs-in-bengaluru?jobAge=3` | identical (no redirect) | 200 | VALID_RESULTS | 20 | 213.0s |
| 3 | Chennai | `.../infrastructure-engineer-jobs-in-chennai?jobAge=3` | identical (no redirect) | 200 | VALID_RESULTS | 20 | 209.8s |
| 4 | Hyderabad | `.../infrastructure-engineer-jobs-in-hyderabad?jobAge=3` | **redirected to** `.../infrastructure-engineer-jobs-in-hyderabad-secunderabad?jobAge=3` | 200 | VALID_RESULTS | 20 | 208.6s |
| 5 | Pune | `.../infrastructure-engineer-jobs-in-pune?jobAge=3` | identical (no redirect) | 200 | VALID_RESULTS | 20 | 207.2s |

**Every one of the 5 queries returned exactly 20 raw jobs, HTTP 200, and
classifier state `VALID_RESULTS`. No query was BLOCKED, SOFT_BLOCK, or
PARSE_FAILURE. Matched block phrase: none, for any query.**

**Note on the Hyderabad redirect (FACT/OBSERVED):** Naukri's server
redirected `jobs-in-hyderabad` to `jobs-in-hyderabad-secunderabad` (its
own metro-area URL normalization) — the classifier still correctly
reached `VALID_RESULTS` with 20 job links on the redirected page. This is
existing, unmodified redirect-following behavior in
`naukri_fetch_bridge.js`'s `page.goto()` call; no code change was made or
needed.

Detail-page fetches: 20 per query × 5 queries = 100, all confirmed HTTP
200 via the stderr diagnostic stream (106 total `DIAGNOSTIC` lines: 1
health check + 5 search + 100 detail = 106, matching exactly).

## Malformed Job — Existing, Documented Fallback Behavior (Not a Defect)

One raw job (from the batch, index 3) was missing a required `company`
field and was skipped by `discover_local.normalize_job()` — this is
existing, documented behavior ("one malformed job is skipped, not
fatal"), not a new defect introduced by this task or by the UA change.
It reduced `normalized_count` to 99 (from `raw_count` 100) and is recorded
in `errors_count=2` (`search_runs` also counts the 1 malformed job in its
`errors_count` alongside the eligibility-loop's per-job error handling
convention). **No production code was changed in response to this — it
is the pipeline working exactly as designed against one real-world
Naukri detail page whose HTML apparently omitted a company name.**

## jobAge=3 — Now EMPIRICALLY VALIDATED

This is the key open question this validation was designed to answer:
*does the native `jobAge=3` URL filter actually constrain the returned
listings, or was it previously only confirmed as a URL parameter that
Naukri might silently ignore?*

**FACT/OBSERVED, directly computed by the unmodified production
`freshness.py` + `job_ranking.py` (post-retrieval classification, run
independently of the discovery-time filter):**

| | Value |
|---|---|
| Unique jobs analyzed (all 72, not just eligible/scored) | 72 |
| `freshness_age_days` range across all 72 | **1 to 3** |
| Jobs exceeding 3 days (`jobs_exceeding_max_age`) | **0 of 72** |
| Freshness label distribution | HOT (≤~2 days): 66, FRESH (3-7 days band): 6 |

**Zero of the 72 unique jobs returned by the `jobAge=3`-filtered search
pages have a post-retrieval-computed age exceeding 3 days.** This is
strong, direct evidence (not merely UI/URL inspection) that Naukri's
native `jobAge=3` filter is genuinely constraining server-side results to
recently-posted jobs — the first successful, listings-based confirmation
of this filter in this project (every prior attempt to test it was
blocked before the UA fix).

**Distinguishing discovery-time vs. post-retrieval freshness (as
requested):**
- **Discovery-time freshness** = the `?jobAge=3` URL parameter sent to
  Naukri, asking its own server to only return jobs posted within 3 days
  — this is what actually reduces the *number* of jobs Naukri returns in
  the first place.
- **Post-retrieval freshness** = `freshness.py`'s independent
  `classify_freshness()` parsing each returned job's own `posted_date`
  text (e.g., "Posted: 2 days ago") *after* the fact, entirely unaware of
  what URL parameter was used to fetch the page.
- These two remained architecturally separate throughout this validation
  (exactly as designed) — the post-retrieval classification's results
  (all ≤3 days) simply **corroborate** that the discovery-time filter
  worked, rather than being the mechanism that enforced it.

**Remaining uncertainty (INTERPRETATION):** this is one successful
5-query trial. It does not prove Naukri's `jobAge=3` filter behaves
identically for every role/location combination, at every time of day, or
indefinitely into the future — only that it worked, verifiably, for these
five location queries at this run.

## Aggregate Totals (via the unmodified production pipeline)

| Metric | Value |
|---|---|
| Total raw jobs | 100 |
| Malformed (skipped) | 1 |
| Total normalized jobs | 99 |
| Duplicate count (production `deduplicate()`, post-normalization) | 27 |
| Total unique jobs | 72 |
| Excluded — experience | 57 |
| Excluded — location | 0 |
| Total eligible | 15 |
| Total scored | 15 |
| Ready-for-approval (score ≥ 70, per priority gate) | 3 |
| Total candidate matches created | 15 |
| Score range (eligible/scored jobs) | **13 – 95** |
| Priority distribution (scored jobs) | A: 2, B: 1, C: 3, REJECT: 9 |
| Jobs exceeding 3-day freshness | **0 of 72** |
| Timed-out queries | 0 |
| Succeeded queries | 5 of 5 |
| Total HTTP requests to Naukri | **106** (1 health + 5 search + 100 detail) |
| Total errors | 1 (the single malformed-job skip, above) |
| Retries | **0** |

## Priority-Ranked Scored Jobs (this run)

| Job ID | Company | Title | Score | Priority | Age (days) |
|---|---|---|---|---|---|
| NAUKRI-4f7bcd41148439c4 | Tenarai Technologies | AWS Cloud Infra and DevOps Engineer | 95 | A | 2 |
| NAUKRI-795afb8054ffa58b | Robosoft Technologies | Lead DevOps Engineer | 90 | A | 1 |
| NAUKRI-8336f2b424fcc7fc | Head Hunters | Infrastructure Engineer Lead | 85 | B | 2 |
| NAUKRI-71298a7982adfb9c | RBS Lynk | Senior DevOps Engineer (AWS, Terraform, Kubernetes) | 75 | C | 1 |
| NAUKRI-3b51661484622081 | Brose | Senior Cloud Platform Engineer (AWS & Terraform) | 75 | C | 2 |
| NAUKRI-394df19a920df78f | Conduent | Infrastructure Systems Engineer III | 70 | C | 2 |
| (9 more) | — | — | 13–65 | REJECT | 1–3 |

## Cross-Query Duplicate Analysis

Two distinct, both-valid views of duplication are reported:

1. **Production pipeline's own dedup (authoritative, post-normalization,
   the number that actually governs what lands in the DB):** 27
   duplicates removed from 99 normalized jobs → 72 unique.
2. **This validation's own raw pre-normalization cross-query URL overlap
   analysis** (observational only, computed from the 5 per-query raw
   job-URL lists before normalization/dedup ran): 23 of 100 raw job URLs
   appeared in more than one of the 5 queries' results. Breakdown by
   overlapping location set:

   | Location overlap | Count |
   |---|---|
   | Bangalore + Bengaluru | 17 |
   | Hyderabad + Pune | 2 |
   | Chennai + Hyderabad | 1 |
   | Bangalore + Bengaluru + Hyderabad | 1 |
   | Bangalore + Bengaluru + Chennai + Hyderabad | 1 |
   | Bangalore + Bengaluru + Chennai + Pune | 1 |

   As expected, the overwhelming majority of cross-query duplicates are
   the "Bangalore" / "Bengaluru" spelling-variant pair for the same city —
   confirms the production dedup logic is correctly collapsing the same
   real-world job posted under two different location-query slugs into
   one canonical entry.

The two numbers (27 vs. 23) are not expected to match exactly: one counts
post-normalization duplicate pairs removed from 99 jobs, the other counts
raw pre-normalization URL overlaps across 100 jobs including the 1
malformed job that never reached the dedup step. Both are consistent with
each other and with a real, working cross-query dedup step.

## Production DB Safety

| | SHA-256 | Row counts |
|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | identical |

**Byte-identical.** All writes (100 jobs analyzed, 72 unique, 15 matches)
landed only in the temporary DB copy, which was deleted after this
validation, along with the temporary driver script.

## Regression Suite

```
Full standalone suite (38 files): PASS=38 FAIL=0
python3 -m py_compile across all scripts/*.py: PY_COMPILE_ALL_OK
```

No test file, production file, or configuration file was created,
modified, or deleted in this task. This was a pure live-validation task.

## Files Changed

**None.**

## Exact Request Counts

- Health checks: 1
- Search queries: 5
- Detail-page fetches: 100
- **Total requests to Naukri: 106**
- Retries: 0
- HTTP status distribution: **200 × 106** (0 of any other status)
- Classifier distribution: **VALID_RESULTS × 5** (0 BLOCKED, 0
  SOFT_BLOCK_OR_CHALLENGE, 0 PARSE_FAILURE)

## Is Naukri Phase 1 Now Validated?

**Yes — Naukri can now be marked as validated for this exact
configuration** (genuinely headless, `channel='chromium'`, default
`JOBOS_NAUKRI_USER_AGENT`, native `jobAge` freshness filter), based on:
1. The single-query live validation (prior task): 1/1 query succeeded.
2. This controlled 5-query live validation: 5/5 queries succeeded, 0
   blocks, 0 retries, `jobAge=3` empirically confirmed by returned
   listings.

**Remaining, explicitly acknowledged limitation (INTERPRETATION, not
FACT):** 6 total successful live queries across 2 sessions is evidence of
reliability, not a guarantee of indefinite reliability — Naukri's
server-side behavior could change at any time, independent of anything in
this codebase. "Validated" here means "validated against the evidence
gathered so far," not "provably permanent."

## Recommended Next Gate

Per the project's phase plan (Phase 3 → Phase 4): **daily-scale validation
is the next possible live-validation step for Naukri specifically**, but
per the explicit phase discipline ("Do NOT jump directly to large-scale
scraping" / "Do NOT implement other job boards... or any later phase"),
the next *authorized* piece of new work is **Phase 4: multi-source
adapter architecture** (LinkedIn, Indeed, Foundit, Instahyre, Wellfound,
career pages) — offline-only until each adapter passes its own Phase-1/
Phase-2/Phase-3 gates independently, exactly as Naukri just did.

**Stopping here, per instruction.** No daily-scale Naukri validation, no
other job board implementation, no Excel generation, no application
automation, and no scheduler work was started in this task.
