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
  server-side regardless of what the UI lets you click.
- **API**: `PATCH /api/candidates/{candidate_id}/jobs/{job_id}/status`
  with `{"status": "SHORTLISTED"}` (or any other value from the list
  above), scoped to your own `candidate_id` like every other route.
- **History**: `GET /api/candidates/{candidate_id}/jobs/{job_id}/status-history`
  returns the full, timestamped transition history for a job — never
  overwritten, only appended to.

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
