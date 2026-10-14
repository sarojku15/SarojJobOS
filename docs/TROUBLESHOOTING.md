# Troubleshooting

Real, discoverable problems only — nothing invented.

## Installation

**`npx playwright install chromium` fails, or Naukri/Hirist/IIMJobs/Apna
searches error out.** Playwright's browser binary is a per-machine
cache (`~/Library/Caches/ms-playwright` on macOS), not part of this
repo — re-run the install command.

**`pip install -r requirements.txt` fails.** Check your Python version
(`python3 --version`) — this project is developed/tested on 3.13,
requires 3.11+. Use a fresh virtualenv (`python3 -m venv .venv`) rather
than a system Python.

**`npm install` fails or is slow.** Only `playwright` (`^1.63.0`) is a
real dependency — check `package.json`. Node 18+ is required.

## Startup

**Port 8420 already in use.** Another instance is likely still running
(`ps aux | grep uvicorn`) — stop it, or start this one on a different
port (`--port 8421`) and open that URL instead.

**Nothing in `data/applications/` after first run.** The dev database
(`jobos_dev.db`) is created automatically on the first API request, not
at process startup — open the app in a browser first.

## API keys / search providers

**"JobOS never asked me for an API key — is it using someone else's?"**
No. JobOS ships with no API key of any kind, has no default or
fallback key, and never uses another installation's credentials — a
fresh install genuinely has zero providers configured (verified by
`scripts/test_byok_credential_isolation.py`). It never asks for a key
because **no key is required**: the four direct boards (Naukri,
Hirist, IIMJobs, Apna) are searched directly and work with nothing
configured, which is why your first search can return real results
with an empty Settings page. The other seven boards stay
`SEARCH_PROVIDER_NOT_CONFIGURED` and are skipped until *you* add your
own key under **Settings → Search Providers**. Confirm what's actually
in use at any time with `GET /api/sources` (shows each board's real
status) or `GET /api/settings/search-providers` (shows
`configured: true/false` per provider — never a key value).

**Settings page shows a provider as configured, but I never added a
key there.** Check whether a provider variable is exported in your
shell environment (e.g. `TAVILY_API_KEY`) — an exported environment
variable takes precedence over the UI-saved value and is reported as
configured. See [SECURITY.md](SECURITY.md)'s
"Environment-variable precedence".

**Two people share one installation and see each other's provider
quota.** Expected: provider keys are scoped per *installation*, not
per candidate. Run separate installations for independent users — see
[SECURITY.md](SECURITY.md)'s "Credential ownership (BYOK) and its
scope".

**A provider test says `AUTH_FAILED`.** The key is wrong, revoked, or
lacks the required scope on the provider's side. Re-generate it on the
provider's own dashboard and save it again. JobOS reports the
provider's own response message and never echoes your key.

## Using the app

**"Profile needs to be confirmed" when trying to run a search.** Go to
`/profile`, review the extracted/entered fields, and click "Confirm
profile" — a `DRAFT` profile cannot be used to run a search.

**A search-provider board (LinkedIn/Indeed/etc.) never appears as an
option.** No provider API key is configured in `.env` yet — check
`GET /api/sources` or Settings → Search Providers for the live reason
(`NOT_CONFIGURED` vs an actual failure). See
[JOB_SOURCES.md](JOB_SOURCES.md).

**A search finishes but a source shows `FAILED` or `BLOCKED`.** This is
truthful reporting, not a bug to "fix" by retrying harder — the source
either errored or actively blocked the request (e.g. a CAPTCHA
challenge). JobOS will not attempt to bypass it. Try again later, or
proceed with the sources that did succeed.

