# Development Guide

For setup, see [GETTING_STARTED.md](GETTING_STARTED.md) or
[../CONTRIBUTING.md](../CONTRIBUTING.md). This page is about the
codebase itself — the actual structure, not an idealized one.

## Repository structure (as it actually exists)

```
api/            FastAPI app: main.py (all routes), db.py (dev/prod DB
                resolution), profile_store.py, applications_store.py,
                results_store.py, search_store.py, schemas.py,
                search_provider_settings_api.py
web/            Static HTML/CSS/JS frontend -- no build step, no
                framework; served directly by api/main.py's FileResponse
                routes
scripts/        Everything else: 200+ files -- source adapters,
                scoring, profile/resume extraction, the scheduler,
                migrations, and every scripts/test_*.py test file
config/         application_schema.json (lifecycle states), searches.json,
                site_adapters.json, jobos.env.example, profile.json.example
n8n/workflows/  The two real, committed n8n workflow JSON files
data/fixtures/  Tracked, synthetic test fixtures (resume PDF, mock job
                JSON) -- never real personal data
docs/           Everything you're reading now
launchd/        macOS-only, optional, not-installed-by-default automation
                template -- see its own README.md
```

There is **no** `api/search/` or `api/providers/` subdirectory —
search-provider logic lives in `scripts/search_provider.py` and
`scripts/search_provider_manager.py`; the API surface for it is
`api/search_provider_settings_api.py`. Job source adapters are flat
files directly under `scripts/` (`naukri_adapter.py`,
`linkedin_adapter.py`, etc.), registered in `scripts/source_registry.py`.

## Architecture in one pass

- **FastAPI** (`api/main.py`) is the only HTTP surface. It has no
  separate ORM — every route opens a raw `sqlite3` connection via
  `api/db.py`.
- **`api/db.py`** decides dev (`jobos_dev.db`) vs. production
  (`jobos.db`) based on the `JOBOS_DB_PATH` environment variable, read
  once at import time. Never add a second way to pick the database —
  this is the one authoritative switch.
- **The web frontend** (`web/*.html`) is plain HTML/CSS/vanilla JS —
  no React/Vue/build step. Each page fetches JSON from the API itself.
- **SQLite** is the only datastore. Schema changes go through a new
  `scripts/migrate_vN_*.py` file (see existing ones for the pattern),
  never a hand-edited `ALTER TABLE` run once and forgotten.
- **Search** = source adapters (direct crawl) + the search-provider
  layer (API-key-backed discovery for 7 boards JobOS can't crawl
  directly) → normalize → dedupe → eligibility gate → scoring.
- **Scoring** (`scripts/score_job.py`) is a pure function:
  `score_job(job, candidate_profile)` — deterministic, no I/O, no
  randomness. See [MATCHING.md](MATCHING.md) / [SCORING.md](SCORING.md).
- **Application Tracker** (`scripts/application_lifecycle.py`,
  `scripts/application_follow_ups.py`) enforces the 18-state lifecycle
  server-side — see [APPLICATION_LIFECYCLE.md](APPLICATION_LIFECYCLE.md).
- **Resume tailoring** (`scripts/resume_tailoring.py`) and **interview
  prep** (`scripts/interview_prep.py`) are both deterministic,
  rule-based — no LLM call anywhere in this codebase's own pipeline.
- **Company research** (`scripts/company_research.py`) reuses the same
  search-provider layer search uses — never a second implementation.
- **n8n** talks to JobOS only through its HTTP API — never opens
  SQLite directly. See [N8N_SETUP.md](N8N_SETUP.md).

## Where to look when adding...

| You want to add... | Start here |
|---|---|
| A new job source (direct crawl) | `scripts/source_adapter.py` (the `JobSourceAdapter` base class + `AdapterStatus`/`AdapterCapability`), then a new `scripts/<name>_adapter.py`, registered in `scripts/source_registry.py`'s `ADAPTERS` dict. Follow the phased validation process in `CLAUDE.md`'s "Source Adapter Principle" — never mark it `ENABLED` without real, live-validated evidence. |
| A new search provider | `scripts/search_provider.py` — add a new provider class alongside `YouProvider`/`TavilyProvider`/etc., add its env key to `PROVIDER_ENV_KEYS`, and register it in `scripts/search_provider_manager.py`'s failover list. |
| A scoring rule | `scripts/score_job.py` — read [SCORING.md](SCORING.md) first; this is a stable, tested contract (`scripts/test_scoring.py`, `scripts/test_experience_dimension_scoring.py`). Discuss behavior changes in an issue first. |
| A UI feature | The relevant `web/*.html` file + its inline `<script>` (no separate JS framework/build); add the backing route in `api/main.py` if needed. |
| An API endpoint | `api/main.py` — follow the existing pattern: open a connection via `db_mod.get_conn()`, validate candidate ownership via `profile_store.get_candidate()`, close the connection in a `finally`. |
| An Application Tracker feature | `scripts/application_lifecycle.py` / `scripts/application_follow_ups.py` — the approval-gate and history-preservation rules are load-bearing; see [APPLICATION_TRACKER.md](APPLICATION_TRACKER.md). |
| A test fixture | `data/fixtures/` — follow the existing convention (synthetic names, fictional data only); see `scripts/generate_synthetic_resume_pdf.py` for how the one real-PDF fixture was built. |

## Testing

Every `scripts/test_*.py` file is a standalone script (`python3
scripts/test_whatever.py`), not a pytest suite — run them all with:
```bash
for f in scripts/test_*.py; do .venv/bin/python3 "$f"; done
.venv/bin/python3 scripts/test_frontend_integrity.py
```
Most tests redirect the relevant module's `DB_PATH`/`DATA_DIR` to a
`tempfile.mkdtemp()` directory before running — never open the real
`data/applications/jobos.db` or `jobos_dev.db` in a test. A handful of
tests that specifically verify production-safety behavior record that
file's SHA-256 before and after and assert it's unchanged — follow
that pattern if your test needs to touch anything production-adjacent.
