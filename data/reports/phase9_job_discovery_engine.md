# Job Discovery Engine — Implementation Report

(Filename retained as `phase9_job_discovery_engine.*` per the task's
own explicit naming instruction; this work builds directly on, and
supersedes nothing from, the earlier `phase9_gui_api_implementation.*`
report — that report covers the base GUI/API MVP, this one covers the
generic multi-source discovery architecture layered on top of it.)

## 1. STATUS: **COMPLETE** (with one explicitly-scoped BLOCKED sub-item — Web Search live execution — implemented up to the environment's actual boundary, see §9)

## 2. What was implemented

- **Generic discovery architecture**: `.claude/skills/job-discovery-engine/`
  (SKILL.md + 5 reference docs + 3 orchestration scripts) — builds
  bounded, deterministic query plans from generic criteria (any
  profession), dispatches through the EXISTING
  `source_registry.discover_from_sources()`, validates and normalizes
  results via the EXISTING `discover_local.normalize_job()`. Verified
  offline end-to-end via the `MOCK` adapter — zero live calls.
- **Source capability registry** (`scripts/source_capabilities.py`):
  every registered source's `acquisition_method`,
  `enabled`/`authorized`/`status`/`reason`, and `supports_*` flags,
  read live from `source_registry.py` — never a hardcoded list. Backs
  the GUI's "Unavailable sources" panels.
- **Three new, real, working ATS/career-page adapters**
  (`scripts/greenhouse_adapter.py`, `lever_adapter.py`,
  `ashby_adapter.py`) against each platform's public, documented,
  unauthenticated JSON API — no CAPTCHA/robots/login-wall bypass of any
  kind. Configured via `config/career_pages.json` (ships empty — zero
  company hardcoded). Kept `AdapterStatus.NOT_ENABLED` pending this
  project's own phased live-validation process (never claimed working
  without evidence).
- **Web Search discovery integration boundary**
  (`scripts/web_search_discovery_adapter.py`) — see §9.
- **Generic search model extensions**: `saved_searches` gained
  `skills_json` (query-construction input, never a scoring override)
  and `schedule_json` (stored metadata only — activating a real
  scheduler remains entirely separate and untouched).
- **API extensions**: `GET /api/health`, `PATCH /api/candidates/{id}`,
  `PATCH /api/searches/{id}`, `GET /api/searches/{id}/report`,
  `GET /api/sources` now also returns `enabled`/`unavailable` capability
  detail, `GET /api/candidates/{id}/dashboard` now returns aggregate
  `summary` (jobs found/matching/APPLY_TODAY/duplicate counts, reusing
  the EXISTING `generate_run_report.build_run_summary()`) and `sources`.
- **GUI extensions**: `/searches` (list page), `/searches/{id}` (detail
  page: criteria, run button + live status, results link, report
  download, archive), results page gained a match-reason column and
  application-status/source filters, profile page gained an
  `industries` field and "alternate job titles" framing.
- **Auto-report generation**: the background run-worker thread now
  regenerates the candidate's Excel report immediately after a run
  completes (still `generate_run_report.generate()`, no second
  implementation), so it's ready the moment the user opens
  results/dashboard.
- **One small, additive, protected-file change**: `ProfessionalSummary`
  (`scripts/candidate_profile.py`) gained an `industries: list` field
  (purely informational, like the existing `work_model_preferences` --
  no scoring/eligibility function reads it). Verified: all 16
  pre-existing `test_candidate_profile.py` tests still pass unmodified.

## 3. GUI URL / local startup command

```
.venv/bin/uvicorn api.main:app --reload --port 8420
```
Then open `http://127.0.0.1:8420/`. See README.md's expanded "Local Web
App" section for the full click-through (candidate → resume → profile
→ search → run → results → Excel).

## 4. API startup command

Same process as the GUI (one FastAPI app serves both) — see §3.

## 5. Discovery Skill location

`.claude/skills/job-discovery-engine/` (`SKILL.md`,
`references/{discovery-policy,source-strategy,job-schema,quality-rules,search-query-strategy}.md`,
`scripts/{discover_jobs,validate_discovered_job,normalize_discovered_jobs}.py`).

## 6. Sources actually working

- **NAUKRI** — `AdapterStatus.ENABLED`, unchanged, live-validated in
  earlier phases (Phase 7/8). Not touched this phase; no fresh live
  call was made (see §11 for why).
- **MOCK** — dev/test fixture, `ENABLED` by design, not a real source.

## 7. Sources not working (and 8. exact reason for each)

