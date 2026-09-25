# Phase 7 — Main Job Search Objective / End-to-End Reporting Gate

**Production DB never opened for a write in this task.** Naukri remains
`ENABLED`. Hirist and LinkedIn remain `NOT_ENABLED`. `_default_sources()`
remains `['NAUKRI']`. Exactly **one** live network request was made
(the SIXTH-step Naukri temp-DB validation) — no other step made any
network call.

---

## FIRST — Offline Architecture Audit (code-traced, not assumed)

**1. Where are search results stored?**
Two places, with different scopes:
- `jobs` (flat, global, 32+ columns) — the single system-of-record
  table every source's results land in, via `tracker.upsert_job()`
  (for eligible/scored jobs) or `search_worker._upsert_job_identity_only()`
  (for ineligible jobs — identity fields only, no score/priority).
- `candidate_job_matches` (candidate_id, job_id) — the newer,
  per-candidate-correct table, written **only** by
  `search_worker.upsert_candidate_job_match()`, **only** for jobs that
  passed eligibility. **0 rows in production today** — confirmed by
  direct query; no real candidate run has ever gone through
  `search_worker.py` against production.

**2. Where are `candidate_job_matches` generated?**
`search_worker.process_queue_item()` only — the sole writer. Confirmed
by grep: no other file inserts into this table.

