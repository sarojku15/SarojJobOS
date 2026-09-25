# Naukri 5-Query Live Validation

## 1. Test Objective

Validate the complete real search pipeline — `search_submission` →
`search_worker` → `source_registry` → `naukri_adapter` → `discover_local` →
`job_eligibility` → `score_job` → ranking enrichment — end to end against
**exactly 5 live Naukri search queries**, generated deterministically from
Saroj's real CONFIRMED candidate profile via the existing, unmodified
`query_planner.build_queries_from_search_profile()`, before scaling to 10 and
then 50 queries.

## 2. Safety Controls

- Production DB (`data/applications/jobos.db`) opened **read-only** for
  before/after verification only — never opened for writing.
- Fresh, isolated temp SQLite DB created via `init_tracker` +
  `migrate_v2_schema` (same schema-init helpers this project's own test suite
  uses) — never a copy of production.
- Exactly **one** `search_runs`/`search_queue` row was created, via the
  unmodified `search_submission.submit_search(..., max_queries=5)` — using
  that function's own existing `max_queries` parameter, not a hand-edited
  query list.
- Execution used the unmodified `search_worker.run_once(db, candidate_id="saroj", max_items=1, dry_run=False)`
  exactly once. No query was retried; no second search run was submitted.
- All instrumentation was **observation-only**: `NaukriAdapter.search` and
  `NaukriFetcher.fetch` were wrapped (original implementation still called
  through unmodified) purely to record per-query/per-fetch detail for this
  report — nothing about adapter behavior was altered or bypassed.

## 3. Production DB Baseline

| | SHA-256 | Size | Row counts |
|---|---|---|---|
| **Before** | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | 114688 bytes | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| **After** | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | 114688 bytes | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |

**Byte-identical. Confirmed unchanged.**

Temp DB: `/var/folders/5x/hc_gpp312k1fm2bsxw0d2t2r0000gn/T/jobos_live_5query_validation_26fc_fm9/jobos_test.db`

## 4. Exact Five Queries

Recorded **before** execution, produced by the unmodified
`build_queries_from_search_profile()` (source → role → location, each sorted
alphabetically, deterministic) from Saroj's full, un-narrowed confirmed
profile (9 target roles × 6 target locations × 1 source, capped at 5):

| # | Source | Role | Location |
|---|---|---|---|
| 1 | NAUKRI | Infrastructure Engineer | Bangalore |
| 2 | NAUKRI | Infrastructure Engineer | Bengaluru |
| 3 | NAUKRI | Infrastructure Engineer | Chennai |
| 4 | NAUKRI | Infrastructure Engineer | Hyderabad |
| 5 | NAUKRI | Infrastructure Engineer | Pune |

All 5 queries share the role "Infrastructure Engineer" because that is
alphabetically first among Saroj's 9 target roles — this is the correct,
deterministic output of the existing planner, not a hand-picked "easy" query.
Two of the five location slugs (`bangalore`, and all of them for the
`infrastructure-engineer` role) were previously **unverified** URL patterns
for `naukri_adapter.py`'s `_slugify()` (only `senior-site-reliability-engineer`/`bengaluru`
had been confirmed live before this test).

## 5. Per-Query Results

| # | Query | Classification | Raw jobs | Job links | Detail fetches | Exception |
|---|---|---|---|---|---|---|
| 1 | Infrastructure Engineer / Bangalore | VALID_RESULTS | 20 | 20 | 20 | None |
| 2 | Infrastructure Engineer / Bengaluru | VALID_RESULTS | 20 | 20 | 20 | None |
| 3 | Infrastructure Engineer / Chennai | VALID_RESULTS | 20 | 20 | 20 | None |
| 4 | Infrastructure Engineer / Hyderabad | VALID_RESULTS | 20 | 20 | 20 | None |
| 5 | Infrastructure Engineer / Pune | VALID_RESULTS | 20 | 20 | 20 | None |

All 5 previously-unverified URL slugs (`infrastructure-engineer-jobs-in-bangalore`,
`...-in-bengaluru`, `...-in-chennai`, `...-in-hyderabad`, `...-in-pune`)
resolved correctly to real, positively-classified (`has_shell_markers=True`)
search-results pages. `_slugify()`'s URL pattern generalizes correctly beyond
the one previously-verified combination.

Full per-query detail (including `has_shell_markers`, `block_reason`, raw
classification object) is in the accompanying JSON.

## 6. Aggregate Results