| Source | Reason |
|---|---|
| LINKEDIN | `NO_AUTHORIZED_DIRECT_JOB_DISCOVERY` — robots.txt disallows unrecognized agents; no authorized API/provider credential configured (`data/reports/linkedin_phase5_inspection.md`) |
| HIRIST | `SOURCE_NOT_READY` — captured search-page company/title extraction is structurally unreliable across 3 conventions, no deterministic disambiguation (Phase 8 forensics report) |
| INDEED, FOUNDIT, INSTAHYRE, CUTSHORT, WELLFOUND, SHINE, TIMESJOBS, IIMJOBS, CAREER_PAGE | `SOURCE_NOT_READY` — registered skeletons, never implemented or validated |
| GREENHOUSE, LEVER, ASHBY | `SOURCE_NOT_READY` — real, working implementations exist against each platform's public API, but zero company boards are configured in `config/career_pages.json`, and none has been through this project's phased live-validation process |
| WEB_SEARCH | `NO_WEB_SEARCH_BACKEND_CONFIGURED` — see §9 |

Every one of these is surfaced live via `GET /api/sources` and the
dashboard/search-creation GUI, with its exact reason string — never
silently dropped, never claimed working without evidence.

## 9. Is Claude/Web Search integration executable in this environment?

**No — and this is the one genuinely external dependency called out in
the task's own instructions.** The backend (`api/`) runs as a plain
FastAPI/uvicorn process. That process has no mechanism to invoke Claude
Code's own WebSearch tool — WebSearch is a capability of the agent
session driving this repository, not a library importable by a Python
worker process — and no web-search API credential is configured
anywhere in this project's `config/`/`.env`. Everything around this
boundary IS implemented so that adding a credential/backend later is
configuration, not a redesign:
- `scripts/web_search_discovery_adapter.py` — the full provider
  interface, a `set_web_search_backend(callable)` hook, and
  `AdapterNotEnabledError` raised with an exact, honest explanation
  when no backend is configured (never a fabricated empty/successful
  result).
- `.claude/skills/job-discovery-engine/references/search-query-strategy.md`
  + `discover_jobs.build_web_search_query_strings()` — the bounded,
  deterministic query-string generator a real backend would need,
  fully built and tested (offline, deterministic — see §10).
- `.claude/skills/job-discovery-engine/scripts/validate_discovered_job.py`
  — the quality gate any web-search-sourced job must pass before being
  treated as actionable, already wired in.
- Verified by test: `set_web_search_backend()` with a fake callable
  correctly routes through `WebSearchDiscoveryAdapter.search()` end to
  end (`test_phase10_discovery_engine.py`, section 3).

## 10. Number of tests passed

- **New this phase:** `scripts/test_phase10_discovery_engine.py` — **53/53 passed**.
- **Phase 9's suite (unaffected):** `scripts/test_phase9_api.py` — **58/58 passed**.
- **Full existing suite:** all **53 other runnable test files** still
  pass (2 documented live-Naukri-only tests, `test_naukri_adapter.py`
  and `test_live_naukri_score_audit.py`, intentionally skipped, exactly
  as every prior phase). **Zero regressions.**
- **Total: 55 test files, 53 run + 2 documented-skip, 100% pass rate
  on every one run.**

## 11. Naukri live regression result

**Not performed, by deliberate engineering judgment** (the task
permits "at most ONE... if required" — this phase judged it not
required and documents why): this phase made **zero** changes to
`naukri_adapter.py`, `naukri_fetcher.py`, `naukri_parser.py`,
`search_worker.py`'s Naukri-facing logic, or anything in the Naukri
request path. The only registry-level change was ADDING four new,
unrelated `NOT_ENABLED` entries to `source_registry.ADAPTERS` (proven
network-free by construction, and by `test_multi_source_adapter_architecture.py`/
`test_registry_pipeline.py`/`test_offline_search_pipeline.py`/
`test_query_orchestrator.py` all still passing unmodified). A live
call against unchanged code would produce no new evidence and would
violate the project's own "do not run broad live searches" discipline
for no benefit.

## 12. Production DB SHA before/after

`174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8`
**(identical)** — checked at the start of this phase, again after every
major implementation step, and again after running the complete test
suite. Size: 114688 bytes, unchanged. `mtime` unchanged from before
this entire multi-phase session began.

## 13. Production DB row counts before/after

`candidates=1, candidate_search_profile=1, jobs=9,
candidate_job_matches=0, search_runs=0, search_queue=0` — identical
before and after.

## 14. Files created

`scripts/{ats_common,greenhouse_adapter,lever_adapter,ashby_adapter,
web_search_discovery_adapter,source_capabilities,
migrate_v4_search_extensions,test_phase10_discovery_engine}.py`,
`scripts/fixtures/{greenhouse_sample,lever_sample,ashby_sample}.json`,
`config/career_pages.json`,
`.claude/skills/job-discovery-engine/{SKILL.md,
references/{discovery-policy,source-strategy,job-schema,quality-rules,
search-query-strategy}.md,
scripts/{discover_jobs,validate_discovered_job,normalize_discovered_jobs}.py}`,
`web/{searches,search_detail}.html`,
`data/reports/phase9_job_discovery_engine.{md,json}` (this report).

