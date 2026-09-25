# Saroj Job Search OS

Automated job discovery, matching, application preparation,
browser-assisted application, tracking and follow-up system.

## Candidate

Saroj Kumar Nayak

## Architecture

- n8n — workflow orchestration
- Claude/Anthropic — JD analysis and application intelligence
- Google Sheets — job/application tracker
- Playwright — browser automation
- Python — utilities and scoring
- macOS launchd — scheduling

## Local Web App (Phase 9 / Phase 10)

A generic job-search web app -- any profession, not just SRE/DevOps --
runs locally on top of the existing pipeline: create candidate → upload
resume (or skip) → review/edit profile → confirm → create one or more
saved searches → run → see results → download the existing 9-sheet
Excel report. It talks ONLY to `data/applications/jobos_dev.db`
(created automatically on first run) -- never the production
`data/applications/jobos.db`.

**1. Start the backend** (one-time setup already done in this
checkout; for a fresh clone run `python3 -m venv .venv && .venv/bin/pip
install -r requirements.txt` first):

```
.venv/bin/uvicorn api.main:app --reload --port 8420
```

**2. Frontend**: served by the same process (no separate build/start
step) -- there is no frontend framework in this repo, by design (plain
HTML/CSS/JS, see `web/`).

**3. Open the GUI**: http://127.0.0.1:8420/

**4. Create a candidate**: on the home page, enter a name (+ optional
email/phone) and click "Create candidate."

**5. Upload a resume**: on the same page, choose a `.pdf` file and
click "Upload & extract profile" -- or click "Continue without a
resume" to skip straight to manual entry.

**6. Create a search**: go to `/searches/new`, fill in job titles,
locations, experience/salary range, skills, work model, employment
type, minimum score, freshness, and pick from the sources currently
shown as available (only real, ENABLED sources are ever listed).

**7. Run a search**: from `/dashboard` or a search's own `/searches/{id}`
page, click "Run now." The call returns immediately
(`{run_id, status:"QUEUED"}`); the page polls `/api/runs/{run_id}`
until it reaches a terminal status.

**8. View results**: `/searches/{id}/results` -- filterable table with
an `[Open Job]` link to the job's own external URL. No apply button
exists anywhere.

**9. Download Excel**: "Download Excel report" on the dashboard or a
search's detail page -- the existing 9-sheet workbook
(`scripts/generate_run_report.py`), unchanged.

**10. Sources currently ENABLED**: 4 direct, live-validated sources --
`NAUKRI`, `HIRIST`, `IIMJOBS`, `APNA` (plus `MOCK`, a dev/test
fixture) -- always on. 7 more (`LINKEDIN`, `INDEED`, `FOUNDIT`,
`INSTAHYRE`, `CUTSHORT`, `WELLFOUND`, `SHINE`) turn on automatically
the moment at least one search-provider API key
(`YOU_API_KEY`/`TAVILY_API_KEY`/`EXA_API_KEY`/`BRAVE_API_KEY`/
`SERPER_API_KEY`) is configured in `.env` -- discovered through that
provider's search API, never a direct crawler for these 7. Check
`GET /api/sources` or the dashboard's "Sources" panel for the live,
authoritative list; see CLAUDE.md's "Source Adapter Principle" for the
full current-state detail.