| Metric | Value |
|---|---|
| Total live SEARCH-PAGE requests | **5** (exactly as planned) |
| Total live DETAIL-PAGE requests | **100** (20 per query — every parsed job link was fetched) |
| Total live HTTP requests (incl. 1 health check) | **106** |
| Failed fetches | 0 |
| Raw jobs (sum across 5 queries) | 100 |
| Normalized jobs | 100 (0 malformed) |
| Cross-query duplicate occurrences removed | 28 |
| Unique global jobs | 72 |
| Eligible | 28 |
| Ineligible | 44 |
| Candidate matches created | 28 |
| Candidate matches updated | 0 |
| Ranking records produced | 72 |
| Ranking errors | 0 |

## 7. Cross-Query Deduplication Analysis

The same underlying Naukri posting frequently appears across multiple
location-scoped searches (a real Naukri behavior — many listings are posted
against several cities at once, e.g. "Pune, Bengaluru"). The worker's
existing, **unmodified** `discover_local.deduplicate()` operates over the
**entire combined raw-job batch from all 5 queries in one pass** (not
per-query) — exactly the mechanism needed here, with no new code required.

- **100 raw jobs → 72 unique jobs (28 duplicate occurrences collapsed).**
- **`jobs` table row count: 72** — verified **zero** rows sharing the same
  `(source, job_id)` pair (`GROUP BY source, job_id HAVING COUNT(*) > 1`
  returned empty).
- **`candidate_job_matches` row count: 28** — verified **zero** rows sharing
  the same `(candidate_id, job_id)` pair. Duplicate search-result occurrences
  did **not** inflate the match count: 28 unique eligible jobs produced
  exactly 28 match rows, not more.
- Same-source (`NAUKRI`-vs-`NAUKRI`) pairs are correctly **out of scope** for
  `cross_source_dedup.find_cross_source_duplicate_candidates()` by design (it
  answers a different question — cross-*source* duplicates — and the exact
  `(source, job_id)` dedup above already owns intra-source duplicates). All 72
  ranking records correctly show `duplicate_candidates: []`, since only one
  source was queried in this test.

## 8. Eligibility Analysis

| Reason | Count |
|---|---|
| ELIGIBLE | 28 |
| EXPERIENCE_BELOW_PROFILE | 44 |
| LOCATION_NO_MATCH | 0 |

All 44 ineligible jobs were excluded solely on experience grounds (verified:
`excluded_location: 0` in the `WorkItemResult`, and every job's location
included at least one of the 6 candidate target locations). No location-based
false rejection occurred. This is consistent with the pattern already
documented in the prior 20-job score-quality audit.

## 9. Score / Ranking Analysis

- **Score range (28 eligible jobs): 33 – 95** (mean 57.5).
- **Priority distribution:** A=2, B=1, C=2, REJECT=23.
- **Component-sum check:** `sum(components) == score` verified for **all 28**
  eligible jobs — zero mismatches.
- **Score-explanation check:** `score_explanation` fields verified identical
  to `score_job()`'s own return dict for all 28 — zero mismatches.
- **Ineligible jobs are never scored:** all 44 ineligible ranking records have
  `score=None, priority=None, status=None` — verified.
- **Exactly one match per (candidate, job) pair:** confirmed in §7 — 28 unique
  jobs, 28 match rows, no duplicates.
- **Ranking does not alter the 100-point model:** `score_job.py` was not
  modified; every eligible job's score is the direct, unmodified output of
  the existing rubric.

**Top 5 eligible jobs by score:**

| Score | Priority | Company | Title |
|---|---|---|---|
| 95 | A | Bounteous | Senior Cloud Infrastructure Engineer |
| 90 | A | Pfizer | Senior Manager, Cloud Infrastructure Engineer |
| 85 | B | Walmart | Systems and Infrastructure Engineer III |
| 75 | C | Ellicium Solutions | Infrastructure Engineer |
| 70 | C | Cradlepoint | Infrastructure Engineer |

## 10. Search-Run Accounting

| Field | Value |
|---|---|
| status | COMPLETED |
| queries_total | 5 |
| queries_completed | **5** |
| queries_failed (timed out) | 0 |
| queries_blocked | 0 |
| jobs_discovered (raw) | 100 |
| jobs_deduplicated | 28 |
| jobs_experience_excluded | 44 |
| jobs_eligible | 28 |
| jobs_scored | 28 |
| jobs_ready (A/B) | 3 |
| matches_created | 28 |
| matches_updated | 0 |
| errors_count | 0 |

**`queries_completed == queries_total == 5`, confirming the previously-fixed
`succeeded_queries` accounting defect (from the first live-integration test)
holds correctly here too** — all 5 queries genuinely succeeded (all returned
real results, so this run doesn't newly re-exercise the *zero-result*
edge case specifically, but it does confirm the general accounting formula
(`succeeded_queries + timed_out_queries`) produces the correct total when
every query succeeds).

## 11. Worker Lifecycle

