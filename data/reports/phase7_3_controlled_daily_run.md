# Phase 7.3 — Controlled Real Daily-Run Gate

**Production DB byte-identical before and after. Zero application
submissions. launchd was NOT installed, NOT loaded, NOT started.
Naukri is the only source that made any request; Hirist and LinkedIn
were never touched.**

## Main Objective — Is It Proven?

**YES: "Real daily Naukri search → actionable Excel report" is proven
end-to-end**, using the exact `run_daily_search.sh` → `run_search_worker.py
--once` → `generate_run_report.py` chain Phase 7.2 built, driven by
the **real, unmodified** `NaukriAdapter` against a **temporary,
disposable database** (production was never opened). One real job
("Hiring For AWS Cloud Infra and DevOps Engineer" at Tenarai
Technologies, score 90, priority A) landed correctly in `APPLY_TODAY`
with a working hyperlink, and the full 9-sheet workbook + `run_summary.json`
reconcile exactly against the worker's own reported counts.

**The scheduler itself is NOT live.** launchd was not installed,
loaded, or started at any point — see the Safety Confirmations section.

## An Important Finding, Disclosed Upfront

**This validation made 22 total HTTP requests, not 1.** The task
specified "exactly one bounded live query" / "no second query" / "no
details beyond what the normal worker itself requires" — the second
phrase turned out to matter: `NaukriAdapter.search()` (confirmed by
reading `scripts/naukri_adapter.py`, unmodified) does not merely fetch
one search-results page — for **every** raw job listing it finds on
that one page, it also fetches that job's own **detail page**, via
`parse_detail_page()`, to get the full JD text needed for accurate
10-dimension scoring (a search-results snippet alone is normally too
short). Additionally, `health_check_is_advisory = True` means
`discover_from_sources()` also fetches the Naukri homepage once,
advisory-only, before the search itself. This is **all pre-existing,
already-reviewed, already-tested `NaukriAdapter` behavior — nothing
new was introduced or modified in this task**, confirmed by reading
the adapter's own code, not merely inferred from the log.

**Exact breakdown** (from `data/daily/2026-09-20/worker.log`, 22
`DIAGNOSTIC` lines total, every one `"status":200`):

| Requests | Purpose |
|---|---|
| 1 | Homepage (`health_check()`, advisory) |
| 1 | Search-results page (`infrastructure-engineer-jobs-in-bangalore?jobAge=3`) |
| 20 | One detail-page fetch per raw job found (`raw_count=20`) |
| **22** | **Total** |

**No retries, no blocks, no CAPTCHA, no second logical query** — this
was still genuinely **one** search cycle (`--once`, one queue item,
one `process_queue_item()` call), and every one of the 22 requests
resulted from that single call, deterministically, per the adapter's
own existing logic. **This is exactly what "the normal worker itself
requires" for one query with 20 results** — not a deviation.

**A retroactive note, for honesty**: this is the first time in this
project's history that a Naukri validation's exact HTTP request count
was captured at this granularity (via the wrapper's own detailed
`worker.log`). Earlier Naukri validations in this project (including
Phase 7's SIXTH step, which also drove `process_queue_item()` against
a real `NaukriAdapter`) almost certainly made this same 20-detail-page
pattern too, but were reported/characterized in terms of "one logical
query" rather than a precise total HTTP count, since that level of
request-by-request logging didn't exist yet at the time. Flagging this
now so the record is accurate going forward — it does not change the
safety conclusion of any prior task (no block, no CAPTCHA, no retry
ever occurred in any of them either), but the earlier "1 live request"
phrasing undercounted the true HTTP count.

---

## STEP 1 — Read-Only Precheck

| | Value |
|---|---|
| Production DB SHA-256 | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production DB size | 114688 bytes |
| `candidates` | 1 |
| `candidate_search_profile` | 1 |
| `jobs` | 9 |
| `candidate_job_matches` | 0 |
| `search_runs` | 0 |
| `search_queue` | 0 |
| Naukri | `AdapterStatus.ENABLED` |
| Hirist | `AdapterStatus.NOT_ENABLED` |
| LinkedIn | `AdapterStatus.NOT_ENABLED` |
| `_default_sources()` | `['NAUKRI']` |
| Headless default | Confirmed — `naukri_fetch_bridge.js`: `headless = process.env.JOBOS_BROWSER_HEADLESS !== '0'` (headless unless explicitly disabled) |
| Chrome UA override | Confirmed intact — `JOBOS_NAUKRI_USER_AGENT` env override with `DEFAULT_NAUKRI_USER_AGENT` fallback, unmodified |
| `max_job_age_days=3` | Confirmed available as an explicit `SearchQuery`/`search_profile` parameter |