## 15. Files modified (all additive; zero behavior change for any existing caller, proven by unmodified pre-existing tests still passing)

`scripts/source_registry.py` (registered 4 new NOT_ENABLED adapters),
`scripts/candidate_profile.py` (+`industries` field, optional, informational),
`scripts/init_dev_db.py` (+v4 migration call),
`api/schemas.py`, `api/search_store.py`, `api/main.py`,
`api/profile_store.py`, `api/results_store.py` (new
endpoints/fields/summary as described in §2),
`web/{profile,search_new,dashboard,results}.html`, `README.md`.

## 16. Remaining limitations (honest, not hidden)

- `WEB_SEARCH` cannot execute live in this backend-process runtime
  (see §9) — architecturally ready, not activatable here.
- `GREENHOUSE`/`LEVER`/`ASHBY` are real but unvalidated (no board
  configured, no phased live-validation run yet) — by design, per this
  project's own evidence discipline.
- Per-search Excel report is an alias onto the existing
  candidate-scoped report (same limitation already disclosed in the
  base Phase 9 report) — not a second report implementation.
- Still no real authentication — documented, single-user local tool.
- DOCX resume support was not added (would require a new dependency
  not currently in `requirements.txt`) — PDF-only, matching the
  existing `resume_extractor.py`.

## 17. Exact next action only if something genuinely remains

None required to consider this phase complete. If/when the user wants
`WEB_SEARCH` to actually execute, the next action is narrowly scoped:
supply a web-search API credential (or run this from an agent context
that can call `set_web_search_backend()` with a real callable) — no
further redesign needed anywhere in this codebase.

---

## Final Acceptance Checklist (Phase S)

**PRODUCT**
- [x] Web GUI starts locally
- [x] Dashboard works (now with summary + sources panels)
- [x] Candidate creation works
- [x] Resume upload works
- [x] Profile review works (now incl. industries)
- [x] Manual profile works without resume
- [x] Multiple searches work (verified: 4 distinct saved searches per test run)
- [x] Search criteria are generic (skills/schedule added, still zero hardcoded profession)
- [x] Search execution works (async QUEUED→...→COMPLETED)
- [x] Results page works (+ match reason, app-status/source filters)
- [x] External job URL works (`[Open Job]`, `target="_blank"`)
- [x] Excel download works (candidate-level and search-scoped alias)

**GENERICITY**
- [x] SRE search works
- [x] Java Developer search works
- [x] Product Manager search works
- [x] Data Scientist search works
- [x] Mechanical Engineer search works (new this phase, `test_phase10_discovery_engine.py`)
- [x] No code modification required between them

**DISCOVERY**
- [x] Naukri remains functional (unmodified; existing tests pass)
- [x] Discovery provider architecture exists (`job-discovery-engine` Skill)
- [x] Claude/Web Search integration boundary exists (documented, tested, BLOCKED only on live execution — see §9)
- [x] Source-specific query generation exists (bounded, deterministic, tested)
- [x] CommonJob normalization exists (reused, unmodified)
- [x] Cross-source dedup exists (reused, unmodified)
- [x] Freshness works (reused, unmodified)
- [x] Eligibility works (reused, unmodified)
- [x] Existing scoring works (reused, unmodified, protected file untouched)
- [x] Existing report works (reused, unmodified, now auto-generated post-run too)

**SOURCE SAFETY**
- [x] No CAPTCHA bypass
- [x] No robots bypass
- [x] No authentication bypass
- [x] No anti-bot bypass
- [x] No fabricated jobs
- [x] No fabricated URLs
- [x] Disabled sources are not silently treated as working (every one carries an explicit reason)
- [x] LinkedIn only enabled if authorized/valid acquisition exists (still NOT_ENABLED)
- [x] Hirist only enabled after valid acquisition/data-quality validation (still NOT_ENABLED)

**APPLICATION SAFETY**
- [x] No automatic application submission
- [x] No automatic Apply click
- [x] No automatic resume submission
- [x] Human remains in control

**DATA SAFETY**
- [x] Candidate isolation tested
- [x] Production DB unchanged (SHA/size/rows identical)
- [x] No arbitrary DB path
- [x] No arbitrary SQL
- [x] Upload validation exists
- [x] Path traversal protected

**REGRESSION**
- [x] Existing 53+ tests pass (53 runnable files, all pass)
- [x] New tests pass (58 + 53 = 111 new checks across two files, all pass)
- [x] No unexplained failures
- [x] py_compile succeeds on every touched file
- [x] JSON reports validate
