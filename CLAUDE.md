# SarojJobOS — Claude Code Project Guide

## Project Purpose

Automated job discovery, matching, scoring, tracking, preparation, and
human-approved application assistance for Saroj Kumar Nayak.

Do not rebuild this project. It has a working foundation. Extend it.

---

## Candidate Profile

- Azure-certified Senior SRE / DevOps / Cloud / Platform Engineer
- 11+ years experience
- AWS, Azure
- Kubernetes EKS/AKS, Docker, Helm
- Terraform, Ansible
- Jenkins, GitHub Actions, ArgoCD
- Prometheus, Grafana, Splunk, Dynatrace, OpenTelemetry
- Python, Shell, PowerShell
- Linux, Azure Synapse
- SLI/SLO/SLA, Error Budgets, Incident Management, RCA
- Chaos Engineering, Capacity Planning
- Certifications: AZ-104, SC-300, SC-100

Target roles: Senior SRE, Lead SRE, Senior DevOps Engineer, Lead DevOps
Engineer, Senior Cloud Engineer, Platform Engineer, Senior Platform
Engineer, Infrastructure Engineer, DevOps/Cloud Consultant (when
technically aligned).

Target locations: Bangalore, Hyderabad, Pune, Chennai, Remote/Hybrid.

---

## Scoring Model

The scoring model is implemented in `scripts/score_job.py`.
Do not replace or rewrite it. Extend it only when explicitly requested.

| Dimension                    | Points |
|------------------------------|--------|
| Core role alignment          | 20     |
| SRE/DevOps responsibilities  | 15     |
| Azure/AWS alignment          | 15     |
| Kubernetes                   | 10     |
| Terraform/IaC                | 10     |
| CI/CD                        | 10     |
| Observability                | 5      |
| Experience alignment         | 5      |
| Location/work model          | 5      |
| Overall/domain fit           | 5      |
| **TOTAL**                    | **100** |

Priority thresholds:

- A = 90–100
- B = 80–89
- C = 70–79
- Reject = below 70

The mandatory requirement gate runs before the numerical score.
A high numerical score must never override a hard mandatory skill gap.

---

## Profile Truth Rule

Never fabricate:

- Technologies, tools, or platforms
- Years of experience
- Employers or projects
- Responsibilities or achievements
- Certifications
- Leadership experience
- Metrics or KPIs
- Domain experience

If a job requires something not in the candidate profile, the system must
flag it. It must not invent it.

---

## Resume Variants

Five variants are defined in `config/profile.json`:

- SRE-A — primary variant
- DEVOPS-A
- AZURE-A
- AWS-A
- PLATFORM-A

Only use a resume variant when the actual content of that resume supports
the job requirements. The `resume_variant` field in `jobos.db` must be
populated by logic that compares the job requirements against the actual
resume content — not by assumption.

Current state: only `resumes/SarojKumarNayak_SRE_DevOps_11Yrs.pdf`
(SRE-A) exists. Other variant files are missing and must be created before
variant selection logic can be used.

---

## Architecture Pipeline

Every job flows through this pipeline in order. Do not skip stages.

```
Job Source
   ↓
Source Adapter (implements JobSourceAdapter from scripts/source_adapter.py)
   ↓
Normalized Job Object (via scripts/discover_local.py)
   ↓
Deduplication (in-batch + against SQLite; cross-source via scripts/cross_source_dedup.py)
   ↓
Freshness classification (scripts/freshness.py — date-based, never guessed; dead-link/availability checking itself is not yet implemented)
   ↓
Eligibility Gate (scripts/job_eligibility.py — experience + location, the one
authoritative check every ingestion path shares; independent of scoring)
   ↓
Mandatory Requirement Gate (scripts/score_job.py — evaluate_hard_reject)
   ↓
100-point Scoring Engine (scripts/score_job.py — score_job)
   ↓
Priority A / B / C
   ↓
SQLite System of Record (data/applications/jobos.db via scripts/tracker.py)
   ↓
Resume Variant Selection (not yet implemented)
   ↓
Application Preparation (not yet implemented)
   ↓
Human Approval (required — never skip)
   ↓
Browser / Application Workflow (not yet implemented)
   ↓
Application Status Tracking
   ↓
Interview Tracking
   ↓
Follow-up
   ↓
Reporting (scripts/generate_daily_report.py)
```