**Export/Excel job count looks different from the results page.** It
shouldn't — both read from the same canonical, run-scoped functions
(`api/results_store.py`'s `_job_ids_for_search()`). If you genuinely see
a mismatch, that's a regression worth reporting with the specific
`search_id`/`run_id`.

**A job I shortlisted doesn't show up on `/applications`.** By design
— My Applications hides jobs still at `FOUND`/`NOT_QUALIFIED` (never
acted on yet) by default. Use `?status=FOUND` (or the status filter on
the page) to see everything, including untouched matches.

**"Mark as Applied" is disabled/rejected (400).** The job must already
be `APPROVED` — this is the same non-negotiable gate the status
dropdown enforces, see
[APPLICATION_LIFECYCLE.md](APPLICATION_LIFECYCLE.md). Approve it first.

**My follow-up dashboard widget and the n8n workflow used to disagree
on "overdue."** Fixed — both now read the same server-computed
`is_overdue`/`is_due_today` fields from
`GET /api/candidates/{id}/follow-ups`; neither recomputes it locally
anymore. If you still see a mismatch, check the n8n workflow was
re-imported after this update (the URL now includes `?due_only=true`).

**Follow-up reminders only show up in n8n's own Executions log, never
as a real notification.** Expected — no notification provider is wired
in by default (see `scripts/notification_events.py`'s own honest
`NOT_REQUESTED` status, surfaced as "Notification not configured").
Add a Slack/Email/Telegram node after "Log Due Follow-Ups" in the n8n
workflow yourself to get real delivery.

## Resume tailoring / company research / interview prep

**Tailoring a resume returns 404 "not eligible."** This candidate
doesn't pass the same experience/location eligibility gate scoring
uses for that job — not a bug. Check the job's own eligibility reason
in the results view.

**Company research always comes back `NOT_ATTEMPTED`.** No
search-provider API key is configured — see
[CONFIGURATION.md](CONFIGURATION.md)'s search-provider section
(`YOU_API_KEY`/`TAVILY_API_KEY`/etc., or Settings → Search Providers).
This is honest reporting, not a broken feature.

**Interview prep has no "company-specific" questions.** That category
only appears when a real, linked company-research record with actual
content exists — link one (or run research first) if you want it.

**A tailored resume's `factual_safety_status` is `REJECTED`.** This
should be structurally rare (the pipeline only ever reorders/extracts
existing text) — if you see it, it means the automated check found
generated text that doesn't trace back to the original profile; treat
it as a real bug worth reporting with the `tailored_resume_id`.

## Custom Scoring Profiles

**`GET /api/candidates/{id}/scoring-profiles` (or any scoring-profile
page/route) returns `500: sqlite3.OperationalError: no such table:
scoring_profiles`.** The production database hasn't been migrated
since this feature was added — this is expected on an existing
installation and is not a code bug. `scripts/run_production_api.py`
never runs migrations itself by design (see
[CONFIGURATION.md](CONFIGURATION.md)'s "Two API instances" section).
Fix: `.venv/bin/python3 scripts/migrate_production_schema.py --confirm`
once (safe, backed up, verified, and a no-op if already current). The
dev database (`jobos_dev.db`, served on port 8420) gets this
automatically on next startup via `scripts/init_dev_db.py` — only the
production DB (port 8421) needs this explicit step.

**A search run shows `status: FAILED` with "no longer exists" in the
error message.** The scoring profile this search had selected was
deleted (or belongs to a different candidate). This is deliberate —
JobOS never silently falls back to default scoring under a deleted
profile's name. Select a different profile, or clear the selection, on
the search's edit page, then re-run it.

**My search's results don't seem to use my profile's weights.**
Results only reflect a profile once that search has actually been
*run* with it selected — selecting a profile never retroactively
rescales a previous run's already-persisted scores (see
[SCORING.md](SCORING.md#when-scores-are-recomputed)). Run the search
again after selecting/changing the profile.

**I changed a saved search's own "Minimum match score" and nothing
changed.** That field only has an effect when no custom scoring
profile is selected on that search — once a profile is selected, only
the profile's own shortlist threshold determines qualification. See
[SCORING.md](SCORING.md#the-two-thresholds-and-precedence).

## Scheduling

**A schedule's `next_run_at` passed but nothing ran.** Scheduling
persists state only — nothing fires unless an external trigger is
actually running (see [CONFIGURATION.md](CONFIGURATION.md)'s
"Scheduling & automation"). Check: is the n8n workflow activated and
importing correctly, or is `scripts/run_scheduled_searches.py` actually
being invoked by cron/launchd? Calling
`POST /api/scheduler/run-due` manually (or via `curl`) will process any
currently-due schedule immediately, regardless of which trigger you
normally use — good for testing.

**A schedule shows `SKIPPED_ALREADY_RUNNING`.** Working as intended —
the previous run for that search hasn't reached a terminal state yet
(same guard "Run Now" uses). It will run on its next due tick once the
current one finishes.

**Testing a schedule without waiting for the real interval.** Set the
search's schedule via the UI, then call
`POST /api/scheduler/run-due` directly — it processes whatever is
currently due. To force an immediate due state during development, a
schedule's `next_run_at` can be moved into the past directly in the
dev DB (`jobos_dev.db`, never the production DB).

## Database

**Dev database schema looks out of date / a column is missing.** Delete
`data/applications/jobos_dev.db` and restart the app — it's regenerated
automatically via `scripts/init_dev_db.py`'s migration chain. Never do
this to the production DB (`jobos.db`).

## Claude Code / Skills

**Claude doesn't seem to know about a JobOS skill.** Skills are
discovered from `.claude/skills/*/SKILL.md` in this repository — make
sure you're running Claude Code with this repo as (or inside) the
working directory. See [CLAUDE_GUIDE.md](CLAUDE_GUIDE.md) for the full
discovery mechanism.

**`/jobos` command behaves unexpectedly.** That command drives the
*legacy*, single-candidate CLI pipeline (`config/profile.json`,
`data/inbox/job_input.txt`) — a separate, older code path from the
multi-candidate web app/API most users interact with. See
[ARCHITECTURE.md](ARCHITECTURE.md) for how the two relate.

## n8n workflows

**A workflow fails to import, or a node shows a version-mismatch
warning.** The committed JSON specifies node `typeVersion`s current at
the time it was written; a different n8n build may prompt to "update
node version" on import — accept the prompt, the workflow's logic
(HTTP calls to JobOS's own API) doesn't depend on the exact node
version.

**The follow-up reminder workflow returns nothing.** Its "JobOS Config"
node's `candidate_id` is blank by default (never a real id is committed
to this repo) — fill in your own after importing.

## Still stuck?

Run `scripts/jobos-doctor` for a real, automated check of your Python/
Node/dependency/database/Playwright/Claude-Skills setup — see
[GETTING_STARTED.md](GETTING_STARTED.md).
