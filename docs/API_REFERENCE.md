# API Reference

Every route below exists in `api/main.py` as of this writing — nothing
here is planned or aspirational. There is no separate authentication
layer: every route is scoped by the `candidate_id` you pass in the URL
(see [SECURITY.md](SECURITY.md) for exactly what that does and doesn't
protect against). All request/response bodies are JSON unless noted.

Base URL for local use: `http://127.0.0.1:8420` (dev) or
`http://127.0.0.1:8421` (production/automation — see
[CONFIGURATION.md](CONFIGURATION.md)).

## Health

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | `{"status":"OK","db":"jobos_dev.db","mode":"development","jobos_db_path_configured":false}` — tells you which DB file and mode (`development`/`production`) this process is actually serving. |

## Candidates

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/candidates` | Create a candidate. Body: `{"name": "Alex Doe", "email": "alex@example.com"}` (email/phone optional). Returns a server-generated `candidate_id` (e.g. `cand_1a2b3c4d5e6f`) — you never choose this yourself. |
| GET | `/api/candidates/{candidate_id}` | Candidate's own basic record. 404 if unknown. |
| PATCH | `/api/candidates/{candidate_id}` | Update name/email/phone/status. |

## Profile

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/candidates/{candidate_id}/profile` | Current profile (DRAFT or CONFIRMED), with every skill/employment/certification field. |
| PUT | `/api/candidates/{candidate_id}/profile` | Replace/edit the profile fields directly (manual entry or correcting an extracted draft). |
| POST | `/api/candidates/{candidate_id}/profile/confirm` | Promotes the current DRAFT to CONFIRMED. **A search cannot run until this has happened at least once.** |
| GET | `/api/candidates/{candidate_id}/profile-versions` | Every historical profile version, newest first, each tagged with which resume (if any) produced it. |

## Resumes

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/candidates/{candidate_id}/resume` | Upload a `.pdf` (multipart `file` field). Extracts a new DRAFT profile automatically. Max size enforced; rejects non-PDF content by checking the actual file bytes, not just the filename. |
| GET | `/api/candidates/{candidate_id}/resumes` | Every resume you've ever uploaded (filename, hash, upload/parse timestamps, status) — none is ever deleted or silently replaced by a later upload. |

## Search Providers & Sources

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/settings/search-providers` | List all 5 providers with `configured`/`enabled`/`masked_key` — **never the real key**. |
| POST | `/api/settings/search-providers/{provider}/key` | Save/replace your key for `provider` (`you`/`tavily`/`exa`/`brave`/`serper`). Body: `{"api_key": "<YOUR_PROVIDER_API_KEY>"}`. Response contains only `masked_key`, never the value you sent. |
| DELETE | `/api/settings/search-providers/{provider}/key` | Remove the saved key. |
| POST | `/api/settings/search-providers/{provider}/test` | Makes one real call to that provider with the currently-saved key; reports success/auth-failure/rate-limit honestly. |
| POST | `/api/settings/search-providers/{provider}/enable` / `/disable` | Toggle without deleting the key. |
| POST | `/api/settings/search-providers/order` | Set provider failover priority order. |
| POST | `/api/settings/search-providers/{provider}/reset-usage` | Clears locally-tracked usage counters for that provider. |
| GET | `/api/sources` | Every job source (4 direct + 7 provider-backed + registered-but-not-enabled skeletons) with its real `final_status` (`ENABLED`/`AVAILABLE_VIA_SEARCH_PROVIDER`/`SEARCH_PROVIDER_NOT_CONFIGURED`/`NOT_ENABLED`/etc.) — this is how you check what will actually be searched before running a search. |

