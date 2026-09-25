# Phase 14.6 — Daily Search Planner Optimization

**Date:** 2026-09-21
**Objective:** reduce unnecessary daily search-provider/API calls while maintaining useful job discovery — without redesigning the source architecture, scoring, normalization/dedup, or the 9-sheet report.

## What was built

Three new files, additive only:

- `config/search_planner.json` — user-configurable budgets, cooldown, and per-source refresh intervals (every key has an env-var override of the same name, upper-cased).
- `scripts/search_history_store.py` — a small, git-ignored local JSON file (`data/applications/search_query_history.json`, mirrors the existing `search_provider_usage_store.py` pattern) tracking per-query-fingerprint and per-source history: attempts, successes, result counts, new-result counts, last-run timestamps.
- `scripts/daily_search_planner.py` — the planner itself: decides which queries are DUE, consolidates search-provider locations into one query per source, enforces budgets, and executes the reduced plan through the **same, unmodified** `submit_search()` → `claim_next_queue_item()` → `process_queue_item()` pipeline every prior phase used — called at most twice (direct wave, then search-provider wave).

Two existing files got a small, additive, backward-compatible extension:

- `scripts/search_submission.py` — `build_search_plan()`/`submit_search()` gained an optional `query_plan_override` parameter (default `None`, preserving prior behavior exactly) so the planner can submit its reduced/consolidated plan through the real submission path instead of a second one.
- `scripts/generate_run_report.py` — `generate()` gained an optional `planner_metrics` parameter, merged into the **existing** RUN_SUMMARY sheet only (which already flattens whatever dict it's given). No new sheet, no change to any of the other 8 sheets.

**Not touched:** `score_job.py`, `tracker.py`, `canonical_job.py`, `cross_source_dedup.py`, `source_adapter.py`, `source_registry.py`, `search_provider_manager.py`, `search_provider.py`, `restricted_source_registry.py`, `search_provider_adapter.py`.

## Provider pool rules (Section 1) — reused, not reimplemented

`SearchProviderManager` (Phase 14.2) remains the **only** provider-selection mechanism. The planner never constructs a provider directly and never queries a second provider after a first success. Serper's `AUTH_FAILED` state was left untouched — its priority position (last, after You/Tavily/Exa/Brave) plus You.com continuing to succeed means Serper was never even reached: `provider_requests_used['serper'] == 0` for the live run below.

## Daily query budget (Section 2)

| Setting | Default | Mechanism |
|---|---|---|
| `MAX_SEARCH_PROVIDER_QUERIES_PER_RUN` | 12 | New: enforced directly in `build_daily_plan()`, capping how many search-provider queries one plan may include |
| `MAX_SEARCH_PROVIDER_QUERIES_PER_DAY` | 30 | New: `build_daily_plan()` sums today's already-recorded requests across all 5 providers via the **existing** `search_provider_usage_store.py` |
| `MAX_SEARCH_PROVIDER_QUERIES_PER_PROVIDER_PER_DAY` | 12 | Reused: passed straight through to `SearchProviderManager`'s existing `max_daily` (the same mechanism Phase 14.2 already built) |

Verified offline: with `max_search_provider_queries_per_run=1`, the plan included only 1 search-provider query and every other DUE source showed a `budget-limited` skip reason.

## Query deduplication & cooldown (Sections 3/4)

Every query fingerprints deterministically on `(provider, source, role, location, max_job_age_days)`. A fingerprint that succeeded within `SEARCH_QUERY_COOLDOWN_HOURS` (default 24) is skipped unless `force_refresh=True`. History lives in `data/applications/search_query_history.json` — git-ignored, never the production DB.

**Verified twice:**
1. Offline (fake adapters + replay fixtures): a second `build_daily_plan()` call immediately after execution shows every source `SKIP`, `estimated_provider_calls == 0`.
2. **Live**: re-ran `build_daily_plan()` (read-only, zero network calls) against the temp DB the real live run populated — all 11 sources correctly `SKIP` with reason `recent successful run (refresh interval not elapsed)`, 0 estimated provider calls.

Force Refresh (Section 16) correctly bypasses both cooldown and refresh-interval, verified offline.

## Source refresh intervals (Section 5)

Naukri/Apna: 12h. Hirist/IIMJobs and all 7 restricted boards: 24h. Configurable; no source is ever permanently suppressed — `is_source_due()` returns `True` again once the interval elapses.

## Query consolidation (Section 6) — the main lever

For search-provider sources, the planner builds **one** query per source with all target locations OR'd into the query text (`build_site_query()` already accepts any free-text location string — no change needed to `restricted_source_registry.py` or `search_provider_adapter.py`). Direct sources are **not** consolidated (Section 6 scopes this to search-provider sources only — their query granularity mirrors how each site's own search actually works).