`search_queue`: `QUEUED → RUNNING → COMPLETED` (single queue item, claimed
once, finalized once). Final state: `COMPLETED`. **Zero** remaining
`QUEUED`/`RUNNING` items after the run.

## 12. Naukri Classification Results

All 5 of 5 search pages classified **`VALID_RESULTS`** (`has_shell_markers=True`,
`block_reason=NONE`, 20 job links each). No `BLOCKED`, `SOFT_BLOCK_OR_CHALLENGE`,
`VALID_EMPTY_RESULT`, or `PARSE_FAILURE` occurred in this run. No observation
of a challenge-like page being misclassified as `PARSE_FAILURE` — the
classifier was not exercised on that boundary this time.

## 13. Errors / Blocking / Timeouts

None. `blocked_sources: []`, `timed_out_queries: 0`, `errors: []`,
`ranking_errors: []`. 0 of 106 fetches failed.

## 14. Data-Quality Observations

- **Confirmed, fixed bug:** `freshness.py`'s `_WEEKS_AGO_PATTERN` /
  `_MONTHS_AGO_PATTERN` (and, for consistency, `_DAYS_AGO_PATTERN`) did not
  handle Naukri's real `"N+ weeks ago"` / `"N+ months ago"` bucketed phrasing
  (e.g. `"Posted: 3+ weeks ago"`). This affected **34 of 72 (47%) of the real
  jobs in this run**, all falling back to `UNKNOWN` freshness instead of
  their correct category. See §15 below for full details — **fixed** during
  this validation (freshness only; no effect on scoring/eligibility, which
  never consult `posted_date`).
  - **Freshness distribution BEFORE the fix:** HOT=5, FRESH=20, AGING=7,
    OLD=6, UNKNOWN=34.
  - **Freshness distribution AFTER the fix** (recomputed offline against the
    same already-captured data, zero new network calls): HOT=5, FRESH=20,
    AGING=7, **OLD=40**, UNKNOWN=**0**.
- Many "Infrastructure Engineer" postings are enterprise IT/network/systems
  roles (VMware, Nutanix, AV/Q-sys, Oracle HCM) rather than cloud/SRE-adjacent
  roles — reflected correctly in their low scores (33–50), not a scoring
  defect; `core_role` still matches on the literal title keyword regardless
  of domain fit, exactly as designed.
- Naukri returned exactly 20 job links for every one of the 5 queries — a
  likely fixed page-size limit (page 1 only, as already documented in
  `naukri_adapter.py`), not a sign of a parsing issue.

## 15. Confirmed Bug — Fixed

### BUG-2 (FIXED): `freshness.py` did not recognize `"N+ weeks/months ago"`

**Severity:** Medium (freshness is a secondary, informational signal — never
affects score/eligibility/matching — but was silently wrong/UNKNOWN for
nearly half of real postings).

**Evidence:** direct inspection of `posted_date` values in the temp DB:
`"Posted: 3+ weeks ago"` (34 occurrences), alongside already-working
`"Posted: 1/2/3 week(s) ago"` and `"Posted: N day(s) ago"` variants.

**Root cause:** `_WEEKS_AGO_PATTERN = re.compile(r"(\d+)\s*weeks?\s*ago", ...)`
(and the equivalent months/days patterns) had no provision for a literal `+`
character directly after the number, so `"3+ weeks ago"` never matched any
pattern and fell through to `UNKNOWN`.

**Fix applied:** added an optional `\+?` after the digit group in all three
patterns (`_DAYS_AGO_PATTERN`, `_WEEKS_AGO_PATTERN`, `_MONTHS_AGO_PATTERN`),
treating `"N+"` as exactly `N` units (the literal lower bound Naukri's own
bucketed display gives) — consistent with how the module already treats
every other numeric phrase literally.

**Regression test added:** `test_freshness.py` — 3 new cases (`"3+ weeks ago"`
→ OLD/21, `"3+ months ago"` → STALE/90, `"5+ days ago"` → FRESH/5).

**Verification:** all 33 pre-existing test files still pass unmodified.
The affected validation was re-run **offline** (against the same
already-captured temp DB, zero new network calls) to confirm the corrected
distribution in §14 above.

## Readiness for the Next Controlled 10-Query Test

**Ready.** This run demonstrates:
- The full live pipeline (submission → worker → adapter → classifier →
  normalization → cross-query dedup → eligibility → scoring → ranking →
  persistence) is correct across 5 genuinely different, previously-unverified
  query URLs, not just the one combination tested before.
- Cross-query deduplication within a single search run works correctly and
  does not inflate match counts.
- The one bug this run surfaced (freshness phrasing) is fixed, tested, and
  fully isolated from scoring/eligibility.
- Production DB remained byte-identical throughout.

No blocking issues were found. The system is ready for a 10-query test at
your direction.