---

## Existing Core Components

These components are complete and tested. Do not replace them.

### Config

| File | Purpose |
|------|---------|
| `config/profile.json` | Candidate skills, certifications, resume variants, application rules |
| `config/searches.json` | Search schedule, sources, roles, locations, min score, exclude keywords |
| `config/site_adapters.json` | Ethical automation policy + per-source mode config |
| `config/application_schema.json` | Job record schema, 15-state status lifecycle, priority rules |

### Python Pipeline Scripts

| Script | Purpose |
|--------|---------|
| `scripts/job_id.py` | Deterministic SHA256 job ID generation |
| `scripts/discover_local.py` | Load, normalize, deduplicate jobs from JSON |
| `scripts/filter_jobs.py` | Check SQLite for already-tracked jobs |
| `scripts/score_job.py` | 100-point scoring engine + mandatory reject gate |
| `scripts/tracker.py` | SQLite connect and upsert module |
| `scripts/init_tracker.py` | Initialize DB schema |
| `scripts/ingest_job.py` | Single-job ingestion pipeline |
| `scripts/ingest_jobs.py` | Batch ingestion with min-score=70 gate |
| `scripts/discover_and_ingest.py` | Combined discovery + ingestion pipeline |
| `scripts/prepare_jobs.py` | Score + filter without committing to DB |
| `scripts/generate_daily_report.py` | HTML + CSV daily report from DB |
| `scripts/parse_job_input.py` | Parse structured text from `data/inbox/job_input.txt` |
| `scripts/process_job_input.py` | Orchestrates parse → ingest → report |
| `scripts/add_job.py` | Interactive CLI to add a real job to inbox |
| `scripts/query_planner.py` | Build source × role × location query matrix |
| `scripts/source_adapter.py` | Base adapter ABC + MockJobSourceAdapter + `AdapterStatus`/`AdapterCapability`/`AdapterNotEnabledError` |
| `scripts/source_registry.py` | Adapter registry (`ADAPTERS` dict); `discover_from_sources()` skips any `NOT_ENABLED` adapter before any network call |
| `scripts/naukri_adapter.py`, `hirist_adapter.py`, `iimjobs_adapter.py`, `apna_adapter.py` | The 4 `ENABLED`, live-validated direct-crawl adapters. See "Source Adapter Principle" → "Current adapter state" for full detail. |
| `scripts/naukri_fetcher.py` / `scripts/naukri_fetch_bridge.js` | Naukri's Playwright fetch layer (headless, `channel='chromium'`, configurable `JOBOS_NAUKRI_USER_AGENT`) |
| `scripts/naukri_parser.py` | Naukri search/detail page parsing + `SearchPageState` classifier |
| `scripts/linkedin_adapter.py`, `indeed_adapter.py`, `foundit_adapter.py`, `instahyre_adapter.py`, `cutshort_adapter.py`, `wellfound_adapter.py`, `shine_adapter.py`, `timesjobs_adapter.py`, `career_page_adapter.py`, `greenhouse_adapter.py`, `lever_adapter.py`, `ashby_adapter.py`, `web_search_discovery_adapter.py` | Registered but `NOT_ENABLED` skeleton adapters — no live behavior. See "Current adapter state" below. |
| `scripts/search_provider.py`, `search_provider_manager.py`, `search_provider_adapter.py`, `restricted_source_registry.py` | Multi-provider search layer (You/Tavily/Exa/Brave/Serper) that discovers the 7 restricted boards (LinkedIn/Indeed/Foundit/Instahyre/Cutshort/Wellfound/Shine) — failover only on real provider failures, never on an empty-but-successful result |
| `scripts/daily_search_planner.py` | Adaptive multi-wave query planner: cooldown, per-source/per-run budgets, coverage-driven expansion (not just fewer queries), honest known/unknown location-coverage semantics |
| `scripts/experience_eligibility.py`, `job_eligibility.py`, `location_taxonomy.py` | The one authoritative candidate-job eligibility gate (experience parsing/range logic + location matching), reused unchanged by `score_job.py`, `job_ranking.py`, and every ingestion path — never reimplemented per-caller |
| `scripts/job_ranking.py`, `freshness.py`, `canonical_job.py` | `RankingRecord` construction (eligibility + score + freshness + `requirement_type` + canonical location in one deterministic record), date-based freshness classification, and title/company/location normalization |
| `scripts/search_worker.py` | Background worker: claims one `search_queue` item, runs `discover_from_sources()` → normalize → dedup → eligibility → score → `candidate_job_matches` |
| `scripts/cross_source_dedup.py` | Read-only cross-source comparison layer (duplicate-candidate detection across sources — does not remove/merge rows itself) |
| `scripts/candidate_profile.py`, `candidate_profile_store.py`, `resume_extractor.py` | Resume-extracted candidate profile (DRAFT → CONFIRMED lifecycle); `candidate_search_profile` in the DB is the single authoritative source of `total_experience_years` |
| `api/` (FastAPI) + `web/` (static frontend) | HTTP API and browser UI: candidate profile, saved searches, results, dashboard, Excel report download, search-provider settings. Frontend is a passive renderer of API output only — no client-side re-scoring/re-eligibility |

