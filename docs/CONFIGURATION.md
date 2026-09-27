# Configuration

JobOS runs fully with **zero configuration** (4 direct sources, no
credentials). Everything below is optional, and every variable has a
documented default.

## Environment variables (`.env`, copied from `config/jobos.env.example`)

### Search-provider API keys (unlocks 7 more sources)

| Variable | Purpose |
|---|---|
| `YOU_API_KEY` | You.com search API |
| `TAVILY_API_KEY` | Tavily search API |
| `EXA_API_KEY` | Exa search API |
| `BRAVE_API_KEY` | Brave search API |
| `SERPER_API_KEY` | Serper (Google) search API |

Configuring **any one** turns on `LINKEDIN`, `INDEED`, `FOUNDIT`,
`INSTAHYRE`, `CUTSHORT`, `WELLFOUND`, `SHINE` (see
[JOB_SOURCES.md](JOB_SOURCES.md)). `.env` is loaded automatically by
`scripts/search_provider.py` at import time (`env_config.
load_env_file_into_environ()`) — every entrypoint that imports it
(`api/main.py`, `scripts/submit_search.py`, `scripts/run_search_worker.py`)
sees real keys with zero per-script setup.

### Search-provider tuning (optional, all have working defaults)

| Variable | Purpose |
|---|---|
| `SEARCH_PROVIDER_ENABLED` | Master on/off switch for the provider layer |
| `SEARCH_PROVIDER_ORDER` | Failover order across configured providers |
| `SEARCH_PROVIDER_MAX_DAILY_REQUESTS` / `SEARCH_PROVIDER_MAX_MONTHLY_REQUESTS` | Budget caps |
| `SERPER_GL` / `SERPER_TIMEOUT` | Serper-specific tuning |

### Naukri browser automation

| Variable | Purpose |
|---|---|
| `JOBOS_BROWSER_HEADLESS` | Unset (default) = headless; `0` = headed (debugging only). Automated/background runs must never pop a visible window. |
| `JOBOS_NAUKRI_USER_AGENT` | Overrides the User-Agent Naukri requests present. Unset/empty falls back to a live-validated default (see `scripts/naukri_fetch_bridge.js`) — needed because native headless Chromium's own UA string triggers a block. |

## Configuration files (`config/`)

| File | Purpose |
|---|---|
| `profile.json` | Saroj's own original candidate profile/skills/resume-variant mapping — used by the legacy single-candidate CLI pipeline (`scripts/score_job.py` when run standalone, `scripts/process_job_input.py`, etc.). **Not** the source of truth for the multi-candidate API/web app — there, each candidate's profile lives in the database (`candidate_search_profile` table), built from their own resume upload/manual entry via `/profile`. |
| `searches.json` | Legacy CLI pipeline's search schedule/roles/locations/exclude-keywords. |
| `site_adapters.json` | Ethical-automation policy + per-source mode configuration. |
| `application_schema.json` | The job record schema, the 18-state status lifecycle, and priority-score thresholds — shared by both pipelines. |
| `career_pages.json` | Company boards for the Greenhouse/Lever/Ashby adapters (empty by default — no company hardcoded). |
| `search_planner.json` | Daily-search-planner tuning (cooldowns, per-source/per-run budgets). |
| `jobos.env.example` | Template for `.env` — copy it, never commit the real `.env`. |

## Database

- **Production**: `data/applications/jobos.db` — treat as sacred; never
  edit or migrate it directly. It's excluded from git (`data/applications/`
  in `.gitignore`).
- **Dev**: `data/applications/jobos_dev.db` — created automatically by
  the API on first request; safe to delete and let it regenerate.
  Initialized/migrated by `scripts/init_dev_db.py`.

## Ports

The app itself takes `--port` as a plain `uvicorn` flag (no hardcoded
port in code); this project's own docs/scripts consistently use
**8420** by convention. `docker-compose.yml` runs n8n on **5678**
(`N8N_PORT`).

## Scheduling & automation

No new environment variable is required for scheduling itself — a
schedule's state (enabled/frequency/timezone/next_run_at) is entirely
persisted in the database via
`PUT /api/searches/{id}/schedule`, not `.env`. What you configure is
**which external trigger actually calls it periodically** (pick one,
see [ARCHITECTURE.md](ARCHITECTURE.md)'s "Scheduling" section):

- **n8n**: `docker compose up -d` starts the container already defined
  in `docker-compose.yml`; open `http://localhost:5678`, import
  `n8n/workflows/jobos_scheduled_search_runner.json` and (optionally)
  `n8n/workflows/jobos_follow_up_reminder.json`. Each workflow's own
  "JobOS Config" node holds `jobos_base_url` (default
  `http://localhost:8420`) — edit it if your API runs elsewhere. The
  follow-up workflow additionally needs your own `candidate_id` filled
  in (deliberately left blank in the committed file — never a real id
  committed to the repo). Neither workflow activates itself on import.
- **cron/launchd**: run
  `.venv/bin/python3 scripts/run_scheduled_searches.py --once --db data/applications/jobos_dev.db`
  on whatever interval you like (`--once` is required — there is no
  daemon/loop mode; the script always does a single bounded pass and
  exits, safe to invoke repeatedly).

Company research and interview prep need no new configuration beyond
the search-provider keys above (interview prep's company-specific
questions simply don't appear without a linked research record; that's
not an error).

## Browser automation

Playwright (`node_modules/playwright`, Chromium) powers the direct
Naukri/Hirist/IIMJobs/Apna adapters. Install the browser binary once
per machine: `npx playwright install chromium` (cached outside the
repo, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)).

## Claude configuration

No environment variable is required for Claude Code/Skills to work —
they're discovered from `.claude/skills/*/SKILL.md` and
`.claude/commands/*.md` in this repository automatically. See
[CLAUDE_GUIDE.md](CLAUDE_GUIDE.md).

## What is never read from `.env`

Candidate identity, resumes, and search criteria are never
environment-configured — they're created per-candidate through the UI/
API and live in the database. There is no "your name" or "your resume
path" environment variable.
