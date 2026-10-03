# JobOS

**A personal Job Search Operating System** that helps you discover,
understand, match, score, shortlist, prepare for, and track job
opportunities — while keeping the final application decision under
your control. Matching/scoring and resume tailoring are
**deterministic and rule-based**, never an LLM rewriting or inventing
content — see [SCORING.md](docs/SCORING.md) and
[USER_GUIDE.md](docs/USER_GUIDE.md). An optional Claude Code integration
lets you *talk to* this same deterministic system in plain language
(see [CLAUDE_GUIDE.md](docs/CLAUDE_GUIDE.md)) — it is a conversational
interface on top, not a different/AI scoring engine.

This is Saroj's own personal project, but the application itself is
**fully generic and multi-user**: anyone who runs it locally gets their
own completely independent candidate profile, resumes, saved searches,
and results. A fresh install starts genuinely empty.

## Navigation

[What is JobOS](#what-is-jobos) ·
[Problem it solves](#the-problem-it-solves) ·
[How it works](#how-jobos-works) ·
[Features](#feature-overview) ·
[Human-in-the-loop](#human-in-the-loop-model) ·
[Quick Start](#quick-start) ·
[JobOS in 5 minutes](#jobos-in-5-minutes) ·
[Claude Code](#claude-code-integration) ·
[Claude Skills](#claude-skills) ·
[Architecture](#architecture) ·
[Documentation](#documentation) ·
[Troubleshooting](#troubleshooting) ·
[Security](#security-and-privacy) ·
[Development](#development-and-contribution)

## What is JobOS?

A local, single-user-per-install web app + API that runs your own job
search end to end: it discovers postings across multiple job boards,
normalizes and deduplicates them, checks them against your real
eligibility, scores them explainably against your own profile, and
tracks your application pipeline — without ever submitting an
application for you.

## The problem it solves

The traditional job search is repetitive manual labor:

- Search multiple job boards separately
- Read large numbers of job descriptions
- Compare each one against your own profile by hand
- Figure out what's actually missing from your skillset for a role
- Pick the right resume version for each application
- Track what you applied to, and when
- Remember recruiter/interview activity
- Follow up
- Build your own status reports
- Repeat this every day

JobOS centralizes discovery, comparison, scoring, and tracking into one
system, so you spend your time on the decisions only you can make —
not the repetitive research.

**Why use it**: one place for every source instead of ten open tabs,
an explainable score instead of a gut feeling, an honest skill-gap list
instead of guessing what's missing, and a tracked pipeline instead of
a mental list of "did I already apply to this one?"

## How JobOS works

```
User
  |
JobOS
  |
Discover  (query every enabled source)
  |
Normalize  (one common job shape, regardless of source)
  |
Deduplicate  (in-batch + across sources)
  |
Eligibility  (experience + location -- hard gate)
  |
Match / Score  (100-point engine, against YOUR profile)
  |
Explain  (strong matches / gaps, never a black box)
  |
Shortlist
  |
User Review  <-- you decide, always
  |
Application  <-- you apply yourself, always
  |
Tracking
```

Everything above the "User Review" line is automated. Everything from
there down is **you** — JobOS records what you tell it, it never acts
on your behalf. See [Human-in-the-loop model](#human-in-the-loop-model).

## Feature overview

| Capability | Status |
|---|---|
| Job discovery (11 sources: 4 direct + 7 provider-backed) | Built, live-validated |
| Search management (create/edit/run/re-run) | Built |
| Job normalization & deduplication | Built |
| Eligibility filtering (experience + location) | Built |
| Explainable 100-point scoring | Built |
| Skill-gap identification | Built |
| Candidate profile (resume-extracted or manual) | Built |
| Resume management (multiple versions, never overwritten) | Built |
| Resume/profile *selection* per search | Built |
| Resume tailoring (ATS-oriented reorder/emphasis of your own content) | Built — deterministic, never AI rewriting, never invented content |
| Application status tracking (18-state lifecycle) | Built |
| Follow-up date tracking (candidate+job scoped reminder) | Built |
| Search run history | Built |
| Excel export (scoped to what you're viewing) | Built |
| Browser automation (Naukri/Hirist/IIMJobs/Apna) | Built, via Playwright |
| n8n | Built — two importable workflows (`n8n/workflows/`): scheduled search runner + follow-up reminder |
| Scheduling (recurring automated runs) | Built — persisted per-search schedule; needs an external trigger (n8n workflow or `run_scheduled_searches.py` via cron/launchd) actually running to fire |
| Claude Code integration (natural language) | Built (`.claude/skills/`) |
| Company research | Built — persisted, source-backed, via the existing multi-provider search layer (honest `NOT_ATTEMPTED` if no provider key is configured) |
| Interview preparation | Built — deterministic, evidence-based questions + suggested answers |

## Human-in-the-loop model

Everything in the [Feature overview](#feature-overview) above is what
JobOS **can** do. This is what it will **never** do:

- Automatically submit an application without your explicit approval
- Bypass CAPTCHA or MFA/OTP
- Circumvent anti-bot protections
- Mark an application as submitted unless you told it you did that
- Upload a resume to an employer on your behalf
- Fabricate a skill, employer, score, or status

The application-tracking status pipeline only ever *records* what you
tell it you already did — it never acts on your behalf. Full detail:
[docs/SECURITY.md](docs/SECURITY.md),
[docs/APPLICATION_LIFECYCLE.md](docs/APPLICATION_LIFECYCLE.md).

## Quick Start

**Requirements**: Python 3.11+ (developed/tested on 3.13), Node.js 18+
(Playwright only), macOS/Linux.

```bash
git clone <this-repo-url>
cd SarojJobOS
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
npx playwright install chromium
cp config/jobos.env.example .env   # optional -- see docs/CONFIGURATION.md
.venv/bin/uvicorn api.main:app --reload --port 8420
```

Open **http://127.0.0.1:8420/**. No build step, no separate frontend
server, no manual database init. Full walkthrough:
[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).

Check your setup any time: `scripts/jobos-doctor`.

## JobOS in 5 minutes

1. **Configure your profile** — upload a resume or enter manually.
2. **Add a resume** — every version is kept; none are ever overwritten.
3. **Create a search** — roles, locations, sources, minimum score.
4. **Run it** — click "Run now"; polls until complete.
5. **Review results** — per-source audit + scored, explainable matches.
6. **Understand the score** — click a result for matched skills/gaps.
7. **Shortlist jobs** — status dropdown, no auto-apply ever.
8. **Track your application** — Approve → (you apply) → Applied.

Full detail: [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

## Claude Code integration

Once the app is running, just talk to Claude naturally:

> "Find new Senior SRE jobs matching my profile."
> "Explain why these jobs match me."
> "Show me skill gaps for this job."
> "Show my current application pipeline."
> "Generate today's job-search report."

Claude discovers JobOS's capabilities from `.claude/skills/*/SKILL.md`
in this repository automatically — no setup needed beyond running
Claude Code inside this project. Full guide:
[docs/CLAUDE_GUIDE.md](docs/CLAUDE_GUIDE.md),
[docs/CLAUDE_COOKBOOK.md](docs/CLAUDE_COOKBOOK.md).

## Claude Skills

| Skill | Purpose |
|---|---|
| `jobos-orchestrator` | Natural-language entry point; routes to the skills below; also owns search-scheduling requests |
| `job-discovery-engine` | Standalone, ad-hoc, non-persisting discovery script — not the path a real tracked search uses (that's `jobos-orchestrator`'s own API calls) |
| `job-matching` | Score/priority/skill-gap explanation |
| `resume-manager` | Resume listing/upload/pinning, and ATS-oriented resume tailoring for a specific job (deterministic reorder/emphasis, never AI rewriting) |
| `company-research` | Real, persisted, source-backed company research (reuses the existing multi-provider search layer) |
| `interview-prep` | Deterministic, evidence-based interview question generation + suggested answers, candidate answers, and outcome tracking |
| `application-tracker` | Status pipeline reads + the one gated write path, plus follow-up date set/clear |
| `job-report` | Excel export |
| `jobos` (`/jobos` command) | Legacy single-candidate CLI pipeline — separate, older system |

## Architecture

FastAPI (`api/`) + a plain HTML/CSS/JS frontend (`web/`), SQLite system
of record, Playwright-driven direct adapters + a multi-provider search
layer for 7 more boards, one shared scoring/eligibility/dedup engine.
Full diagram and component list: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

A few subsystems worth knowing about up front, each documented in full:

- **Scoring**: a 100-point engine across 10 dimensions, 70 of which
  compare the job against *your own* profile, plus a mandatory-skill
  hard gate a high score can never override — [docs/SCORING.md](docs/SCORING.md).
- **Job sources**: 4 direct, live-validated sources always on; 7 more
  enabled the moment one search-provider API key is configured. A
  source is never silently skipped — [docs/JOB_SOURCES.md](docs/JOB_SOURCES.md).
- **Application lifecycle**: 18 real states, one hard rule (`APPLIED`
  is unreachable without `APPROVED` first) — [docs/APPLICATION_LIFECYCLE.md](docs/APPLICATION_LIFECYCLE.md).
- **Automation**: recurring saved-search execution is real and
  persisted (`PUT /api/searches/{id}/schedule`), but needs exactly ONE
  external trigger actually running to fire — either the
  `JobOS Scheduled Search Runner` n8n workflow (`n8n/workflows/`) or
  `scripts/run_scheduled_searches.py` via cron/launchd. Neither is
  active by default; pick one, never both at once. Real automation
  should point at the separate production/automation API
  (`scripts/run_production_api.py`, port 8421, the real
  `jobos.db`) — never the interactive dev server (port 8420,
  `jobos_dev.db`) — see [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
  A `launchd` template for the older, legacy single-candidate CLI pipeline also
  still exists, separately, and is likewise not installed by default.

## Documentation

See [docs/README.md](docs/README.md) for the full, categorized
documentation map. Highlights:

| Doc | Covers |
|---|---|
| [GETTING_STARTED.md](docs/GETTING_STARTED.md) | Full install walkthrough (macOS/Linux/Windows) |
| [FIRST_SEARCH_WALKTHROUGH.md](docs/FIRST_SEARCH_WALKTHROUGH.md) | One complete, concrete walkthrough with a fictional candidate |
| [QUICK_REFERENCE.md](docs/QUICK_REFERENCE.md) | One-page cheat sheet |
| [USER_GUIDE.md](docs/USER_GUIDE.md) | Goal-oriented "I want to..." guide |
| [API_PROVIDER_SETUP.md](docs/API_PROVIDER_SETUP.md) | Getting and configuring your own search-provider API key |
| [SEARCH.md](docs/SEARCH.md) / [MATCHING.md](docs/MATCHING.md) / [RESUMES.md](docs/RESUMES.md) | Search, scoring, and resume workflows |
| [APPLICATION_TRACKER.md](docs/APPLICATION_TRACKER.md) / [INTERVIEW_PREP.md](docs/INTERVIEW_PREP.md) / [FOLLOWUPS.md](docs/FOLLOWUPS.md) / [COMPANY_RESEARCH.md](docs/COMPANY_RESEARCH.md) | Application lifecycle, interview prep, follow-ups, company research |
| [N8N_SETUP.md](docs/N8N_SETUP.md) / [AUTOMATION.md](docs/AUTOMATION.md) | Optional n8n automation |
| [API_REFERENCE.md](docs/API_REFERENCE.md) | Every real API endpoint |
| [DATA_AND_BACKUP.md](docs/DATA_AND_BACKUP.md) | Where your data lives, backup, moving computers |
| [CLAUDE_GUIDE.md](docs/CLAUDE_GUIDE.md) | How Claude Code operates JobOS |
| [CLAUDE_COOKBOOK.md](docs/CLAUDE_COOKBOOK.md) | Natural-language prompt examples |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, diagrams, both pipelines |
| [SCORING.md](docs/SCORING.md) | The real 100-point model |
| [JOB_SOURCES.md](docs/JOB_SOURCES.md) | Every source's real status |
| [APPLICATION_LIFECYCLE.md](docs/APPLICATION_LIFECYCLE.md) | The 18 states + approval gate |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Every env var and config file |
| [SECURITY.md](docs/SECURITY.md) | Secrets, privacy, safety boundaries |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Real, discoverable problems |
| [GLOSSARY.md](docs/GLOSSARY.md) | Plain-language term definitions |

## Troubleshooting

Playwright install issues, port conflicts, a provider board not
appearing, "profile needs to be confirmed," and other real,
discoverable problems: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).
Or run `scripts/jobos-doctor` for a live diagnostic of your own setup.

## Security and privacy

Secrets, resumes, and the production database are gitignored and never
committed. Candidate data is strictly scoped per `candidate_id`
(verified by automated multi-user isolation tests), though identity
itself is `localStorage`-based, not a real login — see
[docs/SECURITY.md](docs/SECURITY.md) for the full model and its stated
limitations.

## Development and contribution

Run the full backend suite: `for f in scripts/test_*.py; do .venv/bin/python3 "$f"; done`.
Frontend integrity check: `.venv/bin/python3 scripts/test_frontend_integrity.py`.
See `CLAUDE.md` for the project's working conventions (extend, don't
rebuild; smallest change that achieves the goal; run tests before
calling anything done).

Never commit credentials, OAuth tokens, browser sessions, application
data, or other personal information to Git. Browser automation must
never bypass CAPTCHA, MFA, anti-bot controls, rate limits, or website
restrictions.