### Naukri Playwright Probes (exploratory — not production adapters)

| Script | Purpose |
|--------|---------|
| `scripts/naukri_discovery_probe.js` | Homepage login/captcha/block detection |
| `scripts/naukri_search_probe.js` | Search page signal detection + screenshot |
| `scripts/naukri_detail_probe.js` | Job detail page body text + link dump |
| `scripts/naukri_job_structure_probe.js` | Extract job listing links from search results |
| `scripts/naukri_extractor.js` | Headless structured extractor (one hardcoded URL — prototype) |
| `scripts/naukri_manual_capture.js` | Browser console snippet for manual clipboard capture |

### Tests

| Script | What it validates |
|--------|-------------------|
| `scripts/test_scoring.py` | Score, priority, status for A / B / Reject fixtures |
| `scripts/test_tracker.py` | DB insertion and retrieval |
| `scripts/test_adapter_pipeline.py` | Adapter → normalize → score end-to-end |
| `scripts/test_registry_pipeline.py` | Registry → pipeline deduplication (two identical runs) |
| `scripts/test_query_orchestrator.py` | Query planner → registry orchestration |
| `scripts/test_multi_source_adapter_architecture.py` | AdapterStatus/AdapterCapability model, NOT_ENABLED skip behavior, skeleton adapters, Naukri compatibility, cross-source dedup |

Full current suite: run every `scripts/test_*.py` file. All tests must
pass before any significant change is considered complete.

### Database

- `data/applications/jobos.db` — SQLite, authoritative system of record
- 32-column `jobs` table with full application lifecycle
- Indexes on `status`, `priority`, `company`
- Unique constraint on `(source, job_id)` — deduplication enforced at DB level

SQLite is the authoritative system of record. Excel and Google Sheets are
reporting/tracking views, not competing databases.

Existing Excel tracker: `~/Downloads/Saroj_Job_Search_Tracker.xlsx`
Do not delete it. It has not yet been integrated into the project.

### Infrastructure

- `docker-compose.yml` — n8n container, port 5678
- `node_modules/playwright` — v1.63.0, Chromium installed and confirmed working (used live by the direct adapters)
- `.env` — minimal; credentials must never be committed to Git

---

## Source Adapter Principle

Every job source must implement `JobSourceAdapter` from
`scripts/source_adapter.py`. The `search(query: SearchQuery)` method must
return a list of raw job dictionaries.

Adapters must not:
- Score jobs
- Decide whether to apply
- Modify tracker records
- Submit applications

Register every new adapter in `scripts/source_registry.py` under the
`ADAPTERS` dict.

### Adapter status and capability model (added: Phase 4 architecture work)

Every adapter declares a class-level `status` (`source_adapter.AdapterStatus`:
`ENABLED`, `NOT_ENABLED`, `REQUIRES_AUTH`, `BLOCKED`, `UNSUPPORTED`) and a
`capabilities` frozenset (`source_adapter.AdapterCapability`: `SEARCH`,
`DETAIL`, `NATIVE_FRESHNESS`, `PAGINATION`, `APPLICATION_URL`,
`COMPANY_METADATA`, `SALARY`, `REMOTE_FILTER`, `LOCATION_FILTER`). A
source being *registered* in `ADAPTERS` does not mean it is *enabled* —
`source_registry.discover_from_sources()` skips any non-`ENABLED` adapter
before calling `health_check()` or `search()`, so a registered skeleton
makes zero network calls. Never mark an adapter `ENABLED` — and never
declare a capability — until it has a real, working implementation
backed by evidence (see the phased live-validation process below).