**Nothing was changed at this step.**

---

## STEP 2 — Redirecting the Wrapper to a Temp DB (smallest safe mechanism)

`scripts/run_daily_search.sh` had no way to target a non-production DB
at all. **Smallest possible diagnostic-only change**: one line —

```bash
PRODUCTION_DB="${JOBOS_DAILY_DB_OVERRIDE:-$PROJECT_ROOT/data/applications/jobos.db}"
```

- **Unset (the default, every installed/scheduled run)**: byte-for-byte
  identical behavior to Phase 7.2 — same production path, same
  command structure.
- **Set (this validation only)**: redirects `--db` to a disposable
  temp DB. A visible banner (`stderr`) and a scheduler-log line fire
  whenever the override is active, so a diagnostic run can never be
  mistaken for a real one later.
- **A bug caught and fixed while building this**: the override banner
  initially wrote to `logs/daily_search_scheduler.log` even during
  `--dry-run`, breaking that mode's "zero filesystem changes"
  guarantee. Fixed by moving the log call after the dry-run early-exit
  — re-verified offline (see Step 9).

**No production configuration, production DB path meaning, or safety
check was weakened.** Verified: `scripts/test_daily_scheduler_offline.py`'s
existing check ("wrapper script's `PRODUCTION_DB` points at the real
production DB path, not a temp DB") still passes unmodified, since the
real path remains the literal default.

**Output directory was NOT redirected** — `data/daily/YYYY-MM-DD/`
still resolves to the real path, per the task's own Step 4 expectation.
Only the *database* is disposable; the *generated report location*
matches exactly where a real scheduled run would put it (this
directory is git-ignored, generated-only content — no production data
was written there, only report artifacts derived from the temp DB).

---

## STEP 3 — The One Live Naukri Daily-Run Validation

| | Value |
|---|---|
| Command | `JOBOS_DAILY_DB_OVERRIDE=<temp db path> scripts/run_daily_search.sh` |
| Role | Infrastructure Engineer |
| Location | Bangalore |
| Freshness | `max_job_age_days=3` |
| Candidate | `saroj` (real skills/experience from `config/profile.json`, target role/location narrowed to exactly this one query — confirmed via `dry_run=True` before submitting: `query_count=1`) |
| Source | `NAUKRI` (real, unmodified `NaukriAdapter`) |
| Search URL | `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore?jobAge=3` |
| Total HTTP requests | **22** (see disclosure above) |
| HTTP status, every request | 200 |
| Classifier result | `VALID_RESULTS` (no block/challenge) |
| Retries | 0 |
| Second logical queries | 0 |
| CAPTCHA interaction | 0 |
| Anti-bot bypass | 0 |
| Synthetic job list substituted | No — 100% real Naukri data |

**The real, unmodified production `NaukriAdapter` and the real,
unmodified `search_worker`/`generate_run_report` pipeline were used
throughout** — nothing about the search/scoring/eligibility/freshness/
dedup logic was bypassed, mocked, or substituted; only the database
target was diverted (Step 2).

---

## STEP 4 — Daily Artifacts Verification

```
data/daily/2026-09-20/
  job_search_report.xlsx   (26298 bytes)
  run_summary.json          (1214 bytes)
  worker.log                 (8226 bytes)
```

**Workbook opens successfully** (`openpyxl.load_workbook()`, no error)
and contains **exactly** the 9 required sheets, confirmed by exact-set
comparison: `APPLY_TODAY`, `ALL_MATCHING_JOBS`, `NEW_JOBS`,
`ALREADY_APPLIED`, `REJECTED_EXCLUDED`, `DUPLICATES`,
`APPLICATION_TRACKER`, `SOURCE_HEALTH`, `RUN_SUMMARY`.

| Requirement | Verified |
|---|---|
| Workbook opens | Yes |
| Every required sheet exists | Yes — exact match |
| URLs clickable/hyperlinked | Yes — confirmed real `openpyxl.worksheet.hyperlink.Hyperlink` objects on both Job URL and Application URL cells |
| Score present | Yes (`90` on the one `APPLY_TODAY` row) |
| Priority present | Yes (`A`) |
| Freshness present | Yes (`FRESH`, 3 days) |
| Source present | Yes (`NAUKRI`) |
| Location present | Yes (`Bengaluru`) |
| Application/job URL present | Yes, both, real Naukri listing URLs |
| New/seen classification present | Yes (`Previously Seen = No`, correctly in `NEW_JOBS`) |
| Application status present | Yes (`READY_FOR_APPROVAL`) |
| Exclusion reasons present | Yes — `REJECTED_EXCLUDED`'s 19 rows all carry a populated reason/gap column |
| Duplicate information present | Yes — `DUPLICATES` sheet exists (0 rows this run — single source, nothing to compare cross-source) |
| Source health present | Yes — `SOURCE_HEALTH` has 1 row for this `search_run` |
| Run totals reconcile | **Yes — see below** |

**Reconciliation** (worker's own printed counts vs. workbook/`run_summary.json`):

| Metric | Worker log | run_summary.json / workbook |
|---|---|---|
| Raw | 20 | `jobs_discovered: 20`, `APPLICATION_TRACKER`: 20 rows |
| Normalized | 20 | matches |
| Duplicates removed (intra-source) | 0 | `jobs_deduplicated: 0` |
| Excluded (experience) | 16 | `jobs_experience_excluded: 16` |
| Excluded (location) | 0 | consistent — 16 ineligible total, all experience |
| Eligible | 4 | `jobs_eligible: 4`, `eligible_count: 4` |
| Scored | 4 | `jobs_scored: 4` |
| Ready for approval | 1 | `jobs_ready: 1`, `apply_today_count: 1` |
| Matches created | 4 | (all 4 eligible jobs got a `candidate_job_matches` row) |

**Every number reconciles exactly. No discrepancy.**

---

## STEP 5 — Main Objective / Question Verification

| # | Question | Answered by |
|---|---|---|
| 1 | What jobs did we find today? | `APPLICATION_TRACKER` (20 rows) |
| 2 | Which are actionable? | `APPLY_TODAY` (1 row) |
| 3 | Why is each actionable? | `Matched Skills / Strong Points` column |
| 4 | What is its score? | `Score` column (90) |
| 5 | What is its priority? | `Priority` column (A) |
| 6 | How fresh is it? | `Freshness` + `Freshness (days)` (FRESH, 3) |
| 7 | Where is it located? | `Location` column (Bengaluru) |
| 8 | Which source produced it? | `Source` column (NAUKRI) |
| 9 | Is it new for this candidate? | `Previously Seen = No`, in `NEW_JOBS` |
| 10 | Has it already been applied to? | `Application Status` column + `ALREADY_APPLIED` sheet (empty this run) |
| 11 | Why was something excluded? | `REJECTED_EXCLUDED`'s `Exclusion / Gap Reason` column (all 19 populated) |
| 12 | Did any source/search fail? | `SOURCE_HEALTH`: `errors_count=0`, `blocked_queries=0`, `status=COMPLETED` — no |

**`APPLY_TODAY` contains exactly one row, verified against every Phase
7.1 rule**:

| Rule | Verified |
|---|---|
| Qualifying priority | A (score 90 ≥ 90) |
| ≤3-day freshness | `Freshness (days) = 3` |
| Usable URL | Yes, both Job URL and Application URL populated |
| Not duplicate-suppressed | `Duplicate (Suppressed) = No` |
| Not already applied | `Application Status = READY_FOR_APPROVAL`, not an applied-lifecycle value |
| Not score-rejected | `Report Status = READY_TO_APPLY`, not `SCORE_REJECTED_NOT_QUALIFIED` |
| Not ineligible | `Eligible = Yes` |

**All 7 gates independently confirmed on the one real row that made
it through.** No job violating any of these criteria appears in
`APPLY_TODAY` — confirmed by inspecting all 20 `APPLICATION_TRACKER`
rows: exactly 1 satisfies all 7 gates simultaneously, and it is the
one shown.

---

## STEP 6 — Status Semantics, Verified Live for the First Time

| Case | Live result |
|---|---|
| `NOT_QUALIFIED` (the 3 REJECT-priority jobs — **first live confirmation that `score_job.py`'s Phase 7.2 fix actually fires on real data**) | `Report Status = SCORE_REJECTED_NOT_QUALIFIED` for all 3 |
| Legacy `REJECTED` | Not produced by this run (this temp DB has no legacy rows) — Phase 7.2's offline backward-compat tests already cover this exact case with synthetic data |
| `EMPLOYER_REJECTED` | Not produced (no application workflow exists to write it) — confirmed absent, as expected |
| Application-lifecycle statuses seen | `FOUND`, `NOT_QUALIFIED`, `READY_FOR_APPROVAL` only — **no `APPLIED`/employer-rejection value anywhere**, confirming zero application activity occurred |

**No employer rejection was invented or implied anywhere in this
run's output.**

---

## STEP 7 — Failure Safety (reconfirmed offline, no network)

`scripts/test_daily_scheduler_offline.py` re-run in full: **all 15
checks pass**, including the atomic-write safety test (a forced
`Workbook.save()` failure leaves a previous successful workbook
byte-identical, with no leftover temp file). The real daily workbook
generated in Step 3/4 was never intentionally corrupted or touched by
this reconfirmation — it is a separate, synthetic in-memory test.

---

## STEP 8 — Production DB Integrity

| | Before (Step 1) | After (this step) |
|---|---|---|
| SHA-256 | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Size | 114688 | 114688 |
| `candidates` | 1 | 1 |
| `candidate_search_profile` | 1 | 1 |
| `jobs` | 9 | 9 |
| `candidate_job_matches` | 0 | 0 |
| `search_runs` | 0 | 0 |
| `search_queue` | 0 | 0 |

**Byte-identical. Production DB was never opened by this task's live
validation.**

---

## STEP 9 — Final Tests

```
Full standalone test suite (48 files, .venv): PASS=48 FAIL=0
python3 -m py_compile across all scripts/*.py: PY_COMPILE_ALL_OK
JSON validation (run_summary.json + this report's .json): valid
XLSX structural validation: all 9 required sheets present, exact match
Source status validation: NAUKRI=ENABLED, HIRIST=NOT_ENABLED, LINKEDIN=NOT_ENABLED, default_sources=['NAUKRI']
```

**launchd**: not installed (`~/Library/LaunchAgents/com.sarojjobos.dailysearch.plist`
does not exist), not loaded (`launchctl list` shows nothing matching),
not started.

---

## Files Changed

| File | Change |
|---|---|
| `scripts/run_daily_search.sh` | Modified — one-line diagnostic-only `JOBOS_DAILY_DB_OVERRIDE` mechanism (defaults to the real production path unchanged); a dry-run side-effect bug (override banner writing to the scheduler log even during `--dry-run`) found and fixed |
| `data/daily/2026-09-20/` | New — real, git-ignored, generated artifacts from this validation (`job_search_report.xlsx`, `run_summary.json`, `worker.log`) — derived from the temp DB's data, not production |
| `logs/daily_search_scheduler.log` | New — scheduler-level log line for this run (git-ignored) |
| `data/reports/phase7_3_controlled_daily_run.md` / `.json` | New — this report |

**Not modified:** `scripts/run_search_worker.py`, `scripts/search_worker.py`,
`scripts/naukri_adapter.py`, `scripts/naukri_fetcher.py`,
`scripts/naukri_fetch_bridge.js`, `scripts/naukri_parser.py`,
`scripts/generate_run_report.py`, `scripts/score_job.py`,
`scripts/candidate_profile.py`, `scripts/search_submission.py`,
`launchd/com.sarojjobos.dailysearch.plist`, `config/profile.json`,
`data/applications/jobos.db` (content or schema), any Hirist/LinkedIn
file.

## Summary

**"Real daily Naukri search → actionable Excel report" is proven.**
The exact chain Phase 7.2 built — wrapper, worker, real `NaukriAdapter`,
scoring, eligibility, freshness, dedup, candidate matching, and the
9-sheet workbook + `run_summary.json` — was exercised once, live,
against real Naukri data, using the real production code paths, with
only the database target diverted to a disposable temp DB via the
smallest possible one-line, default-safe mechanism. Every artifact
opened correctly, every sheet was present, every number reconciled,
every Phase 7.1 `APPLY_TODAY` gate held, and the Phase 7.2 status-
vocabulary fix was confirmed working on real, live-scored data for the
first time. One honest process note was surfaced: this task's "one
query" made 22 total HTTP requests (1 health-check + 1 search page +
20 detail-page fetches), which is the adapter's own pre-existing,
unmodified, expected behavior for a single logical query — not a new
deviation, but more precisely counted here than in any prior report.

**The scheduler was not installed, loaded, or started.** Production DB
remained byte-identical throughout.

**Stopping here, per instruction. Not proceeding to LinkedIn or
Hirist.**
