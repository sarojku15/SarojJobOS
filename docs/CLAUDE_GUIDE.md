# Claude Code Guide

How Claude Code (or any Claude Skills-aware agent) discovers and uses
JobOS.

## What Claude Skills are, here

A Skill is a `SKILL.md` file with YAML frontmatter (`name`,
`description`) plus instructions. Claude Code reads every
`.claude/skills/*/SKILL.md` in the working directory automatically and
uses each skill's `description` to decide whether to invoke it for a
given user request — no manual registration needed, unless the skill's
frontmatter sets `disable-model-invocation: true` (in which case it
only runs via an explicit slash command, like `/jobos`).

## Where they live

```
.claude/
├── commands/
│   └── jobos.md              # /jobos slash command -> legacy skill
└── skills/
    ├── jobos-orchestrator/    # primary natural-language entry point
    ├── job-discovery-engine/  # standalone, ad-hoc, non-persisting discovery script only
    ├── job-matching/          # score/gap explanation + analysis
    ├── resume-manager/        # resumes + profile-version selection
    ├── application-tracker/   # status pipeline (the only write path)
    ├── job-report/            # Excel export
    ├── company-research/      # optional, best-effort web research
    └── jobos/                 # legacy single-candidate CLI pipeline
```

## How Claude should interact with JobOS

1. **Start with `jobos-orchestrator`** for any natural-language
   request about the current multi-candidate app. It routes to the
   focused skills below rather than you re-implementing their logic.
2. **The app must be running.** JobOS is a local FastAPI server
   (`.venv/bin/uvicorn api.main:app --port 8420`) — check
   `GET /api/health` before assuming it's up.
3. **There is no login.** `candidate_id` identifies who you're acting
   for, and it's just a value — ask the user for theirs (or create one)
   rather than guessing or assuming a single default candidate. There
   is no "list all candidates" endpoint, by design (see
   `docs/SECURITY.md`).
4. **Everything you say must trace to a real API response.** Never
   present a job, score, status, or source result you haven't actually
   retrieved this turn.

## Which skill for which request

| Request shape | Skill |
|---|---|
| "Find X jobs in Y" / "run my searches" | `jobos-orchestrator` — real, tracked searches run via its own direct API calls (`POST /api/candidates/{id}/searches` + `/run`), **not** `job-discovery-engine` (that's a separate, non-persisting script for ad-hoc/untracked exploration only — see its own SKILL.md) |
| "Why did this score Y" / "skill gaps" / "compare jobs" | `job-matching` |
| "What resumes do I have" / "upload my resume" / "pin a resume" | `resume-manager` |
| "Show my pipeline" / "mark this shortlisted/approved/applied" | `application-tracker` |
| "Generate/download a report" | `job-report` |
| "Tell me about this company" | `company-research` (best-effort, not a built pipeline — see its SKILL.md) |
| `/jobos ...` explicitly | `jobos` (legacy CLI pipeline — separate system, see `docs/ARCHITECTURE.md`) |

## What requires human approval

Every write beyond search/profile setup goes through
`application-tracker`, and even there, only ever *records* a status the
user tells you they already achieved themselves. Concretely:

- Creating a candidate, uploading a resume, confirming a profile,
  creating/running a search — fine to do directly on the user's
  request, these are reversible and don't touch any employer.
- Setting `SHORTLISTED`/`READY_FOR_APPROVAL`/`APPROVED` — fine, on the
  user's explicit instruction; these are the user's own internal
  tracking decisions.
- Setting `APPLICATION_STARTED`/`APPLIED` (or anything past it) —
  **only ever a record of something the user says they already did.**
  Never do this speculatively or as a "next logical step."
- Actually submitting an application anywhere — **never**. No code
  path for this exists in this project.

## What Claude must never claim without verification

- That a source "ran" if its audit row says `NOT_ATTEMPTED`,
  `NOT_CONFIGURED`, `FAILED`, or `BLOCKED` — see
  `docs/JOB_SOURCES.md`'s status vocabulary.
- That an application was submitted, unless the user told you so and
  you're only recording it via `application-tracker`.
- A specific score, skill match, or gap you haven't read from a real
  API response this session.
- That `config/profile.json` describes the person you're currently
  talking to — for the current multi-candidate app, always pull the
  live profile via the API for whichever `candidate_id` is in scope;
  `config/profile.json` is specifically the legacy CLI pipeline's
  single candidate (see `docs/ARCHITECTURE.md`).

## Example prompts

See `docs/CLAUDE_COOKBOOK.md` for a full organized set. A few to start:

- "Find new Senior SRE jobs matching my profile."
- "Run my current job searches."
- "Find jobs scoring above 80."
- "Explain why these jobs match me."
- "Show me skill gaps for this job."
- "Show my current application pipeline."
- "Prepare an application checklist for this job."
- "Generate today's job-search report."
