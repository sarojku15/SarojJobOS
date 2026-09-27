# Architecture

## Two coexisting pipelines

JobOS grew from a single-candidate CLI tool into a multi-candidate web
app. Both still exist in this repository — this is not duplication to
clean up, it's an honest description of what's here:

1. **The current, primary system**: a FastAPI app (`api/`) + a plain
   HTML/CSS/JS frontend (`web/`), backed by SQLite, supporting any
   number of independent candidates. This is what `README.md`'s Quick
   Start describes, and what new users should use.
2. **The legacy, single-candidate CLI pipeline**: `config/profile.json`
   (Saroj's own profile), `data/inbox/job_input.txt`,
   `scripts/process_job_input.py`, `scripts/query_planner.py`,
   `scripts/generate_daily_report.py`, driven by the `/jobos` Claude
   Code slash command. Still functional, still tested, kept for
   backward compatibility — not the recommended entry point for a new
   user.

Both share the same underlying scoring/eligibility/dedup/freshness
logic (`scripts/score_job.py`, `scripts/job_eligibility.py`,
`scripts/cross_source_dedup.py`, `scripts/freshness.py`) — there is
exactly one scoring engine, never two.

## Component diagram (current system)

```mermaid
flowchart TD
    User[User / Browser] -->|HTTP| Web[web/ static frontend]
    Claude[Claude Code / Skills] -->|HTTP or direct DB read| API
    Web -->|fetch| API[FastAPI api/main.py]

    API --> SearchStore[api/search_store.py<br/>saved searches, runs]
    API --> ResultsStore[api/results_store.py<br/>scoped results, dashboard]
    API --> ProfileStore[api/profile_store.py<br/>candidate profile]

    SearchStore --> Worker[scripts/search_worker.py<br/>background run executor]
    Worker --> Registry[scripts/source_registry.py]
    Registry --> Direct[Direct adapters<br/>Naukri/Hirist/IIMJobs/Apna<br/>Playwright + Chromium]
    Registry --> Providers[Search-provider layer<br/>You/Tavily/Exa/Brave/Serper]

    Worker --> Pipeline[Normalize -> Dedup -> Eligibility -> Score -> Explain]
    Pipeline --> DB[(SQLite<br/>jobos_dev.db / jobos.db)]

    API --> Lifecycle[scripts/application_lifecycle.py<br/>status transitions]
    Lifecycle --> DB

    API --> Report[scripts/generate_run_report.py<br/>Excel export]
    Report --> DB

    DB --> Dashboard[Dashboard / Reports]
```

## Discovery pipeline (every job, either system)

```
Job Source
   |
Source Adapter (JobSourceAdapter)
   |
Normalized Job Object
   |
Deduplication (in-batch + cross-source)
   |
Freshness classification (date-based)
   |
Eligibility Gate (experience + location)
   |
Mandatory Requirement Gate (hard reject)
   |
100-point Scoring Engine
   |
Priority A / B / C / Reject
   |
SQLite (system of record)
   |
Resume/Profile provenance recorded per result
   |
Human review -> Shortlist -> Approve -> (you) Apply -> Track
```

See [SCORING.md](SCORING.md) and
[APPLICATION_LIFECYCLE.md](APPLICATION_LIFECYCLE.md) for the two gates
and the tracking states in full detail.

## Key modules

| Layer | File(s) |
|---|---|
| HTTP API | `api/main.py`, `api/db.py`, `api/schemas.py` |
| Search/run management | `api/search_store.py`, `scripts/search_worker.py` |
| Results/dashboard/export scoping | `api/results_store.py`, `scripts/generate_run_report.py` |
| Candidate profile | `api/profile_store.py`, `scripts/candidate_profile.py`, `scripts/candidate_profile_store.py`, `scripts/resume_extractor.py` |
| Resume storage | `scripts/resume_store.py` |
| Source adapters | `scripts/source_adapter.py` (base ABC), `scripts/source_registry.py` (registry), one file per source |
| Search-provider layer | `scripts/search_provider.py`, `search_provider_manager.py`, `search_provider_adapter.py`, `restricted_source_registry.py` |
| Eligibility | `scripts/job_eligibility.py`, `experience_eligibility.py`, `location_taxonomy.py` |
| Scoring | `scripts/score_job.py`, `scripts/score_explanation.py` |
| Dedup/freshness | `scripts/cross_source_dedup.py`, `scripts/canonical_job.py`, `scripts/freshness.py` |
| Application lifecycle | `scripts/application_lifecycle.py` |
| Resume tailoring | `scripts/resume_tailoring.py` (deterministic reorder/emphasis of the candidate's own profile content, never AI rewriting) |
| Company research | `scripts/company_research.py` (reuses `search_provider_manager.py` — no second search implementation) |
| Interview preparation | `scripts/interview_prep.py` (deterministic, evidence-based; reuses `score_job.py` for matched/missing skills) |
| Search scheduling | `scripts/scheduler.py` (persisted state only, no daemon/loop), `scripts/run_scheduled_searches.py` (CLI trigger) |
| Frontend | `web/*.html`, `web/app.js`, `web/style.css` (no build step) |
| Claude Skills | `.claude/skills/*/SKILL.md`, `.claude/commands/*.md` |

## Scheduling: the operating model

Recurring saved-search execution has exactly one source of truth and
one execution path, regardless of which external clock is used:

```
External trigger (n8n Schedule Trigger, OR cron/launchd)
        |
        v
POST /api/scheduler/run-due   (or scripts/run_scheduled_searches.py)
        |
        v
scripts/scheduler.py            <- persisted state only, no daemon/loop
   (finds due schedules, advances next_run_at via an
    optimistic compare-and-swap UPDATE, then...)
        |
        v
api/search_store.py trigger_run()   <- the SAME function "Run now" uses,
   (already refuses a second run while one is QUEUED/RUNNING)         including its concurrency guard
        |
        v
scripts/search_worker.py -> the existing discover/normalize/dedup/
                              eligibility/score pipeline
```

`search_schedules` (`migrate_v14_search_schedules.py`) is the only
persisted schedule state; nothing computes "is this due" independently
of `scripts/scheduler.py`, and nothing spawns a second run-guard beside
`trigger_run()`'s own. A crash between "claimed this tick" and
"triggered the run" can only ever cause a **missed** run, never a
**duplicate** one (the compare-and-swap `UPDATE ... WHERE next_run_at = ?`
only succeeds for whichever caller reads the still-current value).

**Pick exactly one external trigger** — never both at once (that's
just duplicate polling against the same `run-due` endpoint):

- **A. n8n** (recommended if n8n is already running): import
  `n8n/workflows/jobos_scheduled_search_runner.json`, point its
  `jobos_base_url` at your running API, activate it.
- **B. cron/launchd** (lightweight alternative): run
  `scripts/run_scheduled_searches.py` on a timer — same underlying
  `scheduler.run_due_schedules()` call, no daemon/loop mode in the
  script itself.

See [CONFIGURATION.md](CONFIGURATION.md) for setup and
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) for "a schedule never fires."

## Infrastructure

- **n8n** (`docker-compose.yml`, port 5678): two real, importable
  workflows now exist under `n8n/workflows/` — `JobOS Scheduled Search
  Runner` (the external clock for scheduling, above) and
  `JobOS Follow-Up Reminder` (read-only; logs due/overdue follow-ups
  via `GET /api/candidates/{id}/follow-ups`). Neither is active by
  default on import — activation is a manual step, and neither ever
  submits an application or writes anything n8n itself decided.
- **macOS launchd** (`launchd/com.sarojjobos.dailysearch.plist`):
  ready-to-install template for the legacy CLI pipeline's daily
  scheduled run (a separate concern from the scheduling above).
  Deliberately **not installed** — installing it is a separate,
  human-approved step.
- **Google Sheets**: mentioned as a planned tracker integration; not
  implemented. The Excel export (`.xlsx`, via `openpyxl`) is the real,
  working reporting mechanism today.

## Data storage

- **Production**: `data/applications/jobos.db` (SQLite) — the single
  system of record. Never migrated/edited by tests.
- **Dev**: `data/applications/jobos_dev.db` — auto-created, safe to
  reset.
- Both share the same schema, built additively via
  `scripts/migrate_v*.py` files, chained in `scripts/init_dev_db.py`
  (currently v1-v15 — v11 tailored resumes, v12 company research, v13
  interview prep, v14 search schedules, v15 `follow_up_date`).