**11. `CAREER_PAGE`, `GREENHOUSE`/`LEVER`/`ASHBY`, `TIMESJOBS`, and the
plain-registry-key `LINKEDIN`/`INDEED`/etc. skeletons** remain
`NOT_ENABLED` (registered, no live behavior) -- reserved for a
hypothetical future authorized *direct* crawler for boards already
covered above via the search-provider path. `WEB_SEARCH` (see #14 of
CLAUDE.md's own list) needs a backend wired in first.

**12. Adding a new authorized source**: implement a
`source_adapter.JobSourceAdapter` subclass (`status = NOT_ENABLED`
until validated), register it in `scripts/source_registry.ADAPTERS`,
add its entry to `scripts/source_capabilities.py`, write offline tests
against fixtures, then run this project's phased live-validation
process (offline → one live query → controlled multi-query) before
ever setting `status = ENABLED`. No GUI/API/scoring/report code needs
to change -- `/api/sources` and the search form both read the live
registry. For a Greenhouse/Lever/Ashby company board specifically, just
add an entry to `config/career_pages.json` (empty by default -- no
company is hardcoded) and then run the same validation process.

**13. How Web Search discovery works**: `.claude/skills/job-discovery-engine/`
is the orchestration Skill; `scripts/web_search_discovery_adapter.py`
is the integration boundary. **This backend process (a plain
FastAPI/uvicorn worker) cannot call Claude Code's own WebSearch tool
directly**, and no search-API credential is configured in this
environment -- so `WEB_SEARCH` is `NOT_ENABLED` today. The provider
interface, quality-control validation
(`.claude/skills/job-discovery-engine/scripts/validate_discovered_job.py`),
and bounded query-string generation all exist and are tested; wiring in
a real backend later is a one-line configuration call
(`web_search_discovery_adapter.set_web_search_backend(...)`), not a
redesign.

**14. Security limitations (stated plainly, not hidden)**: this is a
single-user local development tool. "Candidate identity" is just
remembered in the browser's `localStorage` (see `web/app.js`) --
**not** a real login/session system: any caller can still *claim* to be
any candidate_id, there is no proof of identity. What **is** enforced
server-side: every route keyed by a bare search_id/run_id now requires
an explicit `?candidate_id=` and verifies the looked-up search/run
actually belongs to that candidate_id (`search_store.
get_saved_search_for_candidate()` / `get_run_status_for_candidate()`)
-- candidate A can no longer read, run, archive, or download the report
for candidate B's search merely by guessing or reusing a search_id/
run_id string (see `scripts/test_candidate_ownership_isolation.py`).
**A real internet-facing deployment would still need actual
authentication** (verifying *who* is making the request) layered on
top of this -- this ownership check only verifies the record belongs
to whichever candidate_id was supplied, not that the supplied
candidate_id is really the caller.

**15. How matching works, and why it's explainable, not a black box**:
`scripts/score_job.py` computes a 100-point score across 10 named
dimensions (role, SRE/DevOps practices, cloud, Kubernetes, IaC, CI/CD,
observability, experience, location, domain fit). 6 of the 10
dimensions (worth 70 of the 100 points) compare the job's text against
**your own** profile -- your `target_roles` for the role dimension, your
own listed cloud/Kubernetes/IaC/CI-CD/observability skills for the
rest -- so the *same* job genuinely scores differently for two
different candidates (see `scripts/test_multi_user_matching.py`).
`scripts/score_explanation.py` turns the raw score into a `strong
matches` / `gaps` breakdown, visible on every result's detail view.
Two dimensions ("SRE/DevOps responsibilities" and "Overall/domain fit"
-- 20 of the 100 points) are still generic job-quality signals, not yet
tied to a specific profile field.

**16. Shortlist, approve, and track an application (no auto-apply, ever)**:
`config/application_schema.json`'s existing status lifecycle
(`FOUND` → `SHORTLISTED` → `READY_FOR_APPROVAL` → `APPROVED` →
`APPLICATION_STARTED` → `APPLIED` → interview stages → terminal
states) now has a real write path: `PATCH
/api/candidates/{id}/jobs/{job_id}/status` with `{"status":
"SHORTLISTED"}` (or `APPROVED`, etc.), scoped to your own candidate_id
like every other route. The one hard rule this endpoint enforces:
`APPLICATION_STARTED`/`APPLIED` is unreachable unless the job is
already `APPROVED` -- there is no way to skip your own approval step
through this endpoint or any other code path in this project. It never
submits anything; it only records a status *you* explicitly set,
exactly like clicking `[Open Job]` and applying yourself, then telling
the system you did.

**17. Resume variant selection/tailoring: not built yet.**
`config/profile.json` defines 5 variant labels (SRE-A/AWS-A/AZURE-A/
DEVOPS-A/PLATFORM-A) and `jobs.resume_variant` exists as a column, but
only one resume file exists on disk and nothing currently picks a
variant or generates a tailored version for a specific job. Every
result honestly shows an empty `resume_variant` field rather than
guessing.

**18. No-auto-application policy**: the system ends at
SEARCH → MATCH → SCORE → SHOW → `[Open Job]` → human decides (and, per
#16, optionally shortlists/approves/records their own application).
There is no code path anywhere in `api/` or `web/` that submits an
application, clicks Apply, or uploads a resume to an employer --
verified by a static grep check in
`scripts/test_phase9_api.py`/`test_phase10_discovery_engine.py` and
re-affirmed by manual code audit each phase.

Run the test suites (fully offline, isolated temp DBs, never touch
production):

```
.venv/bin/python3 scripts/test_phase9_api.py
.venv/bin/python3 scripts/test_phase10_discovery_engine.py
.venv/bin/python3 scripts/test_multi_user_matching.py
.venv/bin/python3 scripts/test_candidate_ownership_isolation.py
.venv/bin/python3 scripts/test_application_lifecycle.py
```

Or run every `scripts/test_*.py` file for the complete suite.

## Important

Never commit credentials, OAuth tokens, browser sessions,
application data, or other personal information to Git.

Browser automation must not bypass CAPTCHA, MFA, anti-bot
controls, rate limits, or website restrictions.