### Current adapter state

**DIRECT ENABLED, LIVE-VALIDATED (`ENABLED`, real direct crawl):**
- **Naukri** (`scripts/naukri_adapter.py`) — headless Chromium,
  `channel='chromium'`, configurable `JOBOS_NAUKRI_USER_AGENT`, advisory
  health check, native `jobAge` freshness filter. Validated via a
  single-query and a controlled 5-query live run (see
  `data/reports/naukri_ua_single_query_live_validation.md` and
  `data/reports/naukri_5_query_ua_freshness_live_validation.md`).
- **Hirist** (`scripts/hirist_adapter.py`) — headless Chromium, respects
  robots.txt `Crawl-delay: 10`. Live-validated (see
  `data/reports/phase11_public_multisource_completion.md`).
- **iimjobs** (`scripts/iimjobs_adapter.py`) — live-validated (see
  `data/reports/phase11_public_multisource_completion.md`).
- **Apna** (`scripts/apna_adapter.py`) — live-validated.

All 4 direct sources were re-validated with one controlled live query each
on 2026-09-25 (see
`data/reports/phase_remaining_items_4_direct_source_live_validation.md`):
all reachable, zero blocks, real results pass cleanly through the full
normalize → eligibility → score → freshness pipeline.

**AVAILABLE VIA SEARCH PROVIDER (`ENABLED` only when a provider API key is
configured; discovered through the multi-provider search layer —
`scripts/search_provider_manager.py`, `search_provider.py`,
`search_provider_adapter.py` — never a direct crawler for these 7):**
LinkedIn, Indeed, Foundit, Instahyre, Cutshort, Wellfound, Shine. The
manager fails over across You/Tavily/Exa/Brave/Serper on real provider
failures only (`AUTH_FAILED`/`QUOTA_EXHAUSTED`/`RATE_LIMITED`/`TIMEOUT`/
`NETWORK_ERROR`/`SERVER_ERROR`) — an empty-but-successful result never
triggers trying the next provider. See
`data/reports/phase14_search_provider_completion.md`.

