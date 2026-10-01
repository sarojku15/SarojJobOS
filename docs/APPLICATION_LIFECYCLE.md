# Application Lifecycle

JobOS tracks the status of a job through your own process, from
discovery to outcome. **It never submits an application.** Every status
change is a record of something *you* explicitly did or decided.

## The 18 real states (`config/application_schema.json`)

```
FOUND
NOT_QUALIFIED
SCREENING
SHORTLISTED
READY_FOR_APPROVAL
APPROVED
APPLICATION_STARTED
APPLIED
RECRUITER_CONTACTED
SCREENING_CALL
INTERVIEW_1
INTERVIEW_2
FINAL_ROUND
OFFER
REJECTED            (legacy -- see note below)
EMPLOYER_REJECTED
GHOSTED
WITHDRAWN
```

Notes taken directly from the schema's own `_status_notes`:

- **`NOT_QUALIFIED`** is the real score-bucket outcome for an eligible-
  but-below-threshold or hard-rejected job.
- **`REJECTED`** is a **legacy** value from before this distinction
  existed. No code writes it anymore; it's kept only for backward
  compatibility with old rows. Never assume a row with this status was
  rejected by an employer — see `NOT_QUALIFIED` and
  `EMPLOYER_REJECTED` for the real, current meanings.
- **`EMPLOYER_REJECTED`** is reserved for a genuine, human-recorded
  employer rejection. No code currently writes this value automatically.

## The one hard rule

`PATCH /api/candidates/{candidate_id}/jobs/{job_id}/status`
(`scripts/application_lifecycle.py`) enforces exactly one invariant:

> **`APPLICATION_STARTED` and `APPLIED` are unreachable unless the job
> is already `APPROVED`.**

There is no code path anywhere in `api/` or `web/` that skips this,
auto-approves a job, or submits an application on your behalf. Setting
`APPLIED` only ever *records* that you applied — exactly like clicking
"Open Job," applying yourself on the employer's site, and then telling
JobOS you did.

## Typical flow

```
FOUND ──▶ (scored) ──▶ SHORTLISTED ──▶ READY_FOR_APPROVAL ──▶ APPROVED
                                                                  │
                                                                  ▼
                                                     APPLICATION_STARTED
                                                                  │
                                                                  ▼
                                                              APPLIED
                                                                  │
                                        RECRUITER_CONTACTED / SCREENING_CALL
                                                                  │
                                              INTERVIEW_1 → INTERVIEW_2 → FINAL_ROUND
                                                                  │
                                                    OFFER / EMPLOYER_REJECTED / GHOSTED
```

`WITHDRAWN` is available at any point if you decide to stop pursuing a job.

## How to change status

- **UI**: from a result's detail view, the status dropdown (Shortlist →
  Approve → Applied, etc.) — the approval gate above is enforced
  server-side regardless of what the UI lets you click. The dropdown
  now offers `SCREENING`, `EMPLOYER_REJECTED`, and `GHOSTED` (real,
  reachable states this project's own reporting layer already
  understood, but the UI never offered before), and no longer offers
  the legacy `REJECTED` value (see the notes above).
- **API**: `PATCH /api/candidates/{candidate_id}/jobs/{job_id}/status`
  with `{"status": "SHORTLISTED"}` (or any other value from the list
  above), scoped to your own `candidate_id` like every other route.
- **History**: `GET /api/candidates/{candidate_id}/jobs/{job_id}/status-history`
  returns the full, timestamped transition history for a job — never
  overwritten, only appended to.

## Application truth: Mark as Applied

Applying is always something *you* do yourself, outside JobOS, on the
employer's own site. JobOS never submits anything — "Mark as Applied"
only ever *records* that you already did.

There are two ways to record it:

- The generic status dropdown, `{"status": "APPLIED"}` — still fully
  supported, still gated by APPROVED above.
