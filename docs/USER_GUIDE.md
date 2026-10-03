# User Guide

Organized around what you're actually trying to do. See
[GETTING_STARTED.md](GETTING_STARTED.md) first if you haven't installed
JobOS yet.

## "I want to configure a search-provider API key."

Go to **Settings → Search Providers** (`/settings/search-providers`).
JobOS needs **no key at all** to work — four job boards (Naukri,
Hirist, IIMJobs, Apna) are searched directly with nothing configured.
Adding your own key for any one of You.com/Tavily/Exa/Brave/Serper
additionally unlocks seven more boards (LinkedIn, Indeed, Foundit,
Instahyre, Cutshort, Wellfound, Shine), discovered through that
provider's own search API. For each provider you can **add**, **test**
(makes one real call to confirm it works), **enable**, **disable**,
**replace**, or **remove** a key — a saved key is always shown back to
you masked (e.g. `••••••••1234`), never in full, and no plain-text key
is ever returned by any JobOS API response. Full walkthrough,
including where to create an account and generate a key for each
provider: [API_PROVIDER_SETUP.md](API_PROVIDER_SETUP.md).

## "I want to find jobs."

Create a search at `/searches/new` with your target roles/locations,
pick from the sources shown as available, and click "Run now" (or ask
Claude — see [CLAUDE_COOKBOOK.md](CLAUDE_COOKBOOK.md)). Results appear
at `/searches/{id}/results`.

## "I want to find jobs matching my profile."

That's what every search does by default — 6 of the 10 scoring
dimensions compare the job against **your own** confirmed profile
(target roles, cloud/Kubernetes/IaC/CI-CD/observability skills), not a
generic checklist. See [SCORING.md](SCORING.md).

## "I want AWS jobs." / "I want SRE jobs."

List the role/skill in your search criteria and/or your profile's
target roles and skills — the scoring engine reads directly from what
you entered, nothing is hardcoded to any particular profession.

## "I want to understand a match score."

Click any result for its detail view: the raw score, priority tier
(A/B/C), and the exact "Matched Skills" / "Gaps" breakdown that
produced it. Full explanation of every dimension in
[SCORING.md](SCORING.md). Ask Claude "explain why this job scored X"
for the same data in plain language.

## "I want to identify skill gaps."

The same detail view's "Gaps" list — dimensions where the job wanted
something your profile doesn't show. Never inferred beyond what's
explicitly missing from your own listed skills.

## "I want to choose a resume."

The Profile page lists every resume you've ever uploaded. When
creating/editing a search, the "Resume/Profile version" selector lets
you pin it to a specific past resume instead of always using whichever
is current. See "Multiple resumes" in the main [README](../README.md).

## "I want to tailor my resume for a specific job."

Open a job's workspace page (the "Resume tailoring, company research &
interview prep" button on any result's detail view), pick one of your
uploaded resumes, and click "Tailor Resume." **This is ATS-oriented
reordering and emphasis of your own existing experience and skills —
not AI rewriting.** It reorders your summary's own sentences and skill
list, and extracts your own existing bullet points into a highlights
view, so job-relevant content appears first; nothing is invented, and
an automated factual-safety check runs before anything is saved. Every
tailoring attempt creates a new, numbered version — your original
uploaded resume and every earlier tailored version stay exactly as they
were. Only works for a job you're actually eligible for (same gate as
scoring). Download the result as plain text from the same page.

## "I want to research a company."

From the same job workspace page, enter/confirm the company name and
click "Research Company." This runs a real search through whichever
search-provider you've configured (Settings → Search Providers) and
persists a version-numbered record with an honest status:
`NOT_ATTEMPTED` (no provider key configured yet), `FAILED` (every
provider errored), `PARTIAL` (searched, but nothing confidently
identified), or `SUCCESS` (a real website/description found, each with
its own source URL). A fact is never shown without its source link.

## "I want to prepare for an interview."