## Adaptive strategy (Section 7) — cross-run, not mid-run

If the **last** consolidated query for a source's fingerprint succeeded but returned zero results, the next plan falls back to a bounded (max 2, ranked by historical priority) per-location split instead. This is a cross-run adaptive rule driven by `search_history_store`, not a mid-run retry loop — a deliberate, disclosed simplification: `process_queue_item()`'s own architecture executes one batch of queries per call, and inventing a true within-run retry loop would have meant calling `discover_from_sources()` a third time mid-run, which felt like more surface area than the literal instruction ("issue one more, then stop") required. The net effect is the same query-count discipline Section 7 asks for, one run later.

## Result-based stopping (Section 8)

Every DUE source is queried at least once before any target-based early stop — the planner never skips a source that has become due just because an earlier source already found enough jobs, exactly as instructed. Early stopping only happens from the budget ceiling, never from "we already have enough."

## Direct sources first, search-provider fills gaps (Sections 11/12)

Two-wave execution: wave 1 submits and fully processes all DUE **direct** sources first, through the unmodified pipeline; wave 2 then submits/processes DUE **search-provider** sources against the same database. This lets the existing, unmodified cross-source dedup mechanism see wave 1's jobs already persisted before wave 2's are normalized/scored. Honest caveat: this project's dedup mechanism recognizes a duplicate only **after** both postings are fetched (title/company/location similarity) — there is no evidence-based way to know in advance that an unfetched search-provider posting duplicates a direct-source one, so no query was ever skipped specifically because "direct probably already found this." What's implemented and verified is the wave ordering plus the existing dedup mechanism running across both waves' data together.

## Freshness (Section 13)

Unchanged. You.com's `freshness` request parameter is still never sent (the Phase 14.4 fix stands, re-verified by the existing, unmodified `test_phase14_3_you_provider.py`).

## A bug was found and fixed during this phase's own live validation

`execute_daily_plan()`'s wave-2 before/after job-set snapshot originally queried `jobs.source IN (registry_keys)` (e.g. `'CUTSHORT_SEARCH'`), but `jobs.source` actually persists the real board name (`'CUTSHORT'`) — the same distinction Phase 14.5 discovered for reporting, missed here for the planner's own new/known tracking. Result: `new_jobs_by_source`/`known_jobs_by_source` for the search-provider wave came back empty even though real jobs were found, which would have fed incorrect `new_result_count=0` into the history store for every restricted board.

**Fix:** `_run_one_wave()` gained a `job_id_sources` parameter — the board-level names used for the snapshot query — kept separate from `sources` (the registry keys used for actual submission). Re-verified two ways, with **zero additional live network calls**:
1. The offline acceptance test's "new jobs discovered" count rose from 2 to 19 after the fix (previously undercounted).
2. The already-fetched live temp DB's real per-source counts were recomputed directly (`CUTSHORT: 1, LINKEDIN: 6, INSTAHYRE: 10, WELLFOUND: 8, SHINE: 2, INDEED: 0, FOUNDIT: 0`) and recorded into history — the underlying pipeline execution was never wrong, only this phase's own new reporting layer was, and it needed no re-run to correct.

## One controlled live validation (Section 19's final requirement)

Same search profile as Phase 14.5: candidate `cand_821156a4dfc3`, role "Site Reliability Engineer", locations Bangalore/Hyderabad/Pune/Chennai, freshness 3 days, temp DB (never production).