## Searches

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/candidates/{candidate_id}/searches` | Create a saved search. Fields (from the actual creation form): role/keyword list, locations, min/max experience years, max job age (days), min/max salary + currency, work model (Remote/Hybrid/Onsite), skills/criteria, employment type, minimum match score, which sources to use, optional resume/profile version pin. |
| GET | `/api/candidates/{candidate_id}/searches` | List your saved searches. |
| GET` / PUT` / PATCH` / DELETE` | `/api/searches/{search_id}` | Read/replace/partially-update/delete one saved search. |
| POST | `/api/searches/{search_id}/run` | Starts a run in the background; returns a `run_id` immediately (does not block). |
| GET | `/api/runs/{run_id}` | Poll this to see run status (queued/running/a terminal state) until it finishes. |
| GET | `/api/searches/{search_id}/results` | Scored results for that search's latest usable run, plus the per-source execution audit (`SUCCESS`/`ZERO`/`FAILED`/`BLOCKED`/`NOT_CONFIGURED`/`NOT_ATTEMPTED` — see [JOB_SOURCES.md](JOB_SOURCES.md)). |
| PUT / GET | `/api/searches/{search_id}/schedule` | Set/read a recurring schedule (hourly/daily/weekly) — see [ARCHITECTURE.md](ARCHITECTURE.md) (Scheduling section) for what actually triggers it. |

## Jobs / Applications

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/candidates/{candidate_id}/jobs/import` | Manually import one job by URL/details (for a source JobOS can't crawl) into your pipeline, scored the same way as a discovered job. |
| PATCH | `/api/candidates/{candidate_id}/jobs/{job_id}/status` | Move a job through the lifecycle (Shortlist → Approve → Applied → ...). Enforces APPROVED-before-APPLIED. |
| POST | `/api/candidates/{candidate_id}/jobs/{job_id}/mark-applied` | The dedicated "I actually applied" action — records `applied_at` and lets you confirm which resume you used. |
| PATCH | `/api/candidates/{candidate_id}/jobs/{job_id}/notes` | Set/clear a free-text note on this application. |
| GET | `/api/candidates/{candidate_id}/jobs/{job_id}/status-history` | Every status transition for this job, in order, who/when. |
| GET | `/api/candidates/{candidate_id}/applications` | "My Applications" — every job you've acted on, across **all** your saved searches, with filters. |

## Follow-ups

| Method | Path | Purpose |
|---|---|---|
| PATCH | `/api/candidates/{candidate_id}/jobs/{job_id}/follow-up` | Set or clear the simple follow-up date field (back-compatible with the dashboard widget). |
| GET | `/api/candidates/{candidate_id}/follow-ups` | List active follow-ups; `?due_only=true` restricts to overdue/due-today. |
| GET | `/api/candidates/{candidate_id}/jobs/{job_id}/follow-up-history` | Every follow-up instance ever scheduled/completed/cancelled for this job — never deleted. |
| POST | `.../follow-up/schedule` | Schedule/reschedule, with a note. |
| POST | `.../follow-up/complete` | Mark the current follow-up done (keeps history). |
| POST | `.../follow-up/cancel` | Cancel without claiming it was completed. |

## Resume Tailoring

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/candidates/{candidate_id}/jobs/{job_id}/tailor-resume` | Deterministic, rule-based reordering/emphasis of your own confirmed profile for this specific job — **never AI rewriting, never invents content.** Creates a new numbered version; nothing is overwritten. |
| GET | `.../tailored-resumes` | List tailored versions for this job. |
| GET | `/api/candidates/{candidate_id}/tailored-resumes/{id}` / `/download` | Read or download one tailored version. |

## Company Research

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/candidates/{candidate_id}/company-research` | Runs a real search (via your configured provider) for the company behind a job. Status is honestly one of `NOT_ATTEMPTED`/`FAILED`/`PARTIAL`/`SUCCESS` — never fabricated. |
| GET | `.../company-research` / `/{id}` | List or read a research record. |

## Interview Preparation

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/candidates/{candidate_id}/interview-prep` | Generate category-grouped questions (Kubernetes/Cloud/Terraform/CI-CD/Observability/Incident Management/SLI-SLO/Behavioral/Leadership/Company-specific/Resume-based) for a job, each with a suggested answer from your real resume or an honest "Preparation required." |
| GET | `.../jobs/{job_id}/interview-prep` / `/interview-prep/{id}` | Read a prep record. |
| PATCH | `.../interview-prep-questions/{question_id}` | Save your own answer/confidence/notes for one question. |
| PATCH | `.../interview-prep/{id}/outcome` | Record the real interview outcome. |

## Reports

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/candidates/{candidate_id}/report` | Candidate-wide Excel export. |
| GET | `/api/searches/{search_id}/report` | Single-search-scoped Excel export. |

## Dashboard & Scheduler

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/candidates/{candidate_id}/dashboard` | Aggregated pipeline view — see [DASHBOARD section of USER_GUIDE.md](USER_GUIDE.md). |
| POST | `/api/scheduler/run-due` | Called by n8n/cron — JobOS itself decides which saved searches are actually due and triggers them. |

## Errors

Every endpoint uses standard HTTP status codes: `404` for an unknown
candidate/resource, `400`/`409` for an invalid state transition (e.g.
trying to mark Applied before Approved), `422` for a malformed request
body (FastAPI's standard validation error shape), `500` only for a
genuine unhandled server error. None of these endpoints ever return a
fabricated "success" when the underlying operation didn't actually
happen.