From the job workspace page, optionally pick a tailored resume and/or
company research record, then click "Generate Interview Preparation."
This produces real, category-grouped questions (Kubernetes, Cloud,
Terraform, CI/CD, Observability, Incident Management, SLI/SLO,
Behavioral, Leadership, Company-specific, Resume-based) each with a
**Suggested Answer** — a real quote from your own resume where you have
matching experience, or an honest **"Preparation required"** note where
you don't. Nothing here claims experience you don't have. You can
save your own answer/confidence/notes per question, and record the
real interview outcome afterward.

## "I want to track an application."

Use the status dropdown on a result's detail view (or
`PATCH /api/candidates/{id}/jobs/{job_id}/status`): Shortlist → Approve
→ Applied, etc. The one rule: you can't reach Applied without having
set Approved first. Nothing here ever auto-applies — see
[APPLICATION_LIFECYCLE.md](APPLICATION_LIFECYCLE.md).

Once you've actually applied on the employer's own site, come back and
click **Mark as Applied** (on the result's detail view, or from
[My Applications](#i-want-to-see-everything-ive-applied-to-in-one-place))
instead of just picking "Applied" from the dropdown — it also records
*when* you applied and lets you confirm which resume you actually used
(your match-time resume is suggested as a default, but you always
confirm it). Add a note any time from the same place.

## "I want to see everything I've applied to in one place."

`/applications` ("My Applications") lists every job you've ever
shortlisted, approved, or applied to, **across all your saved
searches** — not just one search's results page. Filter by status,
company, source, or follow-up state; summary cards up top show your
totals (applied, follow-ups due, interviews, offers, etc.). Each card
shows the real applied date/resume when set, and lets you follow up,
reschedule, or open the job's full history from there.

## "I want to see pending follow-ups."

`/dashboard` shows your candidate-scoped pipeline, recent activity, and
a "Follow-ups" card listing every job with an active follow-up date,
soonest/most-overdue first, each clearly marked **Overdue**, **Due
today**, or **Upcoming** (one definition, computed by the server —
the dashboard and the n8n reminder workflow can never disagree on this
again). For the full set of actions — **Follow Up Now** (marks it
done, keeps the history), **Reschedule**, or **Cancel** — use
[My Applications](#i-want-to-see-everything-ive-applied-to-in-one-place)
or a job's own workspace page. Nothing here ever sends you a real
notification unless you've separately wired a Slack/Email/Telegram
node into the n8n workflow yourself — until then it only logs to n8n's
own Executions tab.

## "I want to use Claude."

Just ask, in plain language, once the app is running — "find AWS
Senior SRE jobs in Bangalore," "show my pipeline," etc. See
[CLAUDE_GUIDE.md](CLAUDE_GUIDE.md).

## "I want to automate recurring searches."

Open a search's detail page and use the "Scheduling" card: enable it,
pick hourly/daily/weekly, set your timezone, save. This persists the
schedule and computes a real `next_run_at` — **but saving it does not
by itself start anything running in the background.** Something
external has to actually call the trigger periodically:

- **Option A (n8n)**: import `n8n/workflows/jobos_scheduled_search_runner.json`
  into your n8n instance (`docker compose up -d` starts the container
  already configured in this repo), set its `jobos_base_url`, activate
  it. It calls `POST /api/scheduler/run-due` on a timer — JobOS itself
  decides what's actually due and triggers it.
- **Option B (cron/launchd)**: run
  `.venv/bin/python3 scripts/run_scheduled_searches.py --once` on a
  timer instead (`--once` is required). Same underlying logic, no n8n
  required.

Pick **one**, not both — running both against the same JobOS instance
is just redundant polling, never a correctness problem (the same
"only one run at a time" guard applies either way), but it's wasted
effort. See [ARCHITECTURE.md](ARCHITECTURE.md)'s "Scheduling" section
for the full operating model, and
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) if a schedule never seems to
fire.