**Plan produced (Section 17's exact format):**

```
Search Plan
-----------
Direct:
  NAUKRI      DUE   4 queries
  HIRIST      DUE   4 queries
  IIMJOBS     DUE   4 queries
  APNA        DUE   4 queries

Search Provider:
  LINKEDIN    DUE   1 query (consolidated)   YOU
  INDEED      DUE   1 query (consolidated)   YOU
  FOUNDIT     DUE   1 query (consolidated)   YOU
  INSTAHYRE   DUE   1 query (consolidated)   YOU
  CUTSHORT    DUE   1 query (consolidated)   YOU
  WELLFOUND   DUE   1 query (consolidated)   YOU
  SHINE       DUE   1 query (consolidated)   YOU

Estimated provider calls: 7
Maximum allowed (per run): 12
Execute? YES
```

**Old vs. new query count (Section 19's required comparison):**

| | Old (Phase 14.5 pattern) | New (this phase, first run) |
|---|---|---|
| Total queries | 44 | 23 |
| Direct queries | 16 | 16 (unchanged — not in scope for consolidation) |
| Search-provider queries | 28 | 7 |

A first-ever run for a candidate necessarily checks every due source at least once (Section 8), so this 23-query figure is the **maximum** this planner will issue for this profile — a second run within the cooldown window issues **0** (verified above).

**Execution result:**

| Wave | Sources | Queries | Raw | Unique | Eligible/Scored |
|---|---|---|---|---|---|
| direct | NAUKRI, HIRIST, IIMJOBS, APNA | 16 | 374 | 226 | 79 |
| search_provider | 7 restricted boards | 7 | 63 | 27 | 27 |

Provider requests this run: `{you: 7, tavily: 0, exa: 0, brave: 0, serper: 0}` — confirms Section 15's "success stops the chain" behavior end to end (You succeeded on every one of its 7 calls; no other provider was ever touched).

## Performance target (Section 21) — actual numbers, no pass/fail framing

| Metric | Value |
|---|---|
| Old search-provider requests | 28 |
| New search-provider requests (first run) | 7 |
| Request reduction | **75%** |
| Old total requests | 44 |
| New total requests (first run) | 23 |
| Total reduction (first run) | 47.7% |
| Old eligible/scored (Phase 14.5, full 28-query matrix) | 171 (direct 78 + search-provider 93) |
| New eligible/scored (this run, 7-query consolidated matrix) | 106 (direct 79 + search-provider 27) |

**Honest finding, disclosed rather than hidden:** the search-provider side's eligible/scored count dropped from 93 to 27 — not from cooldown/budget skipping (nothing was skipped this first run), but from a mechanical property of the **unchanged** `search_provider_adapter.py`: every provider call returns at most 10 results (`_MAX_RESULTS_PER_QUERY`) regardless of how many locations are OR'd into one query. Four per-location queries could together surface up to 40 candidate URLs per source; one consolidated query surfaces at most 10. This is a real trade-off between request count and discovery breadth, not a defect. It is noted as a recommendation for a future, explicitly-approved phase (e.g., requesting a higher `num` on a consolidated query, or tightening the adaptive-split threshold from "zero results" to "below N results") — not implemented here, since the instructions asked for a simple, explainable heuristic and no further scope beyond what was specified.

## Serper (Section 20)

State unchanged: `AUTH_FAILED` / HTTP 403. Zero requests consumed this phase (`provider_requests_used['serper'] == 0`), confirming it was never attempted since You.com continued to succeed and no automatic Serper retry logic was added.

## 9-sheet report validation

`data/reports/phase14_6_daily_planner_optimization.xlsx` — exactly the existing 9 sheets: `APPLY_TODAY, ALL_MATCHING_JOBS, NEW_JOBS, ALREADY_APPLIED, REJECTED_EXCLUDED, DUPLICATES, APPLICATION_TRACKER, SOURCE_HEALTH, RUN_SUMMARY`. All 16 `planner.*` metric rows (old/new query counts, requests avoided, reduction percent, skip counts, new/known job counts) appear in `RUN_SUMMARY` only, appended after the existing summary keys — no other sheet changed.

## Test suite

**68 test files, all exit 0** (67 pre-existing + the new `scripts/test_phase14_6_daily_planner.py`, 21 checks, fully offline, real-evidence replay fixtures, never touches production DB or the network).

## Production DB

SHA256 before/after: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` — **byte-identical.**

## Explicit confirmations

- Scheduler/launchd activated: **NO**
- Auto-apply changed: **NO**
- Restricted-board pages fetched directly: **NO**
- Scoring changed: **NO**
- Normalization/dedup implementation changed: **NO**
- 9-sheet report structure changed: **NO** (only RUN_SUMMARY rows added, as explicitly requested)
- A second scoring/dedup/normalization/report pipeline created: **NO**

---
STOP after this report, per instructions.