**3. How are score/freshness/priority persisted?**
- Score/priority: `jobs.score`/`jobs.priority`, written by
  `tracker.upsert_job()`. **Not per-candidate** — `search_worker.py`'s
  own docstring documents this as a known limitation inherited from
  reusing that single-candidate-era function unchanged. In a
  single-real-candidate system (today's actual state) this is
  currently harmless, but it is a real constraint for any future
  second candidate.
- Freshness: **`jobs.freshness` is a real column** (added by
  `migrate_v2_schema.py`) but **is never written by any pipeline
  code**, confirmed by grep across the whole `scripts/` tree —
  `search_worker.py` computes freshness (via `job_ranking.build_ranking_record()`
  → `freshness.classify_freshness()`) but only ever attaches it to an
  **in-memory** `RankingRecord`, discarded when the process exits. This
  was the single biggest gap this audit found (see SECOND, below) and
  is what `generate_run_report.py` was built to close, by
  **recomputing** freshness fresh at report time rather than reading a
  column that is always empty.

**4. How is application status represented?**
`jobs.status`, one column, `application_schema.json`'s 16-value
lifecycle (`FOUND` → ... → `APPLIED` → ... → `REJECTED`/`GHOSTED`/`WITHDRAWN`).
(CLAUDE.md says "15-state" — the real list has 16 entries; a stale doc
count, not a code bug — not fixed here, not asked.)

Traced precisely: `tracker.upsert_job()`'s `ON CONFLICT DO UPDATE SET`
clause **deliberately excludes `status`** from the columns it
overwrites on re-ingest — so once a job is first scored, a later
re-scoring run can never silently clobber a status a human has since
progressed. This is correct, safe, existing design, confirmed by
reading the SQL, not assumed.

**Real, concrete finding: `status="REJECTED"` is ambiguous.**
`score_job()` itself writes `status="REJECTED"` for any
eligible-but-<70-scored job, at first-insert time. Separately,
`application_schema.json`'s lifecycle also defines `REJECTED` to mean
"a human applied and the employer rejected them." **No code in this
project currently ever advances a job's status through the
application lifecycle at all** (confirmed by tracing every writer of
the `status` column — there are exactly two, both described above,
and neither implements a human-approval/application workflow, matching
CLAUDE.md's own "Human approval workflow: Missing" / "Application
submission pipeline: Missing"). So today, every real `status="REJECTED"`
row got there via the score bucket, never a real rejection.
`generate_run_report.py` treats `status="REJECTED"` as an **exclusion**
signal, not "already applied" — documented explicitly in that module's
own docstring so the choice is auditable. **Not fixed at the schema
level** (would require changing `score_job.py`'s status vocabulary —
out of this task's scope, not requested).

**5. How are duplicate jobs represented?**
Two independent, non-overlapping mechanisms:
- **Intra-source exact duplicates**: `discover_local.deduplicate()`,
  keyed on `(source, job_id)` — collapsed before persistence, never
  visible in the DB at all.
- **Cross-source duplicate CANDIDATES**: `cross_source_dedup.find_cross_source_duplicate_candidates()`
  over `canonical_job.derive_canonical_job()` output — a **candidate**
  detector, never auto-merging; both sides keep full provenance. Like
  freshness, this is **computed but never persisted** by
  `search_worker.py` (same in-memory-only `ranking_records` path).
  `generate_run_report.py` re-runs this at report time (pure, cheap,
  deterministic) to populate the new `DUPLICATES` sheet.

**6. How are already-applied jobs separated?**
Only by `jobs.status` (see #4) — no dedicated flag/table. Today, this
is entirely manual (no code path sets it beyond the initial insert).

**7. How are excluded/ineligible jobs represented?**
`_upsert_job_identity_only()` inserts them into `jobs` with **only**
identity/descriptive fields — `score`, `priority`, `matched_skills`,
`missing_skills`, `hard_reject_reasons` are **never set** for this
path (default `score=0`, `priority=NULL`). **The per-job exclusion
reason (experience/location gate) is never persisted anywhere** — only
an aggregate count (`search_runs.jobs_experience_excluded`) survives.
`generate_run_report.py` recovers the per-job reason by re-running
`job_eligibility.assess_job_eligibility()` fresh (via
`build_ranking_record()`), not by reading anything from the DB that
doesn't exist.

Separately, a job that passes the eligibility gate but scores `<70`
**is** persisted with a real `score`/`priority="REJECT"`/
`hard_reject_reasons` (score_job()'s own internal mandatory-skill
gate) — so "why excluded" is answerable for this case directly from
the DB, unlike the eligibility-gate case above.

**8. Where is `application_url`/`job_url` stored?**
Both are plain `jobs` columns, populated by every adapter's normalized
output, unchanged by this audit.

**9. What reporting/export code currently exists?**
`scripts/generate_daily_report.py` — HTML + CSV, reading `jobs.score/priority/status`
directly (score≥70, active statuses only), sorted score desc then
priority. Simple, working, **left completely unmodified** by this task.
It does **not** consult `candidate_job_matches`, freshness, or
cross-source dedup — all three didn't exist yet when it was written.

**10. Does Excel generation already exist?**
**No** — confirmed by `grep -rl "openpyxl\|xlsxwriter\|\.xlsx" scripts/`
returning nothing before this task. `openpyxl` was not an installed
dependency (`requirements.txt` had only `pypdf`).

**11. Does a Markdown/JSON run report already exist?**
Only in the sense of this project's many **live-validation** reports
(`data/reports/naukri_*`, `hirist_*`, etc.) — none of these are
*daily job-search* reports; they are one-off engineering validation
artifacts. No standing "run report" for a search_worker execution
existed before this task.

**12. Is there a run summary with sources/queries/raw/normalized/
duplicates/eligible/excluded/matches/score distribution/priority
distribution/freshness distribution/errors?**
**Partially, split across two places, neither complete alone:**
- `search_runs` (persisted): `queries_total`, `queries_completed`,
  `jobs_discovered`, `jobs_deduplicated`, `jobs_experience_excluded`,
  `jobs_eligible`, `jobs_scored`, `jobs_ready`, `errors_count`,
  `blocked_queries`. **No score/priority/freshness distribution** —
  those are never aggregated or persisted anywhere.
- `WorkItemResult` (in-memory only, gone when the process exits):
  everything `search_runs` has, plus `ranking_records` (source of
  score/freshness/dedup detail) — but never written anywhere.
`generate_run_report.py`'s `RUN_SUMMARY` sheet is the first place both
halves are combined into one artifact (see SECOND, below).

**13. Can the output distinguish NEW / ALREADY APPLIED / REJECTED /
DUPLICATE / INELIGIBLE?**
**No, not before this task.** `generate_daily_report.py` shows only
"qualified, active-status" jobs — no NEW/DUPLICATE distinction at all,
and REJECTED/INELIGIBLE jobs are invisible (filtered out by its own
`score >= 70` WHERE clause). This is exactly the gap `generate_run_report.py`'s
sheet classification closes.

**14. Are application links clickable in the final Excel?**
**No Excel existed at all before this task** (see #10). The existing
HTML report does render clickable `<a href>` tags. The new
`generate_run_report.py` workbook writes **real `openpyxl` hyperlinks**
(`cell.hyperlink` + `"Hyperlink"` style) on every `Job URL`/
`Application URL` cell — confirmed programmatically (see FIFTH step).

**15. Is the report sorted in an actionable way?**
`generate_daily_report.py`: score desc, then priority, then posted
date desc — reasonable, unchanged. The new workbook sorts
`APPLY_TODAY`/`ALL_MATCHING_JOBS`/`NEW_JOBS` by priority (A→B→C) then
score desc; `ALREADY_APPLIED`/`APPLICATION_TRACKER` by last-seen desc;
`REJECTED_EXCLUDED` by score desc (so a near-miss sorts above a hard
reject).

---

## SECOND — The Required Daily Output (`scripts/generate_run_report.py`, new)

Nine sheets, exactly as requested. `generate_daily_report.py` is
**unchanged** — this is a new, additive module, not a redesign,
consuming the more complete `jobs` + `search_runs` state via the same
already-existing `job_ranking.build_ranking_record()` every value in
it is grounded in.

| Sheet | Source | Columns | Sort | Filter | URLs clickable |
|---|---|---|---|---|---|
| **APPLY_TODAY** | Current DB state, re-scored live via `build_ranking_record()` | Priority, Score, Title, Company, Location, Source, Job URL, Application URL, Experience Required, Freshness, Freshness (days), Matched Skills, Gaps, Exclusion/Gap Reason, Application Status, First Discovered, Last Seen, Resume Variant | Priority, then score desc | qualifying (priority A/B/C) AND status in `FOUND/SCREENING/SHORTLISTED/READY_FOR_APPROVAL/APPROVED` | Yes |
| **ALL_MATCHING_JOBS** | same | same columns | same | qualifying, any status | Yes |
| **NEW_JOBS** | same | same columns | same | qualifying AND `created_at >= since` cutoff | Yes |
| **ALREADY_APPLIED** | same | same columns | Last Seen desc | status in the applied-lifecycle set (excludes `REJECTED` — see FIRST #4) | Yes |
| **REJECTED_EXCLUDED** | same | same columns | Score desc | NOT qualifying (ineligible OR score<70) AND not already-applied | Yes |
| **DUPLICATES** | `cross_source_dedup` over the same batch | Title, Company, Confidence, Signals, Reason, Source A, Job URL A, Source B, Job URL B | pair order | any row involved in ≥1 cross-source duplicate candidate | Yes |
| **APPLICATION_TRACKER** | same as above, unfiltered | same columns as job sheets | Last Seen desc | **none — every row, every status/score** | Yes |
| **SOURCE_HEALTH** | `search_runs` table, raw | every `search_runs` column | created_at desc | candidate-scoped if given | N/A |
| **RUN_SUMMARY** | latest `search_runs` row + computed distributions over the current report's rows | Metric / Value (flattened) | — | — | N/A |

**DB-state vs. current-run-state**: every job-listing sheet reflects
**current DB state** (all jobs ever ingested for this candidate,
re-scored fresh against the candidate's *current* confirmed profile —
exactly mirroring `search_worker.py`'s own "fresh scoring, frozen
query" design). `SOURCE_HEALTH`/`RUN_SUMMARY.latest_run` reflect the
**most recent search_run specifically**. `NEW_JOBS` bridges both: DB
state, filtered by a run-boundary-ish `since` cutoff.

**`APPLY_TODAY`'s required columns** (task's own explicit list) —
confirmed present: priority ✓, score ✓, title ✓, company ✓, location ✓,
source ✓, job URL ✓, application URL if available ✓ (falls back to job
URL when absent, matching `generate_daily_report.py`'s own precedent),
experience ✓, freshness ✓, matched skills/reasons ✓ (`strong_matches`/
`gaps` from `score_explanation`), exclusion reason if applicable ✓
(`eligibility_reasons`), current application status ✓, first discovered
date ✓, last seen date ✓. **No field was invented** — every column is
either a direct `jobs` column or a value `build_ranking_record()`
already computes from existing, tested functions.

---

## THIRD — Scoring Contract Verification

Read `scripts/score_job.py` directly (not modified): per-dimension
maximums are 20/15/15/10/10/10/5/5/5/5, summing to exactly 100,
matching CLAUDE.md's table dimension-for-dimension (Core role 20,
SRE/DevOps 15, AWS/Azure 15, Kubernetes 10, Terraform/IaC 10, CI/CD 10,
Observability 5, Experience 5, Location/work model 5, Overall/domain
fit 5). `config/application_schema.json`'s `priority_rules` (A≥90,
B≥80, C≥70, REJECT≤69) also matches CLAUDE.md exactly.

**The reporting layer consumes this score unmodified**:
`generate_run_report.py` never computes a score itself — every score
shown comes from `job_ranking.build_ranking_record()`'s call to the
real, unmodified `score_job.score_job()`, the exact same function
`search_worker.py` itself calls. Confirmed by code reading (one
call site, `job_ranking.py:112`) and empirically (SIXTH step's live
run and the FIFTH step's synthetic run both show scores consistent
with hand-traced dimension math — see FIFTH step for one fully
hand-verified example).

**No weight was changed. `score_job.py` was not modified.**

---

## FOURTH — Freshness Verification

`freshness.py`'s categories are **HOT / FRESH / AGING / OLD / STALE /
UNKNOWN** — six, not the five the task text lists (HOT/FRESH/AGING/OLD/UNKNOWN).
`STALE` (age > 30 days) is a strict superset addition on top of the
five named — not a conflict, not altered here (**"do not alter
freshness semantics without evidence" — none was altered; this is
simply what the existing, already-implemented module already does**).
Thresholds, read directly from the code: HOT ≤2 days, FRESH ≤7, AGING
≤14, OLD ≤30, STALE >30, UNKNOWN if unparseable or a future-dated
posting.

**The report distinguishes all six** — confirmed by the FIFTH step's
synthetic test (`freshness_distribution` in `RUN_SUMMARY` shows real
per-category counts) and the SIXTH step's live run (`{"HOT": 14, "FRESH": 6}`
observed on real Naukri data, consistent with the requested
`max_job_age_days=3` constraint — every real result was ≤7 days old,
none `AGING`/`OLD`/`STALE`/`UNKNOWN`, exactly as expected when Naukri's
native freshness filter is doing its job).

`max_job_age_days=3` itself: confirmed still the default/expected
value at the `search_profile`/`query_planner` layer, and was passed
explicitly end-to-end in the SIXTH step's live query, mapped to
Naukri's native `jobAge` parameter by the existing, unmodified
`naukri_adapter._map_to_naukri_freshness_option()`.

---

## FIFTH — Offline End-to-End Test (synthetic, temp DB, zero network)

New file: `scripts/test_generate_run_report.py`, built on this
project's own established `_new_isolated_db()`/`_seed_confirmed_candidate()`
temp-DB pattern (mirrored from `test_search_worker_ranking_integration.py`,
per this project's documented "self-contained test files" convention).
Drives the **real, unmodified** `search_worker.process_queue_item()`
via two deterministic fake adapters (`FAKE_SOURCE_A`/`FAKE_SOURCE_B`,
no network), then `generate_run_report.generate()` against the same
temp DB.

**8 jobs, covering every required scenario:**

| Job | Scenario | Result |
|---|---|---|
| `A-HIGH-001` | eligible, rich JD | **score=100 (A)** — hand-verified dimension-by-dimension: 20(core)+15(sre)+15(cloud)+10(k8s)+10(terraform)+10(cicd)+5(observability)+5(experience)+5(location)+5(domain)=100 |
| `A-MED-002` | eligible, moderate JD, **no application_url** | **score=80 (B)** — hand-verified: 20+10+15+5+10+5+0+5+5+5=80 |
| `A-LOW-003` | eligible, minimal JD | **score=75 (C)** — hand-verified: 20+5+10+5+10+5+0+5+5+5=75 |
| `A-STALE-004` | eligible, rich JD, `posted_date` 111 days old | score=100, **freshness=STALE**, still in `ALL_MATCHING_JOBS` |
| `A-LOWEXP-005` | 1-3 yrs required, candidate has 11 | `eligible=False`, `REJECTED_EXCLUDED`, reason captured verbatim |
| `A-HIGH-001` + `B-HIGH-MIRROR-001` | same title/company/location, different sources | both flagged `DUPLICATES`, 1 pair detected |
| `A-APPLIED-006` | status manually set to `APPLIED` post-ingestion | `ALREADY_APPLIED` only — **not** `APPLY_TODAY`/`ALL_MATCHING_JOBS` |
| `A-PREVSEEN-007` | `created_at` backdated before the `since` cutoff | `ALL_MATCHING_JOBS`, **not** `NEW_JOBS` |

All 3 qualifying, non-backdated jobs correctly land in `NEW_JOBS`.
Workbook written, **all 9 required sheet names present, exact match**.
Hyperlink object confirmed present and correct on a spot-checked cell
(`ws.cell(row=2, column=7).hyperlink` → real `openpyxl.worksheet.hyperlink.Hyperlink`,
`ref='G2'`).

**Result: 100% pass**, all assertions green (see verification output
below).

---

## SIXTH — Native Naukri Temp-DB End-to-End Test (ONE live query)

**Exactly one live HTTP/browser request was made in this entire Phase
7 task — right here, nowhere else.**

**Mechanism**: `HiristAdapter.search()`-style safety was not needed
here (Naukri is `ENABLED`) — the **real, unmodified**
`search_worker.process_queue_item()` was driven directly, via
`submit_search()` + `claim_next_queue_item()` + one call to
`process_queue_item()`, against a **temporary** DB only
(`init_tracker.py` + `migrate_v2_schema._create_new_tables/_ensure_job_columns`).
`data/applications/jobos.db` was never opened.

**Candidate profile**: real skills/experience/certifications loaded
directly from `config/profile.json` (never fabricated), with
`target_roles`/`target_locations` narrowed to exactly
`["Infrastructure Engineer"]` / `["Bangalore"]` so the query plan
contains exactly **one** query. **Confirmed offline, before the live
call**, via `submit_search(..., dry_run=True)`:
`query_plan=[{"source": "NAUKRI", "role": "Infrastructure Engineer", "location": "Bangalore", "max_job_age_days": 3}]`
— exactly one entry.

**The one live call**: `discover_from_sources()` → `NaukriAdapter().search()`
→ `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore?jobAge=3`
— the same, already-proven-live URL-construction path Naukri has used
throughout this project. **Zero retries** (one `process_queue_item()`
call, no loop).

**Captured results**:

| Metric | Value |
|---|---|
| Raw jobs discovered | **20** |
| Normalized | 20 (0 malformed) |
| Deduplicated (intra-source) | 0 |
| Unique | 20 |
| Experience-excluded | **16** |
| Eligible | **4** |
| Scored | 4 |
| Ready (READY_FOR_APPROVAL) | 1 |
| Score range | **8 – 90** |
| Priority counts | A=1, REJECT=3, INELIGIBLE=16 |
| Freshness distribution | HOT=14, FRESH=6 (none AGING/OLD/STALE/UNKNOWN — consistent with `max_job_age_days=3`) |
| Cross-source duplicate pairs | 0 (single-source run) |
| `APPLY_TODAY` count | **1** |
| `NEW_JOBS` count | **1** (see bug/fix below) |
| Already-applied | 0 |
| Rejected/excluded | 19 |
| Errors | 0 |
| Blocked queries | 0 |
| Workbook | `data/reports/phase7_naukri_tempdb_validation/NON_PRODUCTION_naukri_tempdb_report.xlsx` |

Sample real job URLs captured (all real, all clickable in the
workbook): `naukri.com/job-listings-infrastructure-engineer-a-p-moller-maersk-...`,
`...-sr-technical-lead-cloud-infra-engg-birlasoft-...`,
`...-senior-infrastructure-engineer-file-transfer-network-integrations-wells-fargo-...`
(20 total, all persisted as both `job_url` and `application_url` — Naukri's
own adapter contract sets both to the same listing URL, confirmed by
inspecting the real captured rows).

### A real bug this live run caught and fixed

`generate_run_report.py`'s `NEW_JOBS` cutoff comparison initially used
**raw string comparison** between `since` (ISO, `"...T00:00:00"`) and
`jobs.created_at`. SQLite's own `DEFAULT CURRENT_TIMESTAMP` produces a
**space-separated** format (`"2026-09-20 16:04:48"`), and the space
character (`0x20`) sorts *before* `"T"` (`0x54`) — so a same-day
SQLite-formatted timestamp always string-compared as "earlier" than an
ISO cutoff on the same date, **regardless of actual time-of-day**. This
live run's single new job (created at `16:04:48` today) was silently
excluded from `NEW_JOBS` (`new_jobs_count: 0`) by this bug — the FIFTH
step's synthetic test never caught it because its `since` cutoffs
crossed a *day* boundary, which masked the bug (differing day digits
resolve the string comparison before it ever reaches the space-vs-`T`
character).

**Fixed**: added `generate_run_report._parse_timestamp()`, which
parses both formats into timezone-aware `datetime` objects (assuming
UTC for the naive SQLite form, since every writer in this codebase
already uses UTC) and compares those, never raw strings. **Verified
the fix purely offline**, by re-running `generate_run_report.generate()`
against the *same already-fetched* temp DB (zero new network calls):
`new_jobs_count` became **1**, correctly. The saved workbook/dump in
`data/reports/phase7_naukri_tempdb_validation/` reflect the **fixed**
code. This is exactly the kind of "concrete reporting integration bug"
the task's own instructions anticipated and authorized fixing — no
Naukri discovery logic was touched.

**Safety**: `data/applications/jobos.db` SHA-256 confirmed
byte-identical before and after this step (below). No retries. No
authentication/CAPTCHA. Naukri remained `ENABLED` throughout (as it
already was) — nothing about its status changed.

---

## SEVENTH — Report Quality Acceptance Test

| # | Question | Answerable from the new workbook alone? |
|---|---|---|
| 1 | What should Saroj look at today? | **Yes** — `APPLY_TODAY` |
| 2 | Why did each job qualify? | **Yes** — `Matched Skills / Strong Points` + `Gaps` columns (from `score_explanation`) |
| 3 | What is the score? | **Yes** — `Score`/`Priority` columns |
| 4 | How fresh is it? | **Yes** — `Freshness` + `Freshness (days)` |
| 5 | Where is the job? | **Yes** — `Location` |
| 6 | Which source found it? | **Yes** — `Source` |
| 7 | Is it new or already seen? | **Yes** — `NEW_JOBS` sheet membership + `First Discovered` |
| 8 | Has Saroj already applied? | **Yes** — `Application Status` column + `ALREADY_APPLIED` sheet |
| 9 | Clickable job/application link? | **Yes** — real `openpyxl` hyperlinks, confirmed programmatically |
| 10 | Why were other jobs excluded? | **Yes** — `REJECTED_EXCLUDED`'s `Exclusion / Gap Reason` column |
| 11 | Did any source fail or return zero results? | **Yes** — `SOURCE_HEALTH` (`errors_count`, `blocked_queries`, `status`) |
| 12 | How many jobs discovered / survived each stage? | **Yes** — `RUN_SUMMARY` (raw → normalized → dedup → eligible → scored → ready, plus score/priority/freshness distributions) |

**All 12 answerable without opening the database manually** — confirmed
both on the FIFTH step's synthetic data and the SIXTH step's real live
Naukri data.

**Before this task**, only questions 3, 5, 6, 9 (HTML only) were
reliably answerable from `generate_daily_report.py` alone; 1/2/4/7/8/
10/11/12 required manual DB inspection or were not answerable at all
(no freshness, no dedup, no per-job exclusion reason, no run/source
health surfaced anywhere).

---

## Verification

```
Full standalone test suite (46 files, .venv — needed for the one
openpyxl-dependent test file, test_generate_run_report.py):   PASS=46 FAIL=0
python3 -m py_compile across all scripts/*.py:                 PY_COMPILE_ALL_OK
JSON validation (this report's .json + the SIXTH-step dump):   valid
```

| | Value |
|---|---|
| `NaukriAdapter.status` | `AdapterStatus.ENABLED` (unchanged) |
| `HiristAdapter.status` | `AdapterStatus.NOT_ENABLED` (unchanged) |
| `LinkedInAdapter.status` | `AdapterStatus.NOT_ENABLED` (unchanged) |
| `search_profile._default_sources()` | `['NAUKRI']` (unchanged) |
| Production DB SHA-256 before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production DB SHA-256 after | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production row counts before/after | Unchanged (candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0) |
| Production DB opened for a write | **Never** |
| Live network requests, this whole task | **1** (SIXTH step only) |

## A New Local Dependency: `openpyxl`

No `.xlsx` writer library was available (`requirements.txt` had only
`pypdf`; the system Python is Homebrew-managed and PEP 668-protected
against global `pip install`). **Per explicit user confirmation**, a
project-local `.venv/` was created (already `.gitignore`d) and
`openpyxl==3.1.5` installed into it — the system/Homebrew Python was
never touched. `requirements.txt` updated to record this new
dependency. **Any script that imports `openpyxl` (currently only
`generate_run_report.py` and `test_generate_run_report.py`) must be run
via `.venv/bin/python3`**, not plain `python3` — every other script in
this project is unaffected and continues to run under the system
Python exactly as before.

## Files Changed

| File | Change |
|---|---|
| `scripts/generate_run_report.py` | **New** — the 9-sheet reporting module (see SECOND) |
| `scripts/test_generate_run_report.py` | **New** — Phase 7 FIFTH-step offline end-to-end test (8 scenarios) |
| `requirements.txt` | Modified — added `openpyxl==3.1.5` |
| `.venv/` | **New**, local, gitignored — not a tracked project change |
| `data/reports/phase7_naukri_tempdb_validation/` | **New** — non-production live-validation artifacts (workbook, dump JSON, README) |
| `data/reports/phase7_reporting_audit.md` / `.json` | **New** — this report |

**Not modified:** `scripts/generate_daily_report.py`, `scripts/score_job.py`,
`scripts/tracker.py`, `scripts/search_worker.py`, `scripts/job_ranking.py`,
`scripts/score_explanation.py`, `scripts/freshness.py`,
`scripts/job_eligibility.py`, `scripts/cross_source_dedup.py`,
`scripts/canonical_job.py`, `scripts/naukri_adapter.py`,
`scripts/naukri_parser.py`, `scripts/naukri_fetcher.py`,
`scripts/hirist_*.py`, `scripts/linkedin_adapter.py`,
`scripts/source_registry.py`, `config/profile.json`,
`config/application_schema.json`, `data/applications/jobos.db`.

## Summary

The core pipeline (discover → normalize → dedup → eligibility → score
→ track) was already real, tested, and working, via
`search_worker.py`. The gap was entirely in the **reporting layer**:
freshness and cross-source-duplicate detection were already computed
but silently discarded after every run, per-job exclusion reasons for
eligibility-gate rejects were never persisted, no Excel/workbook
generation existed, and nothing distinguished NEW/ALREADY-APPLIED/
REJECTED/DUPLICATE jobs anywhere. `generate_run_report.py` closes this
gap **by reusing every existing pure function unchanged** — it is pure
orchestration + a new `.xlsx` renderer, not a second scoring/eligibility/
freshness implementation. Verified offline against a comprehensive
synthetic scenario, then verified once more against **one real, live
Naukri query**, which additionally caught and led to the fix of one
genuine timestamp-comparison bug in the new module itself before this
report was finalized.

**Stopping here, per instruction. Not proceeding to Hirist Step 12. Not
implementing LinkedIn. Not running a multi-source production search.**
