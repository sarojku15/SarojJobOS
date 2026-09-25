# Phase 4 — Multi-Source Adapter Architecture

Offline-only architecture work: a common adapter contract (status +
capability model) so multiple job sources can plug into the existing,
validated SarojJobOS pipeline without changing scoring, eligibility,
freshness, deduplication, candidate matching, or worker semantics.
**Zero live network/browser requests were made to any source, including
Naukri, anywhere in this phase.**

## Final Adapter Inventory

| # | Source | Registered | `AdapterStatus` |
|---|---|---|---|
| 1 | NAUKRI | yes | **ENABLED** — real, live-validated job source |
| 2 | MOCK | yes | **ENABLED** — test adapter only |
| 3 | LINKEDIN | yes | NOT_ENABLED |
| 4 | HIRIST | yes | NOT_ENABLED |
| 5 | INDEED | yes | NOT_ENABLED |
| 6 | FOUNDIT | yes | NOT_ENABLED |
| 7 | INSTAHYRE | yes | NOT_ENABLED |
| 8 | CUTSHORT | yes | NOT_ENABLED |
| 9 | WELLFOUND | yes | NOT_ENABLED |
| 10 | SHINE | yes | NOT_ENABLED |
| 11 | TIMESJOBS | yes | NOT_ENABLED |
| 12 | IIMJOBS | yes | NOT_ENABLED |
| 13 | CAREER_PAGE | yes | NOT_ENABLED |
| — | GLASSDOOR | **no** | n/a — not registered, no adapter file, not in `config/searches.json` or `config/site_adapters.json`, not part of the agreed target list |

**Totals: 13 registered adapters. 2 `ENABLED` (of which 1 — Naukri — is
a real job source; MOCK is test-only). 11 `NOT_ENABLED` skeletons.**

`search_profile._default_sources()` → **`['NAUKRI']`** — the only source
that ever enters a candidate's default search plan.

## Architecture Inspected (Source of Truth)

Read and reused, unchanged in behavior: `source_registry.py`,
`source_adapter.py`, `naukri_adapter.py`, `search_profile.py`,
`query_planner.py`, `discover_local.py`, `canonical_job.py`,
`cross_source_dedup.py`, `job_id.py`, `job_eligibility.py`,
`experience_eligibility.py`, `location_taxonomy.py`, `freshness.py`,
`job_ranking.py`, `search_worker.py`, `search_submission.py`,
`config/application_schema.json`, `config/site_adapters.json`, existing
`test_*.py` files, prior Naukri reports, `CLAUDE.md`,
`.claude/skills/jobos/SKILL.md`, `.claude/commands/jobos.md`.

**Confirmed by inspection, not assumed:** scoring (`score_job.py`),
eligibility (`job_eligibility.py`, `experience_eligibility.py`),
location handling (`location_taxonomy.py`), and freshness
(`freshness.py`) contain zero source-specific logic — no file hardcodes
`"NAUKRI"` or any other source name. This architecture already satisfies
"do not create separate scoring implementations per source."

**Confirmed on deduplication:** `discover_local.deduplicate()` removes
exact `(source, job_id)` duplicates — this is **intra-source** dedup
(the 5-query Naukri validation already proved this: it correctly
collapsed the same Naukri job found under both "Bangalore" and
"Bengaluru" location queries, since both produce the same `job_id` for
the same `job_url`). **Cross-source** duplicate detection is a separate,
already-existing, additive layer (`cross_source_dedup.py` +
`canonical_job.py`) that flags candidate duplicates for ranking/human
review — it does not remove or merge rows, and this phase did not change
that. True cross-source *consolidation* into one canonical row with
multiple `source_urls` is **not yet built**, and was deliberately not
attempted here: no second live source exists yet to design or validate
that logic against. Recommended as its own explicit task once a second
live source exists (Phase 5+).

## Common Adapter Contract

`scripts/source_adapter.py` — additive only, zero behavior change for
`MockJobSourceAdapter` or `NaukriAdapter`:

- **`AdapterStatus`** enum: `ENABLED`, `NOT_ENABLED`, `REQUIRES_AUTH`,
  `BLOCKED`, `UNSUPPORTED`. `JobSourceAdapter.status` defaults to
  `ENABLED`, preserving every existing adapter exactly.
- **`AdapterCapability`** enum: `SEARCH`, `DETAIL`, `NATIVE_FRESHNESS`,
  `PAGINATION`, `APPLICATION_URL`, `COMPANY_METADATA`, `SALARY`,
  `REMOTE_FILTER`, `LOCATION_FILTER`. `JobSourceAdapter.capabilities`
  defaults to an empty `frozenset`.
