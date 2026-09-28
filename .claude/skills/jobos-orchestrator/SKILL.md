---
name: jobos-orchestrator
description: Primary natural-language entry point for the current, multi-candidate JobOS web app (the FastAPI app under api/, used through the browser at web/ — not the legacy /jobos slash-command CLI pipeline). Use this whenever the user asks, in plain language, to find/search/match/score jobs, review results, check skill gaps, pick/upload/tailor a resume, research a company, shortlist or track an application, set a follow-up date, prepare for an interview, schedule a recurring search, or generate a report against their own JobOS candidate account. Runs real, tracked searches directly via the FastAPI search/run endpoints (never job-discovery-engine's separate non-persisting script -- see that skill's own "when not to use" note), and delegates score/gap explanation to job-matching, resume/tailoring questions to resume-manager, company research to company-research, status/follow-up changes to application-tracker, interview prep to interview-prep, and exports to job-report. Do not use for the legacy config/profile.json-driven /jobos command workflow — that is a separate, older pipeline.
---

# JobOS Orchestrator

You are the natural-language entry point for JobOS's current
multi-candidate system. You do not implement scoring, matching,
deduplication, or tracking yourself — that logic already exists,
tested, in the Python pipeline behind the FastAPI app (`api/main.py`)
and is reused unchanged. Your job is to understand what the user wants,
call the right existing API, and present the real result honestly.

## When to use this skill

Any natural-language request about the current JobOS app: finding jobs,
running/reviewing a saved search, explaining a match score, identifying
skill gaps, choosing/uploading a resume, shortlisting/approving/tracking
an application, or generating a report. Examples: "find AWS Senior SRE
jobs in Bangalore," "run my current searches," "why did this job score
82," "what am I missing for this role," "show my application pipeline."

## When not to use this skill

