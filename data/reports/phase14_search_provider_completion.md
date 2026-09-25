# Phase 14 — Search-Provider Discovery Completion

**Date:** 2026-09-21
**Objective:** make LinkedIn, Indeed, Foundit, Instahyre, Cutshort, Wellfound and Shine participate in the normal JobOS search automatically, through a real search-provider API (Serper.dev) — never by scraping those seven sites directly — fully integrated into the existing pipeline.

## 1. Serper configured: **NO**

`SERPER_API_KEY` is not present in this environment. Per explicit instruction, no live request was made and no successful result was faked anywhere in this phase.

> **Implementation complete; live provider activation pending `SERPER_API_KEY`.**

## 2. Live provider validation performed: **NO**

Sections 3–16 below (per-source and aggregate result counts) are therefore all **0 / not run** — these are deliberately *not* filled in with offline-test-fixture numbers, which would misrepresent test data as a live result. A clearly-separated **Offline validation** section further down documents what *was* proven, using real (not synthetic) Phase 13 search-result data replayed through the new code with zero network calls.

| # | Metric | Value |
|---|---|---|
| 3 | LinkedIn result count | 0 |
| 4 | Indeed result count | 0 |
| 5 | Foundit result count | 0 |
| 6 | Instahyre result count | 0 |
| 7 | Cutshort result count | 0 |
| 8 | Wellfound result count | 0 |
| 9 | Shine result count | 0 |
| 10 | Total search-provider raw hits | 0 |
| 11 | Total valid job-detail URLs | 0 |
| 12 | Total normalized jobs | 0 |
| 13 | Total cross-source duplicates | 0 |
| 14 | Total eligible jobs | 0 |
| 15 | Total scored jobs | 0 |
| 16 | APPLY_TODAY count | 0 |
| 17 | Provider errors/retries | N/A — no live calls made (retry/backoff logic exists and is covered by an offline, mocked-HTTP test) |

## 18. Restricted-source status (all seven)

| Source | Final status (now) | Once `SERPER_API_KEY` is set | Offline parse rate (real evidence) |
|---|---|---|---|
| LinkedIn | `SEARCH_PROVIDER_NOT_CONFIGURED` | `SEARCH_PROVIDER_AVAILABLE` | 89% (9 real URLs) |
| Indeed | `SEARCH_PROVIDER_NOT_CONFIGURED` | `LIMITED` (URLs valid, no company ever parseable — documented, honest) | 0% (9 real URLs) |
| Foundit | `SEARCH_PROVIDER_NOT_CONFIGURED` | `SEARCH_PROVIDER_AVAILABLE` | 83% (6 real URLs) |
| Instahyre | `SEARCH_PROVIDER_NOT_CONFIGURED` | `SEARCH_PROVIDER_AVAILABLE` | 100% (9 real URLs) |
| Cutshort | `SEARCH_PROVIDER_NOT_CONFIGURED` | `SEARCH_PROVIDER_AVAILABLE` | 100% (5 real URLs) |
| Wellfound | `SEARCH_PROVIDER_NOT_CONFIGURED` | `SEARCH_PROVIDER_AVAILABLE` | 100% (3 real URLs) |
| Shine | `SEARCH_PROVIDER_NOT_CONFIGURED` | `SEARCH_PROVIDER_AVAILABLE` (untested) | No real data — Phase 13 got 0 Shine results for 2 query phrasings; its parser is implemented but unvalidated |

None of the seven is ever reported as `ENABLED_DIRECT` — that value does not exist in this codebase's vocabulary, and `source_capabilities.py`'s `final_status` for a search-provider board is always one of the `SEARCH_PROVIDER_*`/`LIMITED` values, never plain `ENABLED` (which remains reserved for a true direct adapter).

## 19. Direct-source status (unchanged)

Naukri, Hirist, IIMJobs, Apna: all still `ENABLED`, untouched this phase, zero behavioral change (verified: `search_profile._default_sources()` still returns exactly these four when `SERPER_API_KEY` is unset).

## 20/21. Production DB SHA256

