# Getting Started

## Prerequisites

- Python 3.11+ (developed/tested on 3.13)
- Node.js 18+ (only needed for Playwright, used by the Naukri/Hirist/
  IIMJobs/Apna adapters)
- Git
- macOS, Linux, or Windows (Windows is documented below and should
  work from the actual project requirements — it has not been
  independently executed on a Windows machine in this project's own
  testing; the macOS/Linux steps have been)
- No other system packages required

## 1. Clone

**macOS / Linux:**
```bash
git clone <this-repo-url>
cd SarojJobOS
```

**Windows (PowerShell):**
```powershell
git clone <this-repo-url>
cd SarojJobOS
```

## 2. Install dependencies

**macOS / Linux:**
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

npm install
npx playwright install chromium
```

**Windows (PowerShell):**
```powershell
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

npm install
npx playwright install chromium
```

If PowerShell blocks the venv activation script with an execution-
policy error, you don't need to activate it at all for the commands in
this guide — every command below calls the venv's Python directly
(`.venv\Scripts\python.exe` / `.venv/bin/python3`), which works without
activation.

## 3. Search-provider API keys (optional)

Nothing here is required to run JobOS. Four job boards (Naukri,
Hirist, IIMJobs, Apna) work with zero configuration. If you also want
the 7 provider-backed boards (LinkedIn, Indeed, Foundit, Instahyre,
Cutshort, Wellfound, Shine), add your **own** API key from any one of
You.com/Tavily/Exa/Brave/Serper later, from inside the app, at
**Settings → Search Providers** — no `.env` editing, no shell profile,
no OS keychain. Full instructions, including where to get a key for
each provider: [API_PROVIDER_SETUP.md](API_PROVIDER_SETUP.md).

## 4. Start JobOS

**macOS / Linux:**
```bash
.venv/bin/uvicorn api.main:app --reload --port 8420
```

**Windows (PowerShell):**
```powershell
.venv\Scripts\uvicorn api.main:app --reload --port 8420
```

The database is created automatically on first request — nothing to
initialize by hand. Stop it with `Ctrl+C` in the same terminal, on any
OS.

## 5. Open the application

http://127.0.0.1:8420/

## 6. Create/import your profile

Either upload a `.pdf` resume (extracts your profile automatically), or
choose "Enter profile manually" to skip straight to manual entry. Both
paths create your candidate record for you — there's no separate name/
signup form first.

## 7. Configure your resume/profile

On `/profile`: fill in/confirm your name, email, phone, skills,
experience, and target roles/locations, then click "Confirm profile."
A search cannot run until this is confirmed. You can upload additional
resume versions later — none are ever deleted or overwritten (see
[USER_GUIDE.md](USER_GUIDE.md)'s resume section).

## 8. Create a job search

Go to `/searches/new`: job titles, locations, experience/salary range,
skills, work model, minimum score, and which of the currently enabled
sources to use (only real, live sources are ever listed — see
[JOB_SOURCES.md](JOB_SOURCES.md)).

## 9. Run the search

Click "Run now" — the run happens in the background; the page polls
until it reaches a terminal status.

## 10. Review results

`/searches/{id}/results`: a summary, a per-source execution audit
(truthful `SUCCESS`/`ZERO`/`FAILED`/`BLOCKED`/`NOT_CONFIGURED`/
`NOT_ATTEMPTED` status for every source you selected), and the scored,
explainable results table. See [SCORING.md](SCORING.md).

## 11. Track your applications

Shortlist and Approve a job from its result card, then apply on the
employer's own site and come back to click **Mark as Applied** — it
records when you applied and which resume you used. `/applications`
("My Applications") shows every job you've ever acted on, across every
search, with follow-up reminders clearly marked overdue/due today/
upcoming. See [APPLICATION_LIFECYCLE.md](APPLICATION_LIFECYCLE.md) and
[USER_GUIDE.md](USER_GUIDE.md).

## Diagnose your setup

**macOS / Linux:**
```bash
scripts/jobos-doctor
```

**Windows (PowerShell)** — the `scripts/jobos-doctor` wrapper is a
bash script and won't run directly; call the underlying Python checker
it wraps instead:
```powershell
.venv\Scripts\python scripts\jobos_doctor.py
```

Runs real checks (Python, Node, dependencies, database, API, Playwright,
Claude Skills, provider configuration) and reports `READY` or exactly
what's missing — see [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if
anything fails.

## Setting up real automation (n8n/cron)

The steps above give you the interactive dev server (port 8420,
`jobos_dev.db`) — fine for trying JobOS out, but real recurring
automation (n8n, cron) should never point at it. See
[CONFIGURATION.md](CONFIGURATION.md)'s "Two API instances" section for
running the separate production/automation API on port 8421 against
your real `jobos.db`, and migrating its schema once beforehand.

## Using it with Claude

Once the app is running, just talk to Claude naturally — "find AWS
Senior SRE jobs in Bangalore," "explain why these jobs match me," etc.
See [CLAUDE_GUIDE.md](CLAUDE_GUIDE.md) and
[CLAUDE_COOKBOOK.md](CLAUDE_COOKBOOK.md).