- **`AdapterNotEnabledError`** exception (mirrors
  `AdapterBlockedError`/`AdapterTimeoutError`'s shape) — the explicit,
  honest "not implemented yet" signal; never a fake empty/successful
  result.
- **`SourceRunState`** extended with `not_enabled: bool` and
  `adapter_status: AdapterStatus | None` (both default `False`/`None` —
  no existing caller's behavior changes).
- **`SourceHealthRecord`** dataclass: an in-memory-only future reporting
  shape (`source`, `status`, `last_check_at`, `last_success_at`,
  `last_failure_at`, `error_type`, `http_status`, `classifier`,
  `query_count`, `job_count`, `latency_seconds`, `notes`). **Not
  persisted to any database table.**

`scripts/source_registry.py`:
- `discover_from_sources()` checks each adapter's `status` **before**
  calling `health_check()`. A `NOT_ENABLED` adapter is skipped with a
  recorded `SourceRunState(not_enabled=True, adapter_status=...)` and
  **zero network calls** — proven by regression tests using spy adapters
  that raise `AssertionError` if their `health_check()`/`search()` is
  ever actually invoked. `health_check_is_advisory` is untouched and
  remains orthogonal (it only matters once a source is already
  `ENABLED`).
- `source_status()` reports `adapter_status` (class-level read, no
  instantiation). `get_adapter_status(source)` is the corresponding
  direct helper.
- The module's own `__main__` self-check verifies all three states
  (`ENABLED`, registered-`NOT_ENABLED`, not-registered-at-all) against
  the complete, current 13-adapter roadmap, and confirms Glassdoor's
  continued absence.
- `ADAPTERS` contains exactly 13 entries, one per row in the inventory
  table above — no duplicate keys.

## Adapters Added (Skeletons Only)

Eleven files, each a minimal `JobSourceAdapter` subclass — the complete
agreed target list minus Naukri: `scripts/linkedin_adapter.py`,
`hirist_adapter.py`, `indeed_adapter.py`, `foundit_adapter.py`,
`instahyre_adapter.py`, `cutshort_adapter.py`, `wellfound_adapter.py`,
`shine_adapter.py`, `timesjobs_adapter.py`, `iimjobs_adapter.py`,
`career_page_adapter.py`.

Every one of them:
- `status = AdapterStatus.NOT_ENABLED`
- `capabilities = frozenset()` (declares nothing — honest, since nothing
  is implemented)
- `health_check()` and `search()` both raise `AdapterNotEnabledError`
  immediately if ever called directly
- Contains **no selectors, no API calls, no endpoints, no cookies, no
  headers, no anti-bot workarounds, no scraping logic, no invented
  URLs** — purely a registration placeholder plus a docstring pointing
  at the exact phased process (offline → 1 query → N queries →
  `ENABLED`) that Naukri already completed and that any real
  implementation must follow.

All eleven are registered in `source_registry.ADAPTERS`. **No adapter
file for Glassdoor exists, and Glassdoor is not registered anywhere.**

## Capabilities Declared

| Source | Status | Capabilities |
|---|---|---|
| NAUKRI | ENABLED | SEARCH, DETAIL, NATIVE_FRESHNESS, APPLICATION_URL |
| MOCK | ENABLED | SEARCH |
| LINKEDIN, HIRIST, INDEED, FOUNDIT, INSTAHYRE, CUTSHORT, WELLFOUND, SHINE, TIMESJOBS, IIMJOBS, CAREER_PAGE | NOT_ENABLED | (none) |

Naukri's declared capabilities deliberately **exclude** `PAGINATION`,
`COMPANY_METADATA`, `SALARY`, `REMOTE_FILTER`, `LOCATION_FILTER` —
`naukri_adapter.py`'s own docstring already documents "Page 1 only," and
none of the other four are actually extracted by `parse_detail_page()`.
Declaring these was a pure documentation/introspection addition — zero
lines of Naukri's actual fetch/parse/classify logic were touched.

## `search_profile._default_sources()` — A Required Downstream Fix

**Not optional.** `_default_sources()` returns "every registered,
ENABLED, non-MOCK adapter." Before this fix it returned every
*registered* non-MOCK adapter regardless of status — the moment new
sources were registered, every candidate's default search plan would
have silently started including unimplemented sources. Fixed:

```python
def _default_sources():
    return [
        s for s in source_registry.list_sources()
        if s != "MOCK" and source_registry.get_adapter_status(s) == AdapterStatus.ENABLED
    ]
```

Regression-tested to return exactly `["NAUKRI"]` with all 11 skeletons
registered, and to remain `["NAUKRI"]` generically — a dedicated test
registers a brand-new, synthetic `NOT_ENABLED` adapter at runtime (not
one of the 11 named sources) and confirms it, too, is excluded, proving
this guarantee holds for any future source, not just today's list.

## Job Model / Pipeline — Unchanged

The flow `source result → normalization → canonical URL → deduplication
→ freshness → eligibility → scoring → candidate match → ranking` was not
restructured. Every enabled adapter (today: only Naukri) feeds this
exact, unmodified pipeline. **Not modified:** the 100-point scoring
rubric, eligibility rules, location rules, experience rules, freshness
semantics, or candidate-profile semantics — verified by the existing
`test_scoring.py`, `test_job_eligibility*.py`,
`test_experience_eligibility.py`, `test_freshness.py`,
`test_job_ranking.py` all remaining green and unmodified.

## Cross-Source Deduplication — Reviewed, Not Weakened

A regression test (`test_cross_source_dedup_bangalore_bengaluru`)
confirms the existing, unmodified `cross_source_dedup.py` +
`canonical_job.py`:
- Correctly treats a job posted on two different sources under
  "Bangalore" vs. "Bengaluru" as the same canonical location (via
  `location_taxonomy.py`'s existing alias table) and flags it as a
  duplicate candidate.
- Correctly does **not** flag a genuinely different job (different city,
  unrelated description) at the same company/title.
- Correctly skips same-source pairs (that's `discover_local.deduplicate()`'s
  job, not this module's).

No change was made to either module's logic.

## Source Health Model — Data Structure Only

`SourceHealthRecord` exists purely as an in-memory dataclass for a
future reporting layer, regression-tested to fabricate zero data:
constructing one for a never-tested source yields `None`/`0`/empty for
every observational field. No database table, migration, or persistence
path exists for it.

## Unified Search Orchestration — Reviewed, Not Run Against New Sources

`search_worker.py` was not modified. A regression test proves, with a
spy adapter, that `discover_from_sources()` already supports "for each
enabled source: execute → normalize → dedup → eligibility → score →
match → report diagnostics," while a `NOT_ENABLED` source is skipped
with provably zero calls. This was never executed against any real new
source — only synthetic in-test fake adapters, added to and removed from
`source_registry.ADAPTERS` within the tests themselves.

## Excel/Reporting — Not Implemented

No Excel generation code was added. The existing dataclasses
(`job_ranking.RankingRecord`, `WorkItemResult`, `SourceHealthRecord`)
already carry every field a future `APPLY_TODAY` / `ALL_MATCHING_JOBS` /
`SOURCE_HEALTH` / `RUN_SUMMARY` sheet would need — no new schema was
required.

## Tests

**File:** `scripts/test_multi_source_adapter_architecture.py` — 14
checks, zero live network/browser calls:

1. Adapter interface shapes (`AdapterStatus`/`AdapterCapability`/`AdapterNotEnabledError`)
2. Source registration: all 11 skeletons registered, MOCK/NAUKRI unaffected, **GLASSDOOR confirmed absent**
3. Capability declarations (Naukri/Mock real; all 11 skeletons empty)
4. Unsupported adapter behavior: every skeleton raises `AdapterNotEnabledError`
5. Adapter selection: `get_adapter()` correct for all 11; a fictional unregistered name is rejected
6. `_default_sources()` excludes all `NOT_ENABLED` skeletons
7. `source_status()`/`get_adapter_status()` correctly distinguish `ENABLED`/`NOT_ENABLED`/unregistered, including an explicit Glassdoor-must-remain-unregistered assertion
8. `discover_from_sources()` skips `NOT_ENABLED` with a spy-proven zero-call guarantee
9. Naukri compatibility unchanged (`status`, `health_check_is_advisory`, capabilities)
10. Old-shape (pre-Phase-4) search-run snapshot still reconstructs correctly
11. Cross-source dedup: Bangalore/Bengaluru flagged, unrelated job not flagged, same-source pairs skipped
12. `SourceHealthRecord` fabricates no data for a never-tested source
13. Future extensibility: a brand-new synthetic `NOT_ENABLED` adapter is excluded from `_default_sources()` and skipped by `discover_from_sources()` with zero calls
14. All touched modules (all 13 registered adapter modules included) import/reload cleanly together

## Verification Results

```
Phase 4 architecture test (scripts/test_multi_source_adapter_architecture.py): 14/14 PASS
Full standalone regression suite (39 files): PASS=39 FAIL=0
python3 -m py_compile across all scripts/*.py: PY_COMPILE_ALL_OK
config/searches.json:       valid JSON, 11 sources, no Glassdoor
config/site_adapters.json:  valid JSON, Naukri enabled=true, all 10 others enabled=false, no Glassdoor, no duplicate keys
This report's own .json:    valid JSON (parsed with json.load)
source_registry.py __main__ self-check: PASS
```

## Production DB Safety

| | SHA-256 | Row counts |
|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | identical |

**Byte-identical. No DB migration occurred or was needed.**

## Files Touched Across Phase 4 (Complete, Final List)

**New adapter files (11):** `scripts/linkedin_adapter.py`,
`hirist_adapter.py`, `indeed_adapter.py`, `foundit_adapter.py`,
`instahyre_adapter.py`, `cutshort_adapter.py`, `wellfound_adapter.py`,
`shine_adapter.py`, `timesjobs_adapter.py`, `iimjobs_adapter.py`,
`career_page_adapter.py`

**New test file (1):** `scripts/test_multi_source_adapter_architecture.py`

**Modified, additive only (no behavior change to any existing enabled
adapter):**
- `scripts/source_adapter.py` — `AdapterStatus`, `AdapterCapability`,
  `AdapterNotEnabledError`, `SourceHealthRecord`, `SourceRunState`
  extension, base-class `status`/`capabilities` defaults
- `scripts/source_registry.py` — all 11 skeleton registrations,
  pre-flight `NOT_ENABLED` skip, `get_adapter_status()`,
  `source_status()` extension, `__main__` self-check
- `scripts/naukri_adapter.py` — one additive `capabilities` class
  attribute; no other line touched
- `scripts/search_profile.py` — `_default_sources()` filters to
  `ENABLED` only

**Config corrected:**
- `config/searches.json` — `"sources"` list now holds the 11-source
  roadmap (Naukri + 10 skeleton boards); Glassdoor removed
- `config/site_adapters.json` — Naukri `enabled: true`; every other
  source `enabled: false`; Glassdoor entry removed; Hirist/Shine/
  TimesJobs/Iimjobs entries added

**Documentation updated:** `CLAUDE.md` (Python Pipeline Scripts table;
"Adapter status and capability model" and "Current adapter state"
subsections under Source Adapter Principle; Tests table; "What Is Not
Yet Built" table), `.claude/skills/jobos/SKILL.md` (`/jobos search`
section and help text).

**Not touched anywhere in Phase 4:** `scripts/score_job.py`,
`scripts/job_eligibility.py`, `scripts/experience_eligibility.py`,
`scripts/location_taxonomy.py`, `scripts/freshness.py`,
`scripts/discover_local.py`, `scripts/canonical_job.py`,
`scripts/cross_source_dedup.py`, `scripts/search_worker.py`,
`scripts/job_ranking.py`, `scripts/tracker.py`, `config/profile.json`,
`data/applications/jobos.db`, `.claude/commands/jobos.md`.

## Confirmations

1. **Zero live requests to any source** (Naukri included) anywhere in
   Phase 4 — every skeleton's `health_check()`/`search()` raises
   immediately with no network-capable code reachable, confirmed by
   regression tests with spy adapters that fail loudly if ever actually
   called; no test instantiates a real `NaukriFetcher` or invokes
   Node/Playwright.
2. **Naukri behavior fully preserved and unchanged.**
   `NaukriAdapter.status == ENABLED` (default, unchanged),
   `health_check_is_advisory == True` (unchanged), its validated
   capabilities are declared for documentation purposes only, zero
   lines of its `search()`/`health_check()`/URL-building logic were
   ever modified.
3. **Glassdoor is fully absent:** not registered in `ADAPTERS`, no
   adapter file exists, not present in `config/searches.json` or
   `config/site_adapters.json`, not mentioned as part of the roadmap in
   `CLAUDE.md` or `SKILL.md`, and a regression test asserts its
   continued absence.
4. **LinkedIn implementation has not started** — `linkedin_adapter.py`
   remains the same `NOT_ENABLED` skeleton as every other unimplemented
   source; no live site inspection, fixture, parser, or live query has
   been performed for it.

## Exact Next Gate

**Phase 5: implement and validate one specific new source adapter**
(starting with LinkedIn, per the agreed roadmap) — following the same
phased process Naukri already completed: (1) inspect the live site and
review the ethical-automation policy, (2) offline implementation +
fixtures + unit tests, (3) one live query, (4) controlled multi-query
validation, (5) only then mark it `ENABLED` and declare its real
capabilities.

**Not started in this task.** Stopping here, per instruction.
