---
name: application-tracker
description: Read and update a JobOS job's application status (Shortlist, Approve, Applied, interview stages, etc.) and follow-up reminder date for the current candidate, and show the status history or the overall application pipeline. The ONLY skill allowed to write a status change, and only ever records a status the user says they already acted on themselves — never applies, approves, or advances anything on its own initiative.
---

# Application Tracker

Wraps `scripts/application_lifecycle.py` via
`PATCH /api/candidates/{id}/jobs/{job_id}/status`. This is the one
place in JobOS where a write happens outside of search/profile setup —
treat every write as recording a fact the user is telling you, never as
an action you decide to take.

## When to use

- "Show my application pipeline / dashboard."
- "Show applications needing follow-up."
- "Shortlist this job" / "I got approved to apply, mark it approved" /
  "I applied to this, mark it applied."
- "What's the status history for this job?"

## When not to use

- Actually applying to a job, filling a form, or clicking Apply on the
  user's behalf — not implemented, and never will be without explicit
  human action outside this tool (see Safety).
- Explaining why a job scored the way it did (→ job-matching).

## Inputs

`candidate_id`, `job_id`, and (for a write) the target `status` — one
of the 18 real values in `config/application_schema.json` (see
`docs/APPLICATION_LIFECYCLE.md` for the full list and the legacy-value
notes).

## Workflow

1. **Pipeline overview**: `GET /api/candidates/{id}/dashboard` — real,
   candidate-scoped counts and recent activity.
2. **One job's history**:
   `GET /api/candidates/{id}/jobs/{job_id}/status-history` — full,
   timestamped transition list.
3. **Status change** (only when the user states they've already taken
   the corresponding real-world action):
   `PATCH /api/candidates/{id}/jobs/{job_id}/status` with
   `{"status": "<value>"}`. The API itself enforces the one hard rule:
   `APPLICATION_STARTED`/`APPLIED` is unreachable unless the job is
   already `APPROVED` — if this returns an error, tell the user exactly
   why (they need to set `APPROVED` first), don't work around it.
4. Before writing, always ask/confirm what actually happened if it's
   ambiguous ("did you already apply, or do you want to shortlist it
   for now?") rather than assuming the strongest status.
5. **Follow-up date** (candidate+job scoped, `candidate_job_matches` —
   never the shared `jobs` table, so it never leaks across candidates):
   `PATCH /api/candidates/{id}/jobs/{job_id}/follow-up` with
   `{"follow_up_date": "YYYY-MM-DD"}` to set/update, or
   `{"follow_up_date": null}` to clear. Setting this **never** changes
   the job's application status — it's a pure reminder date, unrelated
   to the lifecycle gate above. List every job with a follow-up date
   set via `GET /api/candidates/{id}/follow-ups` (soonest first).

## APIs / Tools

`GET /api/candidates/{id}/dashboard`,
`GET /api/candidates/{id}/jobs/{job_id}/status-history`,
`PATCH /api/candidates/{id}/jobs/{job_id}/status`,
`PATCH /api/candidates/{id}/jobs/{job_id}/follow-up`,
`GET /api/candidates/{id}/follow-ups`.

## Safety

- **Never** set `APPLICATION_STARTED` or `APPLIED` (or claim they were
  set) unless the user has explicitly told you they already did that
  themselves. This tool never applies to anything.
- Never set `APPROVED` on the user's behalf as a convenience — approval
  is the user's own explicit decision point.
- Never invent a recruiter name, interview date, or outcome not stated
  by the user.
- If a write is rejected by the API's approval-gate check, report the
  real error — never retry with a different status to force it through.

## Output

The real dashboard/history data, or confirmation of exactly which
status was set and when (from the API's own response), never an
optimistic guess about what "probably" happened.

## Examples

- "Show my current application pipeline." → dashboard summary.
- "Show applications needing follow-up." →
  `GET /api/candidates/{id}/follow-ups` for jobs with an explicit
  follow-up date set (soonest/most-overdue first); also consider
  jobs stalled at `RECRUITER_CONTACTED`/`SCREENING_CALL`/etc. past a
  reasonable time based on status-history timestamps. Don't claim a
  specific follow-up date exists unless you've actually seen it in a
  real API response.
- "Remind me to follow up on this job next Friday." → convert to an
  absolute `YYYY-MM-DD` date and `PATCH .../follow-up`; confirm the
  real date back to the user.
- "I just got approved to apply for the Akamai job, mark it." →
  `PATCH` with `status: "APPROVED"`.
- "Apply to this job for me." → **refuse the submission itself** — no
  status write happens until the user says they've actually applied.
  Setting `APPROVED` is not the same as applying; never advance to
  `APPLICATION_STARTED`/`APPLIED` speculatively.
