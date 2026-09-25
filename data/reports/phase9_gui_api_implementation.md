# Phase 9 — Generic Job Search Web GUI + API: Implementation Report

## 1. Summary

A working local MVP web app was implemented on top of the existing
SarojJobOS pipeline: **Upload Resume (or skip) → Review/Edit Profile
→ Confirm → Create Saved Search → Run Search → See Results → Download
Excel**. It is generic — no job title, skill, location, salary, or
candidate name is hardcoded anywhere in `api/` or `web/`. It was
verified against four differently-shaped candidate personas (Senior
SRE, Senior Java Developer, Product Manager, Data Scientist) using the
exact same, unbranched code path. Production `data/applications/jobos.db`
is untouched (byte-identical SHA-256 before and after all Phase 9 work,
including the full test suite run). launchd was not touched. No
automatic application submission exists anywhere in the new code.

## 2. Architecture Audit Conclusions (STEP 1)

- **Existing backend entry points reused, unmodified in behavior for
  every pre-existing caller:** `candidate_profile.py` (the generic
  `CandidateProfile`/`JobPreferences` data model, `normalize_`/
  `validate_`/`serialize_`/`promote_to_confirmed`/
  `to_legacy_matching_profile`), `resume_extractor.py`
  (`extract_candidate_profile_draft_result`), `search_submission.py`
  (`submit_search`), `search_worker.py` (`run_once`),
  `generate_run_report.py` (`load_jobs`, `load_candidate_job_matches`,
  `build_report_rows`, `generate`), `source_registry.py`
  (`list_sources`, `get_adapter_status`), `job_ranking.py`
  (`RankingRecord`, used transitively via `build_report_rows`).
- **Existing data model:** `candidates` / `candidate_search_profile`
  (versioned, `is_active` flag, DRAFT/CONFIRMED lifecycle) — already
  fully generic; no schema change was needed to represent "what the
  candidate is."
- **Existing search model:** one CONFIRMED profile → one
  `SearchProfile` (via `search_profile.build_search_profile`) → one
  query plan → one `search_runs`/`search_queue` row, consumed once by
  `search_worker.run_once`. This model assumed exactly one active
  profile == exactly one set of search criteria per candidate.
- **Existing report model:** `generate_run_report.py`'s 9-sheet
  workbook + `ReportRow`/`RankingRecord` — candidate-scoped, already
  handles scoring/eligibility/freshness/dedup end to end.
- **Missing API capability (the one genuine gap):** nothing let one
  candidate have *multiple, independently-run* saved searches with
  different target roles/locations — the existing model hard-read
  `target_roles`/`target_locations` from the profile's own
  `job_preferences`. **Fix:** three new, optional, default-`None`
  override parameters (`target_roles_override`,
  `target_locations_override`, `work_models_override`) were added to
  `search_profile.build_search_profile()`,
  `search_submission.build_search_plan()`, and
  `search_submission.submit_search()`. All 25 pre-existing
  `test_search_submission.py` tests still pass unmodified — this is
  purely additive, zero behavior change for any existing caller.
- **Missing GUI capability:** none existed — this phase built it from
  scratch on top of the API boundary described below.

## 3. New Product Data Model (STEP 2)

- **CandidateProfile** (unchanged, reused as-is) = "who the candidate
  is": identity, skills, experience, education, employment history.
- **SavedSearch** (new, additive tables `saved_searches` +
  `saved_search_runs`, `scripts/migrate_v3_saved_searches.py`) = "what
  the candidate wants searched, as one of possibly several independent
  searches": name, target_roles, target_locations, work_models,
  employment_type, experience/salary bounds, minimum_match_score,
  max_job_age_days, sources. One candidate → many SavedSearches. Each
  run of a SavedSearch produces one `search_runs` row (via the
  existing, unmodified `submit_search()`), linked back through
  `saved_search_runs`.
- No second copy of scoring, eligibility, freshness, dedup, or query
  planning was created.

## 4. API Layer (STEP 6) — `api/`

FastAPI app (`api/main.py`) always talks to
`data/applications/jobos_dev.db` — hardcoded, not client-configurable
by any header/param/env var, so the API can never be pointed at
production. Endpoints:

| Method & Path | Purpose |
|---|---|
| `POST /api/candidates` | create a candidate (name/email/phone only) |
| `GET /api/candidates/{id}` | fetch candidate record |
| `GET /api/candidates/{id}/profile` | fetch current profile (+ validation issues) |
| `PUT /api/candidates/{id}/profile` | partial edit → new DRAFT version |
| `POST /api/candidates/{id}/profile/confirm` | DRAFT → CONFIRMED (via `promote_to_confirmed`) |
| `POST /api/candidates/{id}/resume` | upload PDF → extract → new DRAFT version |
| `GET /api/sources` | list ENABLED (non-MOCK) sources only |
| `POST /api/candidates/{id}/searches` | create a saved search |
| `GET /api/candidates/{id}/searches` | list a candidate's own saved searches |
| `GET/PUT/DELETE /api/searches/{id}` | fetch / edit / archive one saved search |
| `POST /api/searches/{id}/run` | submit + background-run → `{run_id, status:"QUEUED"}` |
| `GET /api/runs/{id}` | poll run status/counters |
| `GET /api/searches/{id}/results` | matched jobs (scored, deduped, filterable client-side) |
| `GET /api/candidates/{id}/report` | download the existing 9-sheet Excel workbook |
| `GET /api/candidates/{id}/dashboard` | summary across all of a candidate's searches |

## 5. Async Search Execution (STEP 7)

`POST /api/searches/{id}/run` calls the existing
`search_submission.submit_search()` (read/write, fast) to create the
QUEUED row, then starts a **background `threading.Thread`** running
the existing `search_worker.run_once(db_path, search_run_id=..., max_items=1)`
and returns immediately with `{run_id, status:"QUEUED"}` — it never
blocks the HTTP request for the multi-minute duration a real Naukri
search can take. `GET /api/runs/{id}` reads `search_runs.status`
directly — already exactly the vocabulary needed
(QUEUED/RUNNING/COMPLETED/PARTIAL/BLOCKED/FAILED), so no new status
model was invented.

## 6. Frontend (STEP 16) — `web/`

Plain HTML/CSS/vanilla JS, no build step, no framework (the repo's own
`package.json` had none before this phase — confirmed by inspection).
Pages: `/` (candidate + resume), `/profile` (review/edit/confirm),
`/searches/new` (criteria form, sources populated live from
`/api/sources` — only ever shows currently-ENABLED sources), `/dashboard`
(saved searches, run button, live status polling, Excel download),
`/searches/{id}/results` (table with status/score/priority filters,
`[Open Job]` external link only — **no apply button, no apply
endpoint, anywhere**).

## 7. Multi-User / Candidate Isolation (STEP 11)

This is a **local, single-user development tool** — there is no
login/session system, and the report does not claim otherwise. What
IS enforced server-side: every API route requires an explicit
`candidate_id`/`saved_search_id`/`run_id` in the URL, and every DB
query is scoped to it — one candidate's rows are never returned from
another candidate's endpoint calls. Verified by test (Section 9,
"isolation"). The browser only remembers *which* candidate_id it
created last (`localStorage`, documented in `web/app.js`) — purely a
UX convenience, explicitly not security.

## 8. Genericity Test (STEP 12)

Four personas were run through the identical, unmodified code path
with a fake offline adapter (zero live network calls):

| Candidate | Title | Skills | Location | Resume? |
|---|---|---|---|---|
| A | Senior SRE | AWS, Kubernetes, Terraform | Bangalore | no |
| B | Senior Java Developer | Java, Spring Boot, Kafka | Pune | no |
| C | Product Manager | SaaS, Agile, Product Strategy | Hyderabad | no |
| D | Data Scientist | Python, SQL, Machine Learning | Remote | no |

All four: created, profiled, confirmed, searched, run, and returned
results whose job title matched **that candidate's own** target role —
proving no hardcoded designation anywhere in `api/`/`web/`. (Their
actual match *scores* are governed entirely by the existing, protected
`score_job.py` 100-point rubric, which is intentionally
DevOps/SRE-weighted per `CLAUDE.md` — a non-DevOps candidate scoring
low there is correct, existing, out-of-scope behavior, not a Phase 9
defect.)

## 9. Test Results (STEPs 12/13/18)

