# Saroj Job Search OS

Automated job discovery, matching, application preparation,
browser-assisted application, tracking and follow-up system.

This is Saroj's own personal project, but the application itself is
**fully generic and multi-user**: anyone who runs it locally gets
their own completely independent candidate profile, resumes, saved
searches, and results. A fresh install starts genuinely empty -- no
Saroj-specific data is baked in or required. If Saroj has shared this
repository with you, you can install and use it entirely for your own
job search without seeing (or affecting) anyone else's data.

## Architecture

- n8n — workflow orchestration
- Claude/Anthropic — JD analysis and application intelligence
- Google Sheets — job/application tracker
- Playwright — browser automation
- Python — utilities and scoring
- macOS launchd — scheduling

## Quick Start (fresh clone)

**Requirements**: Python 3.11+ (developed/tested on 3.13), Node.js 18+
(only needed for Playwright, used by the Naukri adapter), macOS/Linux.
No system packages beyond Python/Node are required.

```bash
git clone <this-repo-url>
cd SarojJobOS

# Python dependencies
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Node dependency (Playwright) + its browser binary
npm install
npx playwright install chromium

# Optional: search-provider API keys (see "Configuration" below) --
# the app runs fully without this step, using the 4 direct sources only.
cp config/jobos.env.example .env   # then edit .env if you want the 7
                                    # provider-backed boards too

# Start the app -- the local dev database is created automatically on
# first request; nothing to run by hand.
.venv/bin/uvicorn api.main:app --reload --port 8420
```

Open **http://127.0.0.1:8420/** in a browser. That's it -- no build
step, no separate frontend server (plain HTML/CSS/JS, see `web/`), no
manual database initialization.

**1. Create your candidate**: on the home page, enter a name (+
optional email/phone) and click "Create candidate" -- or upload a
resume directly, which creates the candidate for you automatically.

**2. Upload your resume**: choose a `.pdf` file and click "Upload &
extract profile" -- or click "Continue without a resume" to skip
straight to manual entry. You can upload additional resume versions
later from the Profile page; every version is kept, never overwritten
(see "Multiple resumes" below).

**3. Review and confirm your profile**: `/profile` shows everything
extracted from your resume (identity, skills, experience, job
preferences) -- edit anything, then click "Confirm profile." A search
cannot run until your profile is confirmed.

**4. Create a search**: go to `/searches/new`, fill in job titles,
locations, experience/salary range, skills, work model, employment
type, minimum score, freshness, and pick from the sources currently
shown as available (only real, ENABLED sources are ever listed). You
can also pin the search to a specific resume/profile version instead
of always using whichever is currently active (see "Multiple resumes"
below).

**5. Run the search**: from `/dashboard`, the search's own
`/searches/{id}` page, or the results page itself, click "Run now."
The call returns immediately (`{run_id, status:"QUEUED"}`); the page
polls `/api/runs/{run_id}` until it reaches a terminal status.

**6. View results**: `/searches/{id}/results` -- a summary (jobs
found, hard-eligible, qualified, apply-today, duplicates, new),
a per-source "Source Execution Audit" table (which sources were
attempted, their real status, raw/eligible/displayed counts), and a
filterable results table. Click any row for the full detail: score,
matched/missing skills, freshness, eligibility, which resume/profile
scored it, and a status dropdown (Shortlist → Approve → Applied, with
the approval gate enforced server-side). No auto-apply button exists
anywhere.

**7. Edit an existing search**: from its detail page, click "Edit" --
change any criteria or the pinned resume/profile. Editing never
rewrites a past run's own results; only a future run uses the new
setting.

**8. Download Excel**: "Download Excel report" on the results or
search-detail page -- scoped to exactly the jobs shown for that
specific search/run (never a different, larger candidate-wide count).
See "What the export contains" below for the full column list.

## Multiple resumes / resume versions