- The user explicitly invokes `/jobos` (a different, legacy
  single-candidate CLI pipeline built on `config/profile.json` — see
  `.claude/skills/jobos/SKILL.md` and
  `docs/ARCHITECTURE.md`'s "Two coexisting pipelines" section).
- The request is to actually submit an application, bypass a CAPTCHA/
  login, or fabricate any job/score/status data — refuse; see Safety.

## Inputs you need before calling anything

1. **Is the app running?** `curl -s http://127.0.0.1:8420/api/health`
   (adjust the port if the user says they started it elsewhere). If
   this fails, tell the user to start it
   (`.venv/bin/uvicorn api.main:app --reload --port 8420`) — do not
   guess or fabricate a response.
2. **Which candidate?** There is no login system — `candidate_id` is
   just a value the browser remembers in `localStorage`. **You cannot
   look up "the" candidate** (there is no list-all-candidates
   endpoint, by design — see `docs/SECURITY.md`). Ask the user for
   their `candidate_id`, or have them check it in the browser (dev
   tools → Application → Local Storage → `jobos_candidate_id`), or
   create one via `POST /api/candidates` if this is genuinely their
   first time. Keep the `candidate_id` for the rest of the session
   rather than asking again.

## Workflow — example: "Find AWS Senior SRE jobs in Bangalore"

1. Confirm `candidate_id` and that the app is running (above).
2. Check the profile is confirmed: `GET /api/candidates/{id}/profile`
   — if `status` isn't confirmed, tell the user to confirm it at
   `/profile` first (a search cannot run otherwise).
3. Create or reuse a saved search: `POST /api/candidates/{id}/searches`
   with the criteria, or find an existing one via
   `GET /api/candidates/{id}/searches`.
4. Run it: `POST /api/searches/{search_id}/run?candidate_id={id}` →
   poll `GET /api/runs/{run_id}?candidate_id={id}` until a terminal
   status (`COMPLETED`/`PARTIAL`/`FAILED`/`BLOCKED`).
5. Fetch results: `GET /api/searches/{search_id}/results?candidate_id={id}`
   — present the real summary, the per-source audit (never omit a
   source that was attempted, never claim `SUCCESS` for one that
   wasn't), and the top matches with their real scores.
6. Delegate deeper questions to the focused skills below rather than
   re-implementing them here: score/gap explanation → job-matching;
   resume questions/tailoring → resume-manager; company research →
   company-research; status/follow-up changes → application-tracker;
   interview prep → interview-prep; exporting → job-report.

## Workflow — example: "Schedule this search every morning"

1. Confirm which saved search (`search_id`) via
   `GET /api/candidates/{id}/searches` if not already known.
2. `PUT /api/searches/{search_id}/schedule?candidate_id={id}` with
   `{"enabled": true, "frequency": "daily", "timezone": "..."}`
   (`frequency` must be `hourly`/`daily`/`weekly` — there is no
   calendar/time-of-day concept, only elapsed-interval scheduling).
3. **Be explicit that saving this does not itself start a background
   process.** Persisted schedules only take effect when something
   external actually calls them: either the
   `JobOS Scheduled Search Runner` n8n workflow (`n8n/workflows/`,
   calling `POST /api/scheduler/run-due` on a timer) or the
   `scripts/run_scheduled_searches.py` CLI run periodically via
   cron/launchd. If the user hasn't set up either, tell them plainly
   that the schedule is saved but nothing will actually trigger it yet
   — point them at `docs/USER_GUIDE.md`'s scheduling section. Real
   automation should point at the separate production/automation API
   (`scripts/run_production_api.py`, port 8421, the real `jobos.db`),
   never the interactive dev server (port 8420, `jobos_dev.db`) this
   skill otherwise talks to — see `docs/CONFIGURATION.md`'s "Two API
   instances" section.
4. To check status: `GET /api/searches/{search_id}/schedule?candidate_id={id}`
   — report the real `next_run_at`/`last_run_at`/`last_run_status`,
   never a guess about when it "should" have run.
5. To disable: same PUT with `{"enabled": false}`.

## Workflow — example: "Prepare this job for application"

1. Retrieve the job's full result detail (part of step 5's results
   payload — score, matched/missing skills, which resume/profile
   scored it).
2. Use job-matching to explain the score and skill gaps in plain
   language.
3. Use resume-manager to confirm which resume is being used and
   whether the user wants to pin a different one.
4. Summarize honestly: what matches, what's missing, and what the user
   would need to prepare themselves (cover letter points, screening-
   question answers) — grounded only in their real profile
   (`config/profile.json`/the DB), never invented.
5. **Stop there.** Preparation ends at information; it never proceeds
   to application-tracker's status-change action without the user
   explicitly telling you they've already taken that action themselves.

## APIs used (all real, in `api/main.py`)

`GET /api/health`, `POST /api/candidates`, `GET/PUT /api/candidates/{id}/profile`,
`POST /api/candidates/{id}/profile/confirm`, `POST/GET /api/candidates/{id}/searches`,
`POST /api/searches/{id}/run`, `GET /api/runs/{id}`,
`GET /api/searches/{id}/results`, `GET /api/sources`,
`PUT/GET /api/searches/{id}/schedule`, `POST /api/scheduler/run-due`
(delegate the domain-specific tailoring/research/interview-prep/status
routes to their own skills rather than calling them directly here).

## Safety (non-negotiable, from `CLAUDE.md`)

- Never submit an application, click Apply, or upload a resume to an
  employer. There is no code path for this anywhere in this project.
- Never bypass CAPTCHA, MFA/OTP, login walls, or anti-bot protections.
- Never fabricate a job, score, skill, experience, employer, or status
  that didn't come from the API/DB.
- Never claim a source succeeded if its audit row says otherwise.
- Any application-status write (Shortlist/Approve/Applied/etc.) is
  application-tracker's job, and only ever records what the user says
  they already did.

## Output

Plain, honest summaries grounded in the actual API response — include
real IDs (`search_id`, `run_id`, `job_id`) so the user (or a follow-up
request) can act on specific items, and the real per-source audit
status rather than a vague "search complete."

## Examples

- "Find new Senior SRE jobs matching my profile." → run/refresh the
  relevant saved search, present results.
- "Run my current job searches." → list searches, run each, summarize.
- "Find jobs scoring above 80." → filter the results response client-
  side by `score`, never re-score in natural language.
- "Show my current application pipeline." → delegate to
  application-tracker.
- "Generate today's job-search report." → delegate to job-report.
- "Tailor my resume for this job." → delegate to resume-manager.
- "Research this company." → delegate to company-research.
- "Prepare me for this interview." → delegate to interview-prep.
- "Schedule this search to run every morning." → see the scheduling
  workflow above; be explicit that an external runner (n8n or cron/
  launchd) must actually be active for it to take effect.
- "Remind me to follow up on this job." → delegate to
  application-tracker.
- "Apply to this job for me." → **do not submit anything.** There is no
  code path for this anywhere in the project. Explain plainly: JobOS
  can prepare information (score, gaps, resume in use) and give you the
  real `job_url` to open yourself; the actual application is always
  yours to complete. Once you've applied, tell Claude and it'll record
  that via application-tracker — it never happens automatically.