**IMPORTANT gotcha (root-caused and fixed 2026-09-25):** every one of
these 7 adapters' `ENABLED` status is fixed once, at `search_provider_
adapter.py`'s first import, from whatever `*_API_KEY` values are
already in `os.environ` at that moment — never re-checked later. `.env`
is NOT loaded into `os.environ` automatically by the OS or Python; some
explicit code has to do it before that first import. `search_provider.py`
now does this itself, at its own module import time (calling
`env_config.load_env_file_into_environ()`), so every entrypoint that
transitively imports it — `api/main.py`, `scripts/submit_search.py`,
`scripts/run_search_worker.py`, or any future CLI script — sees real
keys correctly with zero per-script boilerplate required. Before this
fix, only `api/main.py` loaded `.env` (inline, explicitly) — a search
submitted via the CLI/worker path genuinely only searched the 4 direct
sources even with real provider keys configured, because that path
never loaded `.env` at all. See
`data/reports/phase_11_source_live_search_validation.md` and
`scripts/test_search_provider_source_coverage.py`.

**REGISTERED, OFFLINE SKELETON ONLY (`NOT_ENABLED` — no live behavior,
never live-validated, do not call production-ready):** a separate set of
plain-registry-key skeletons reserved for a hypothetical future authorized
*direct* crawler for the same 7 restricted boards above (`linkedin_adapter.py`,
`indeed_adapter.py`, `foundit_adapter.py`, `instahyre_adapter.py`,
`cutshort_adapter.py`, `wellfound_adapter.py`, `shine_adapter.py`), plus
`timesjobs_adapter.py`, `career_page_adapter.py`, `greenhouse_adapter.py`,
`lever_adapter.py`, `ashby_adapter.py`, `web_search_discovery_adapter.py`.

This is the complete, agreed target source list — every source above is
registered in `source_registry.ADAPTERS`. **Glassdoor is explicitly NOT
part of this list**: it was added to this documentation by mistake in an
earlier pass and has been removed (it is not registered, has no adapter
file, and does not appear in `config/searches.json` or
`config/site_adapters.json`). Do not re-add Glassdoor without a fresh,
explicit decision to do so.

Before turning any `NOT_ENABLED` direct-crawl skeleton into a real
implementation: inspect the current website behavior, review existing
probes or prior work for that source, and run the same phased,
evidence-based validation Naukri/Hirist/iimjobs/Apna went through (offline
implementation + tests → one live query → controlled multi-query → only
then mark `ENABLED`/declare capabilities). Do not implement ahead of
schedule without explicit approval. Do not attempt unauthorized direct
crawling of the 7 restricted boards — they go through the search-provider
layer only.

Do not assume every site permits the same automation method. For sites
where automated interaction is restricted, implement compliant discovery
and human handoff rather than attempting to bypass controls.

---

## Testing Principle

Every new adapter must include:

- Unit tests for normalization
- Fixture / test data (static JSON, not live website calls)
- Deduplication tests
- Failure handling (blocked page, missing field, timeout)
- Source-specific parsing tests

An adapter is not complete because one browser page loaded.

---

## Application Safety

These rules are non-negotiable:

- Human approval is required before every final application submission.
- CAPTCHA must never be bypassed by automation.
- MFA/OTP must remain human-handled.
- Do not create fake accounts.
- Do not bypass anti-bot protections.
- Do not submit applications without explicit human approval.
- Do not fabricate any application answer.
- If a website blocks automation, stop immediately and use a compliant
  human handoff workflow.

---

## Working Style for Claude Code

Work one controlled step at a time.

Before making any significant change:

1. Inspect the existing implementation.
2. Identify what can be reused (usually: most things).
3. List exactly which files will change.
4. Make the smallest change that achieves the goal.
5. Run existing tests and confirm they still pass.
6. Report exactly what changed.
7. Wait for explicit approval before the next major step.

Never rebuild something that already exists.

For every implementation request, first determine whether an existing
component can be extended rather than a new one created.

Do not create duplicate pipelines. All job sources use the same
normalize → deduplicate → score → track → report pipeline.

Do not modify `config/profile.json`, `scripts/score_job.py`, or
`scripts/tracker.py` without being explicitly asked to do so.

---

## What Is Not Yet Built

| Capability | Status |
|------------|--------|
| Real source adapters | 4 direct `ENABLED`, live-validated: Naukri, Hirist, iimjobs, Apna. 7 more (LinkedIn, Indeed, Foundit, Instahyre, Cutshort, Wellfound, Shine) available `ENABLED` only via the configured multi-provider search layer (You/Tavily/Exa/Brave/Serper), never direct crawl. A separate set of plain-registry-key skeletons for those same 7 (plus TimesJobs, career pages, Greenhouse, Lever, Ashby, web-search-discovery) remain registered `NOT_ENABLED`. Glassdoor is not part of the roadmap. |
| Freshness classification | **Built** (`scripts/freshness.py`) — deterministic, date-based, independent of scoring/eligibility |
| Dead-link / posting-availability checking | Missing (distinct from freshness classification above — this would mean verifying a URL still resolves to a live posting) |
| Resume variant files (DEVOPS-A, AZURE-A, AWS-A, PLATFORM-A) | Missing |
| Resume variant selection logic | Missing |
| Human approval workflow | **Built**: `PATCH /api/candidates/{id}/jobs/{job_id}/status` moves a job through `config/application_schema.json`'s existing lifecycle (SHORTLISTED → READY_FOR_APPROVAL → APPROVED → APPLICATION_STARTED/APPLIED → ...), scoped to the owning candidate, with a hard-enforced gate: APPLICATION_STARTED/APPLIED is unreachable unless already APPROVED. See `scripts/application_lifecycle.py` and `scripts/test_application_lifecycle.py`. No UI button for it yet (API only). |
| Application submission pipeline | Missing |
| Follow-up automation | Missing |
| Google Sheets integration | Missing |
| n8n workflows | Missing |
| macOS launchd scheduling | Ready-to-install template exists (`launchd/com.sarojjobos.dailysearch.plist`, Phase 7.2) — explicitly **not installed/loaded** (verified via `launchctl list`); installing it is a deliberate, separate, human-approved step |
| claude-job-skill integration | Not evaluated yet |
| Excel tracker integration | Not integrated |

Do not implement items from this list unless explicitly requested.