The Profile page lists every resume you've ever uploaded (filename,
version label, upload date, status, resume ID) -- uploading a new one
never deletes or overwrites an older one. When creating or editing a
search, the "Resume / Profile version" selector lets you pin that
search to a specific past resume/profile, or leave it on "Current
profile" to always use whichever is active when the search runs. An
old search stays tied to the resume it was created with even after you
upload a newer one; a new search can use the newer one. The same job
can legitimately score differently, and show a different resume, in
two different searches pinned to two different resumes.

## Sources currently ENABLED

4 direct, live-validated sources -- `NAUKRI`, `HIRIST`, `IIMJOBS`,
`APNA` (plus `MOCK`, a dev/test fixture) -- always on, no
configuration needed. `APNA` additionally fetches each job's own
detail page (bounded, rate-limited) for real JD text, posted date, and
precise experience/location, truthfully reported in its own source
audit (`detail_fetch_attempted/succeeded/failed`).

7 more (`LINKEDIN`, `INDEED`, `FOUNDIT`, `INSTAHYRE`, `CUTSHORT`,
`WELLFOUND`, `SHINE`) turn on automatically the moment at least one
search-provider API key
(`YOU_API_KEY`/`TAVILY_API_KEY`/`EXA_API_KEY`/`BRAVE_API_KEY`/
`SERPER_API_KEY`) is configured in `.env` -- discovered through that
provider's search API, never a direct crawler for these 7. Check
`GET /api/sources` or the dashboard's "Sources" panel for the live,
authoritative list; see CLAUDE.md's "Source Adapter Principle" for the
full current-state detail.

Without any provider key configured, only the 4 direct sources run --
the app works fully, just with fewer boards. A source is never
silently skipped: every configured source gets a truthful audit entry
(`SUCCESS`/`ZERO`/`FAILED`/`BLOCKED`/`NOT_CONFIGURED`/`NOT_ATTEMPTED`),
visible on the results page's own "Source Execution Audit" table.

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

**17. Resume variant selection: built, honestly scoped.** Every
result's "Resume/Profile used" reflects the actual resume that scored
it, per candidate, per search (never invented, never a Saroj-specific
SRE-A/DEVOPS-A/... label unless you explicitly labeled your own resume
that way). What is NOT built: automatic tailored-content generation
(rewriting a resume's own text for a specific job) -- selection is
"which of your own uploaded resumes," never invented content.

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

## Test/development searches never appear in your normal view

Every saved search has a `search_type`: `USER` (the default -- every
search you create through the normal UI), `TEST` (this project's own
automated tests / manual live-verification), or `SYSTEM` (reserved,
unused). The normal `/searches` page and Dashboard show and aggregate
`USER` searches only -- a development/verification search never
inflates your job counts or clutters your list. This is a real,
persisted field, never guessed from a search's name.

## Troubleshooting

- **`npx playwright install chromium` fails or Naukri searches
  error out**: Playwright's browser binary is a per-machine cache
  (`~/Library/Caches/ms-playwright` on macOS), not part of this repo --
  re-run the install command above.
- **Port 8420 already in use**: another instance is likely still
  running (`ps aux | grep uvicorn`); stop it, or start this one on a
  different port (`--port 8421`) and open that URL instead.
- **A search-provider board (LinkedIn/Indeed/etc.) never appears as
  an option**: no provider API key is configured in `.env` yet --
  check `GET /api/sources` or Settings → Search Providers for the live
  reason (`NOT_CONFIGURED` vs an actual failure).
- **"Profile needs to be confirmed" when trying to run a search**:
  go to `/profile`, review the extracted/entered fields, and click
  "Confirm profile" -- a DRAFT profile cannot be used to run a search.
- **Nothing in `data/applications/` after first run**: the dev
  database (`jobos_dev.db`) is created automatically on the first API
  request, not at process startup -- open the app in a browser first.

## Important

Never commit credentials, OAuth tokens, browser sessions,
application data, or other personal information to Git.

Browser automation must not bypass CAPTCHA, MFA, anti-bot
controls, rate limits, or website restrictions.
