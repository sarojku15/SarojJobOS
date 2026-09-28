# Getting Started

## Prerequisites

- Python 3.11+ (developed/tested on 3.13)
- Node.js 18+ (only needed for Playwright, used by the Naukri/Hirist/
  IIMJobs/Apna adapters)
- macOS or Linux
- No other system packages required

## 1. Clone

```bash
git clone <this-repo-url>
cd SarojJobOS
```

## 2. Install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

npm install
npx playwright install chromium
```

## 3. Configure environment (optional)

```bash
cp config/jobos.env.example .env
```

Nothing in `.env` is required to run JobOS. Edit it only if you want
the 7 additional provider-backed sources (LinkedIn, Indeed, Foundit,
Instahyre, Cutshort, Wellfound, Shine) — see
[CONFIGURATION.md](CONFIGURATION.md).

## 4. Start JobOS

```bash
.venv/bin/uvicorn api.main:app --reload --port 8420
```

The database is created automatically on first request — nothing to
initialize by hand.

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

## Diagnose your setup

```bash
scripts/jobos-doctor
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