- **The dedicated "Mark as Applied" action**
  (`POST /api/candidates/{candidate_id}/jobs/{job_id}/mark-applied`,
  a button on the results page and on
  [My Applications](#my-applications)) — the same gate, the same
  status-history row, plus three application-time facts a plain
  status change doesn't ask for:
  - **`applied_at`** — set automatically, in UTC, the *first* time a
    job genuinely reaches `APPLIED`. Never overwritten by any later
    status change, and never fabricated — if you never mark a job
    Applied, `applied_at` stays `null` forever, however far its status
    otherwise moves.
  - **The resume actually used** (`applied_resume_id`/
    `applied_resume_variant`) — deliberately separate from the
    existing match-time `resume_id`/`resume_variant` (what JobOS
    scored the job against, which can go stale by the time you
    actually apply). The UI suggests your match-time resume as a
    default but always asks you to confirm — never fabricated if you
    leave it blank.
  - **Notes** (`applied_resume_variant`'s neighbor, `notes`) —
    free-text, yours, attached to this one application. Also editable
    any time via `PATCH .../notes`, independent of Mark as Applied.

Calling Mark as Applied again on an already-`APPLIED` job is not an
error — it lets you correct the resume/notes you recorded, but never
moves `applied_at`.

## My Applications

`GET /api/candidates/{candidate_id}/applications` (the `/applications`
page) is the one place to see every job you've ever shortlisted,
approved, or applied to, **across every saved search** — not scoped to
one search's own results page. Supports `?status=`, `?company=`,
`?source=`, `?follow_up_state=` (`overdue`/`due_today`/`upcoming`/
`none`), and `?date_from=`/`?date_to=`. By default, jobs still sitting
at `FOUND`/`NOT_QUALIFIED` (never acted on) are excluded — ask for them
explicitly with `?status=FOUND` if you want to see everything.

## Follow-ups: one canonical "due," real history

A follow-up date used to be a single value you could set or clear —
clearing it looked identical to "never scheduled one." Now every
follow-up you schedule is its own record
(`POST .../follow-up/schedule`), and it moves through real,
never-deleted states:

- **PENDING** — active, waiting for its due date.
- **COMPLETED** — `POST .../follow-up/complete` ("Follow Up Now"):
  records a real completion timestamp, keeps the row.
- **CANCELLED** — `POST .../follow-up/cancel`, or clearing the date
  via the older `PATCH .../follow-up` endpoint (still fully supported,
  now upgraded to keep history too): "I decided not to," a distinct
  fact from "I did follow up."

`GET .../follow-up-history` returns every instance for a job, oldest
first — never fabricated, never pruned.

Due-ness itself has exactly one definition now, computed server-side
(never independently recomputed by the dashboard or by n8n again):

| Field | Meaning |
|---|---|
| `is_overdue` | `follow_up_date < today` |
| `is_due_today` | `follow_up_date == today` |
| `is_due` | overdue or due today |
| `is_upcoming` | `follow_up_date > today` |

`GET /api/candidates/{candidate_id}/follow-ups` returns every row with
these four fields already computed; pass `?due_only=true` to get just
the due ones (what the n8n Follow-Up Reminder workflow now uses,
instead of computing its own date comparison).

## Notifications (optional, never fabricated)

JobOS never claims a notification was sent unless a real provider
confirms delivery. Today, no provider is wired in at all — the n8n
Follow-Up Reminder workflow only logs due follow-ups to its own
Executions tab by default (see its Setup Notes). The backend
(`scripts/notification_events.py`) already tracks a honest
`NOT_REQUESTED`/`QUEUED`/`SENT`/`FAILED` status per follow-up per day,
deduplicated so re-running the same check twice never double-requests
— ready for a real Slack/Email/Telegram node to be added later without
any JobOS-side redesign. Until then, expect "Notification not
configured," never a silent claim of delivery.

## What JobOS will never do

- Auto-apply, click "Apply," or upload your resume to an employer.
- Bypass CAPTCHA, MFA/OTP, or anti-bot protections to get an
  application through.
- Mark a job `APPLIED` because it *thinks* you probably applied.
- Invent a recruiter name, interview date, or outcome you didn't enter.

Verified by a static grep check for any Apply/submit code path
(`scripts/test_phase9_api.py` / `test_phase10_discovery_engine.py`) and
re-affirmed by manual audit each phase — see
[SECURITY.md](SECURITY.md) for the full safety model.