- Before: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8`
- After: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8`
- **Byte-identical.** Row counts unchanged (`candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0`). All validation this phase used temp/dev databases only.

## 22. Full test count

**63 test files, all exit 0** (62 pre-existing + this phase's new `test_phase14_search_provider.py`, 76 checks / 0 failed). Frontend regression (`test_frontend_integrity.py`, `test_phase13_gui_ux_fixes.py`) also re-run clean.

## 23–25

- Scheduler changed: **NO**
- Auto-apply changed: **NO**
- Restricted-site pages fetched directly: **NO** — the only network activity anywhere in this phase's *code* is `search_provider.SerperProvider`'s HTTP calls to `google.serper.dev`, gated entirely behind `SERPER_API_KEY`, which is absent, so zero such calls were made. No LinkedIn/Indeed/Foundit/Instahyre/Cutshort/Wellfound/Shine page was fetched by anything built this phase.

---

## What was actually built

### Architecture

```
Candidate/Search Profile
        ↓
query_planner.build_queries_from_search_profile()   (existing, UNMODIFIED)
        ↓
source_registry.discover_from_sources()              (existing, UNMODIFIED)
   ├── Direct: NAUKRI / HIRIST / IIMJOBS / APNA       (existing adapters, untouched)
   └── Search Provider: LINKEDIN_SEARCH / INDEED_SEARCH / FOUNDIT_SEARCH /
       INSTAHYRE_SEARCH / CUTSHORT_SEARCH / WELLFOUND_SEARCH / SHINE_SEARCH
       (NEW — search_provider_adapter.py, ENABLED only when SERPER_API_KEY is set)
        ↓
discover_local.normalize_job()                        (existing, additively extended: discovery_source/job_source/discovery_query/completeness)
        ↓
canonical_job.derive_canonical_job() → cross_source_dedup.find_cross_source_duplicate_candidates()   (existing, UNMODIFIED)
        ↓
freshness.classify_freshness() → job_eligibility.assess_job_eligibility() → score_job.score_job()    (existing, UNMODIFIED)
        ↓
tracker.upsert_job() → candidate_job_matches            (existing, additively extended: 3 new nullable columns)
        ↓
generate_run_report.py — EXISTING 9-sheet Excel, 3 new appended columns
```

No second scoring engine, dedup algorithm, or report generator was created. `scripts/score_job.py`, `config/profile.json`, and the four existing direct adapters were never touched.

### Search-provider abstraction (`scripts/search_provider.py`)

`SearchProvider` protocol; `SerperProvider` (real HTTP client for Serper.dev, `SERPER_API_KEY`/`SERPER_GL`/`SERPER_TIMEOUT` from the environment only, 3-attempt exponential-backoff retry on 429/5xx); `RecordingProvider` (wraps a live provider, writes every response to a fixture file); `ReplayProvider` (serves a fixture deterministically, zero network, zero API cost). `is_serper_configured()` is a pure environment check — nothing constructs a live provider speculatively.

### Restricted-site registry (`scripts/restricted_source_registry.py`)

Pure, offline, no-network. For each of the seven sites: an exact-domain (never substring) hostname validator, a job-detail-URL regex, a title parser, and a canonical-URL builder. **Hostname validation was hardened relative to the uploaded reference implementation**, which used `domain not in netloc` — a substring check that would incorrectly accept a lookalike host such as `notlinkedin.com.evil.example`. `is_safe_url()` instead requires the host to be exactly the allowed domain or a genuine dot-separated subdomain of it (verified against 7 adversarial test cases, all correctly rejected).

Every title parser was built and verified against **real** search-result titles captured in Phase 13 (`scripts/web_search_evidence_capture.py`), not synthetic examples:

- LinkedIn: `"<Company> hiring <Title> in <Location>"` — 8/9 real results (the 9th uses a different phrasing; deliberately left as company=UNKNOWN rather than inferred from the URL slug, which this module treats as too close to fabrication).
- Indeed: role + location only, **no company field exists in Indeed's own titles at all** — verified against all 9 real results. Honestly returns company="" rather than mis-parsing.
- Foundit: `"<Title> at <Company> in <Location>"` — 5/6 real results (also extracts an experience-years hint when literally present in the title).
- Instahyre: `"<Title> job at <Company> - Instahyre"` — 9/9 real results.
- Cutshort: two real phrasings, both handled — 5/5.
- Wellfound: `"<Title> at <Company> • <Location>"` — 3/3.
- Shine: best-effort dash parser, **explicitly documented as unvalidated** — zero real Shine results exist from either phase.

### Adapters (`scripts/search_provider_adapter.py`)

One shared base class, seven thin subclasses (`LinkedInSearchProviderAdapter`, …), registered under new `*_SEARCH` keys in `source_registry.ADAPTERS` — deliberately separate from the pre-existing `LINKEDIN`/`INDEED`/… Phase 4 direct-crawl skeletons, which remain untouched and `NOT_ENABLED`. Every job record still carries `source="LINKEDIN"` (the real board) and `job_source="LINKEDIN"`; `discovery_source` is set to `"SEARCH_PROVIDER:SERPER"` so it's never confused with a future authorized direct adapter for the same board, and `discovery_query`/`completeness` are attached per job. `AdapterStatus` is fixed at class-definition time from `is_serper_configured()`, matching how every other adapter in this codebase determines its own status — this is exactly why `_default_sources()`, `discover_from_sources()`, and the query planner require **zero changes** to pick these sources up automatically once configured.

### CommonJob additions (`discover_local.py`, additive only)

`discovery_query` and `completeness` join Phase 13's `discovery_source`/`job_source`, both defaulting to `""`/`None` — zero behavior change for every existing caller.

### Schema + persistence (`migrate_v2_schema.py`, `tracker.py`, `search_worker.py`)

Three new nullable `jobs` columns (`discovery_source`, `discovery_query`, `completeness`), added via the project's existing, pre-established idempotent `_ensure_job_columns()` pattern (the same mechanism that already added `freshness`/`updated_at` etc. in an earlier phase). `tracker.upsert_job()` and `search_worker._upsert_job_identity_only()` now write them.

**A regression was found and fixed during this phase's own testing**: making these columns unconditionally required broke `test_registry_pipeline.py`, a legacy test that initializes its temp DB via `init_tracker.main()` alone (predating the v2 migration). Fixed by having both functions detect column presence via `PRAGMA table_info(jobs)` at call time and build their `INSERT` column list dynamically — a database that predates this migration keeps working exactly as it did before this phase, unconditionally. Full suite re-verified clean afterward.

### Capability reporting (`source_capabilities.py`)

Each of the seven boards' single capability record (still one row per board, never a confusing second row for its `*_SEARCH` registry sibling) now reports `search_provider_status` and a `final_status` using Part 12's exact vocabulary (`SEARCH_PROVIDER_AVAILABLE` / `SEARCH_PROVIDER_NOT_CONFIGURED` / `LIMITED`), computed honestly from whether `SERPER_API_KEY` is actually configured — never `ENABLED_DIRECT`.

### Reporting (`generate_run_report.py`)

Still exactly the same 9 sheets. `Discovered_Via`, `Discovery_Query`, and `Completeness` are appended at the **end** of the existing job-sheet column list — no existing column moved, nothing broken for anyone reading by position. `Discovered_Via` shows `"DIRECT"` for every existing job (blank/self-referential `discovery_source`) and the real `"SEARCH_PROVIDER:SERPER"` (or `"MANUAL_IMPORT"`) attribution otherwise — verified end-to-end against a real generated workbook in this phase's test suite.

### Freshness / 3-day safety (Part 9)

Serper's own `qdr:d`/`qdr:w`/`qdr:m` recency filter is used as a best-effort narrowing request only — never treated as proof of `<=3 days`. Real freshness is always re-derived by the **existing, unmodified** `freshness.classify_freshness()` from whatever date text a hit actually carries (a relative phrase like `"3 days ago"` parses correctly, using logic already built for Naukri; no hint at all correctly resolves to `UNKNOWN`). The **existing, unmodified** `APPLY_TODAY` gate in `generate_run_report.py` already requires `freshness_age_days is not None and <= 3` — so an `UNKNOWN`-freshness search-provider job structurally can never qualify for `APPLY_TODAY`, with zero new code.

### Cross-source dedup (Part 8)

No second dedup implementation was built. A search-provider job flows through `canonical_job.derive_canonical_job()` exactly like every other source, so the **existing, unmodified** `cross_source_dedup.find_cross_source_duplicate_candidates()` already detects a Naukri job and a search-provider-discovered LinkedIn job describing the same real posting as duplicate candidates — verified in this phase's test suite with a synthetic-but-realistic pair. Consistent with this component's own long-standing, deliberate design (a *candidate* detector, never an auto-merge — see its module docstring), duplicates are surfaced (both `ALL_MATCHING_JOBS`/`DUPLICATES` sheets, cross-referenced) and `APPLY_TODAY`'s existing highest-score-wins representative-picking naturally prefers the richer direct-source record (which, with full `jd_text`, scores far higher than a thin search-provider record) — this was not weakened or reimplemented.

### Manual import / employer-ATS fallback (Phase 13, unchanged)

Both remain exactly as built in Phase 13 — this phase did not touch `scripts/manual_import.py` or `scripts/employer_ats_resolver.py`.

## Offline validation (real evidence, zero network calls)

Phase 13's actual, dated `WebSearch` captures (31 real job URLs across 6 of the 7 sources) were replayed through the entirely new code path — `search_provider_adapter.py` → `restricted_source_registry.py` → the existing pipeline — via a `ReplayProvider`, proving the integration genuinely works end-to-end on real (not synthetic) data:

- All 6 sources with real evidence produce valid, correctly-parsed `CommonJob` records; Shine (0 real results) correctly returns an empty list, not an error or a fabrication.
- Hardened hostname validation correctly rejects 4 adversarial lookalike/malformed URLs.
- Canonical URL stripping (Indeed's tracking params) and construction (LinkedIn's job-id-based URL) are both deterministic across repeated calls.
- Duplicate hits within one response are de-duplicated; a cross-source Naukri+LinkedIn duplicate pair is correctly detected by the existing dedup module.
- `SerperProvider`'s retry logic was verified against a mocked HTTP layer for both a 429-then-success case and a persistent-timeout case.
- `build_site_query()` was verified for a **non-SRE profession** (`"Product Manager"`, Mumbai) — zero hardcoding.
- A search-provider job was scored, persisted, and rendered into a real generated Excel workbook with correct `Discovered_Via`/`Discovery_Query`/`Completeness` values.

## Files

**New:** `scripts/search_provider.py`, `scripts/restricted_source_registry.py`, `scripts/search_provider_adapter.py`, `scripts/test_phase14_search_provider.py`, this report.

**Modified (all additive):** `scripts/discover_local.py`, `scripts/migrate_v2_schema.py`, `scripts/tracker.py`, `scripts/search_worker.py`, `scripts/source_registry.py`, `scripts/source_capabilities.py`, `scripts/generate_run_report.py`, `scripts/test_phase13_broad_discovery.py` (3 assertions updated to reflect Phase 14's more specific `SEARCH_PROVIDER_NOT_CONFIGURED` status superseding Phase 13's coarser `LIMITED`/`MANUAL_IMPORT` placeholders).

**Not modified:** `scripts/score_job.py`, `config/profile.json`, `scripts/naukri_adapter.py`/`hirist_adapter.py`/`iimjobs_adapter.py`/`apna_adapter.py`, `scripts/cross_source_dedup.py`/`canonical_job.py`/`freshness.py`/`job_eligibility.py`, the seven Phase 4 direct-crawl skeleton adapters, `scripts/manual_import.py`, `scripts/employer_ats_resolver.py`, `launchd/`.

---
STOP after this report, per instructions. No Phase 15 started.