New file: `scripts/test_phase9_api.py` — **58/58 checks passed**,
using FastAPI's in-process `TestClient` (no real socket, no live
network — a fake `FAKE_PHASE9_ENABLED` adapter is registered exactly
the way `test_phase8_daily_queue.py` already established as safe: only
`source_registry.list_sources` is monkeypatched, `get_adapter_status()`
itself is never touched, restored in a `finally` block). Covers:
genericity (4 personas), resume upload against Saroj's real PDF as a
dev fixture (DRAFT-only, never auto-confirmed), saved-search run +
async status polling, candidate isolation, and 14 distinct
security/validation checks (empty name, empty roles, negative salary,
NOT_ENABLED source rejected, unknown IDs → 404, non-PDF upload
rejected, path-traversal-style filenames handled safely via
server-generated names, malformed JSON → 422, path-traversal in URL →
404), a static-analysis check that no automatic-application-submission
token exists anywhere in `api/`/`web/`, and an Excel-download check.

**Full existing suite:** all 51 runnable pre-existing test files still
pass (2 documented live-Naukri-only tests, `test_naukri_adapter.py`
and `test_live_naukri_score_audit.py`, intentionally skipped, exactly
as before this phase). Zero regressions.

## 10. Security / Input Validation (STEP 14)

- File upload: extension + magic-byte (`%PDF-`) check, 8MB size cap,
  server-generated UUID filename (client filename never touches a
  filesystem path — no path-traversal surface).
- Pydantic schemas reject empty candidate names, empty target-role/
  location lists, negative salary/experience, out-of-range scores.
- `sources` is validated server-side against the live ENABLED list on
  every create/update — a NOT_ENABLED or unknown source name is a 400,
  never silently accepted.
- No raw SQL is ever built from client input (every query uses
  parameterized `?` placeholders); no client-supplied DB path anywhere.
- Unknown candidate/search/run IDs return 404, not a stack trace or
  filesystem probe.

## 11. No-Automatic-Application-Submission Audit (STEP 15)

Grepped every file in `api/` and `web/` for
`submit_application`/`apply_to_job`/`auto_apply` — **zero matches**
(also asserted by `test_phase9_api.py`, section 10). The only
job-related action a user can take on the results page is
`[Open Job]`, an ordinary `target="_blank"` link to the job's own URL
on the source site. There is no server route that could submit
anything to an external site.

## 12. Production DB Safety (STEP 19)

| | Before | After |
|---|---|---|
| SHA-256 | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Size | 114688 bytes | 114688 bytes |
| Row counts | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 | identical |

Verified both immediately after building the app and again after
running the entire test suite (new + all 51 existing files). The API
process itself has no code path that can ever open
`data/applications/jobos.db` — `api/db.py` hardcodes
`jobos_dev.db`.

## 13. launchd / Automation (STEP 20)

Not touched. No install/load/start command was run. `launchd/` is
unchanged from Phase 7.2/7.4/8.

## 14. New Files

`api/{__init__,db,schemas,profile_store,search_store,results_store,main}.py`,
`scripts/{init_dev_db,migrate_v3_saved_searches,test_phase9_api}.py`,
`web/{index,profile,search_new,dashboard,results}.html`,
`web/{app.js,style.css}` — 2,546 lines total. Modified (additive only,
zero behavior change proven by unmodified pre-existing tests):
`scripts/search_profile.py`, `scripts/search_submission.py`.
`requirements.txt` gained `fastapi`, `uvicorn`, `python-multipart`,
`httpx` (test-only).

## 15. Known Limitations (honest, not hidden)

- No real authentication — documented, single-user local tool by
  design for this MVP.
- Results are not paginated (fine at MVP data volumes; would need
  pagination before real multi-hundred-job volumes).
- The Excel report endpoint reports the candidate's full matched-job
  history across all their searches (the existing report model is
  candidate-scoped, not per-search) — building a per-search workbook
  would require extending `generate_run_report.py`, not attempted here
  per the "don't create a second report implementation" instruction.
- Non-DevOps candidates will generally score low/NOT_QUALIFIED against
  the existing, intentionally SRE/DevOps-weighted 100-point rubric —
  this is existing, protected, out-of-scope behavior (see Section 8).

## 16. How To Run Locally

See the new "Local Web App (Phase 9)" section added to `README.md`:
`.venv/bin/uvicorn api.main:app --reload --port 8420`, then open
`http://127.0.0.1:8420/`.

## STOP

Per the explicit instruction: launchd was not activated, no
application was submitted anywhere, LinkedIn/Hirist were not enabled
(their `AdapterStatus` remains `NOT_ENABLED`, unchanged, and
`/api/sources` proves only Naukri is ever offered). Phase 9 stops
here.
