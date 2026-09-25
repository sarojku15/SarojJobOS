# Phase 14.7 — Adaptive Search-Provider Query Expansion

**Date:** 2026-09-22
**Objective:** restore the location/job-discovery coverage Phase 14.6's flat consolidation lost, through adaptive per-source query expansion — without reverting to the old 28-query pattern and without redesigning the search architecture.

## The problem, restated with real numbers

| | Search-provider queries | Search-provider eligible/scored |
|---|---|---|
| Phase 14.5 (old, 4 locations × 7 boards) | 28 | 93 |
| Phase 14.6 (1 consolidated query/board) | 7 | 27 |
| **Phase 14.7 (this phase)** | **12** | **33** |

Root cause, confirmed again this phase: every provider call is capped at ~10 results (`search_provider_adapter.py`'s unchanged `_MAX_RESULTS_PER_QUERY`) regardless of how many locations are OR'd into one query — so one combined query structurally cannot surface as much as four separate ones.

## What was built

- **`config/search_planner.json`** — five new, env-overridable keys: `max_provider_queries_per_source_per_run` (default 2), `coverage_min_unique_jobs` (5), `coverage_min_locations_covered` (3), `coverage_min_new_jobs` (3), `coverage_min_completeness` (0.4).
- **`scripts/daily_search_planner.py`** — extended, not replaced:
  - `_normalize_city()` / `_configured_cities()` reuse the **existing, unmodified** `location_taxonomy.py` (the same module `job_eligibility.py` already relies on) so "Bangalore" and "Bengaluru" are never treated as different cities.
  - `_job_snapshot()` now also captures each job's `location` and `completeness`, not just its id — the basis for per-round coverage measurement.
  - `execute_daily_plan()` gained an adaptive loop: after the round-1 consolidated wave, any search-provider board whose cumulative coverage is insufficient (Section 3's five conditions) gets **one more query** combining every still-missing/under-covered configured location, bounded by the per-source cap and the existing Phase 14.6 run/day budgets. The loop generalizes to a higher cap if configured (more rounds), but the default cap of 2 means exactly one extra round.
- **`scripts/test_phase14_7_adaptive_expansion.py`** — 18 new offline checks (Sections 13.A–K).

**Not touched:** `score_job.py`, `tracker.py`, `canonical_job.py`, `cross_source_dedup.py`, `source_adapter.py`, `source_registry.py`, `search_provider_manager.py`, `search_provider.py`, `restricted_source_registry.py`, `search_provider_adapter.py`, `location_taxonomy.py`, `search_history_store.py`, `generate_run_report.py` (Phase 14.6's `planner_metrics` hook was reused as-is).

## How adaptive expansion decides (Sections 1–9)

1. **Primary query** — unchanged from Phase 14.6: one query per board, all target locations OR'd, built by the existing query planner/profile.
2. **Coverage measurement** — after round 1, every newly-persisted job's location is normalized via `location_taxonomy.py` and compared against the candidate's configured cities (also normalized, so aliases never cause a false "uncovered").
3. **Insufficiency triggers** (any → expand): fewer than 5 unique jobs, fewer than `min(3, configured count)` locations represented, **any** configured location with zero jobs, fewer than 3 genuinely new jobs, or average completeness below 0.4. All five thresholds are config keys, not hard-coded.
4. **Targeted query** — combines every still-missing/under-covered location into **one** OR'd follow-up query (never re-queries an already-sufficient location).
5. **Per-source cap** — `max_provider_queries_per_source_per_run=2` by default, so the normal maximum is 7 × 2 = 14, not 7 × 4 = 28. A board whose round-1 query already performs well uses only 1 query.
6. **Global budget** — the existing Phase 14.6 `MAX_SEARCH_PROVIDER_QUERIES_PER_RUN`/`_PER_DAY`/`_PER_PROVIDER_PER_DAY` still govern every query, adaptive or not; never exceeded (see "Budget behavior" below — this phase's real live run demonstrated the budget stopping cleanly mid-way through evaluation).
7. **Priority** — when a naive "target every missing location" combination collides with one already tried this execution (the degenerate all-locations-zero case — see "A bug was found and fixed" below), the lowest-priority city is dropped until the combination is new; a deterministic, explainable tie-break, no ML.
8. **Known-job awareness** — every query's new-vs-known split comes from the real before/after DB diff (the same mechanism Phase 14.6 built), never estimated.
9. **Result quality** — sufficiency is judged on unique jobs, new jobs, location coverage, *and* completeness together — 10 raw hits with 9 duplicates and 1 new job would correctly still count as insufficient (per the `coverage_min_new_jobs` check).

## Direct sources unchanged (Section 10)

Naukri/Hirist/IIMJobs/Apna planning is untouched — same 16 queries this run as Phase 14.6's equivalent run, same refresh-interval/cooldown logic, no regression (re-verified: Phase 14.6's own 21-check test suite still passes unmodified in behavior).

## Daily cooldown preserved (Section 11)

Query fingerprint history, cooldown, and source refresh intervals are all reused from Phase 14.6, unmodified. A second identical plan produces 0 provider queries (re-verified offline, Scenario I/J).

## A bug was found and fixed while writing this phase's own tests

When a board's round-1 query covers **zero** configured locations, the naive round-2 target ("every missing location") is textually identical to round 1's own query — which collides with the cooldown record round 1 *just wrote* moments earlier in the same execution, silently blocking what should be a genuine retry. **Fix:** `execute_daily_plan()` now tracks every distinct city-combination already attempted this execution, per board, and shrinks the target set by one (lowest-priority) city whenever the naive combination was already tried, producing a real, different, cheaper probe instead of a wasted duplicate. Verified via the new test's Scenario D (consolidated query returns 0 → a genuinely different follow-up query is issued) and confirmed honest in the live run below (`locations_still_uncovered` correctly lists what a real retry still couldn't find, never guessed).

## Tests (Section 13, A–K)

All 18 checks pass, fully offline, real-evidence-shaped fixtures, zero network calls:

| # | Scenario | Result |
|---|---|---|
| A | Consolidated query covers all 4 locations | 1 query only, 0 adaptive |
| B | Consolidated covers 1 location | targeted query fills the other 3 |
| C | Consolidated covers 3 locations | exactly 1 targeted query, only the missing one |
| D | Consolidated returns 0 jobs | targeted expansion attempted, subject to budget; honest "still uncovered" when it also finds nothing |
| E | 10 raw hits, only ~2 new (8 pre-seeded as known) | expansion correctly permitted |
| F | Targeted query fills the missing location | no round 3 issued for it |
| G | Per-source cap (2) | never exceeded even with persistent zero results |
| H | Global run budget (1) | never exceeded |
| I | Cooldown | identical query within window skipped |
| J | Second immediate plan | 0 estimated queries |
| K | Scoring/dedup public interfaces | untouched |

## Replay comparison, real evidence, zero additional live calls (Section 14)

Using the same real, Phase-13-captured LinkedIn/Instahyre evidence `test_phase14_search_provider.py` already relies on: old style would be 4 queries/board; Phase 14.6, 1; Phase 14.7, 1 consolidated + 1 adaptive when the real evidence (single-city, Bangalore-only) shows incomplete coverage — exactly reproducing the mechanism this phase adds, with the honest caveat that Phase 13's evidence only ever captured one city, so the targeted retry correctly reports the other three as still uncovered rather than inventing results for them.

## One controlled live validation (Section 15)

Same real, confirmed Saroj profile (`cand_821156a4dfc3`), temp DB, role "Site Reliability Engineer", locations Bangalore/Hyderabad/Pune/Chennai, freshness 3 days. You.com only — Serper never called (`AUTH_FAILED`, unchanged, 0 requests).

**Provider queries:** 7 consolidated + 5 adaptive = **12** (vs. Phase 14.6's 7, vs. Phase 14.5's 28).

**Per-board coverage:**

| Board | Queries used | Unique jobs | New jobs | Cities covered | Still uncovered |
|---|---|---|---|---|---|
| LinkedIn | 2 | 13 | 7 | Bengaluru, Pune | Chennai, Hyderabad |
| Instahyre | 2 | 24 | 14 | — | all 4 |
| Cutshort | 2 | 3 | 2 | — | all 4 |
| Indeed | 2 | 0 | 0 | — | all 4 |
| Foundit | 2 | 0 | 0 | — | all 4 |
| Wellfound | 1 | 8 | 8 | Pune | Bengaluru, Chennai, Hyderabad |
| Shine | 1 | 2 | 2 | — | all 4 |

**Honest finding:** most boards' real jobs never resolved to a single configured city through the existing, unmodified `location_taxonomy.py` — Instahyre found 24 real, unique jobs but 0 of them had a location string that normalized cleanly to Bangalore/Hyderabad/Pune/Chennai. This is not fabricated or hidden: the coverage tracker correctly reports "0 cities covered" rather than guessing, and it did **not** stop the pipeline from scoring those 24 jobs on their own merits — only the location-coverage *metric* is affected, not eligibility/scoring.

**Why Wellfound and Shine got only 1 query each:** the global `max_search_provider_queries_per_run=12` budget was exhausted evaluating the other 5 boards first (config order: LinkedIn, Indeed, Foundit, Instahyre, Cutshort all needed and got round-2 queries before the budget ran out). This is Section 6's "stop cleanly, never exceed budget" working exactly as specified in a real run — not a defect. Both boards remain due again on their next scheduled run; nothing is ever permanently suppressed.

**Eligible/scored:** direct 78 + search-provider 33 = **111 total** (vs. 106 total in Phase 14.6's equivalent run — search-provider rose from 27 to 33, **+22%**). Priority breakdown across all sources: A=6, B=5, C=9, REJECT=91 — READY_FOR_APPROVAL (A/B) = 11, same as Phase 14.6 (still entirely from Naukri, since search-provider jobs' priority stayed C/REJECT this run too — consistent with every prior phase's finding that search-provider results lack `jd_text`).

**Provider requests:** `{you: 12, tavily: 0, exa: 0, brave: 0, serper: 0}` — confirms Serper was genuinely never reached.

## Success target assessment (Section 16, no fabricated pass/fail number)

| Criterion | Result |
|---|---|
| Provider queries significantly below 28 | **Yes — 12 (57% fewer)** |
| Materially better coverage than Phase 14.6's 27 | **Yes — 33 eligible/scored (+22%)** |
| Reasonable coverage across configured locations | **Partial** — 2 of 7 boards resolved at least one city; the rest found real jobs whose location text didn't parse to a single city, and 2 boards never got a round-2 chance this run due to budget |
| No duplicate query requests | **Yes** (the self-collision edge case is fixed) |
| No budget violations | **Yes** — exactly 12 of 12 used, never exceeded |

## 9-sheet report

`data/reports/phase14_7_adaptive_search_optimization.xlsx` — exactly the existing 9 sheets, no 10th. `RUN_SUMMARY` gained the Section 12 metrics (`consolidated_queries`, `adaptive_queries`, `queries_saved_vs_old`, `locations_initially_covered`/`_filled_by_adaptive_queries`/`_still_uncovered` per board, `new_jobs_per_source`, `average_new_jobs_per_provider_query`, baseline comparisons, budget/Serper state) appended after the existing summary keys — no other sheet touched.

## Test suite

**69 test files, all exit 0** (67 base + Phase 14.6's 21-check suite, re-verified unmodified in behavior + this phase's new 18-check suite).

## Production DB

SHA256 before/after: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` — **byte-identical.**

## Explicit confirmations

- Scheduler/launchd activated: **NO**
- Auto-apply changed: **NO**
- Restricted-board pages fetched directly: **NO**
- Serper called: **NO** (`AUTH_FAILED`, 0 requests, unchanged)
- Reverted to the old 28-query strategy: **NO**
- Search architecture redesigned: **NO**
- A second scoring/dedup/normalization/report pipeline created: **NO**

---
STOP after this report, per instructions.
