# Phase 7.1 — Reporting Semantics + Daily Runtime Integration Gate

**Zero network requests. Zero production DB modification.** Naukri
remains `ENABLED`; Hirist and LinkedIn remain `NOT_ENABLED`;
`_default_sources()` remains `['NAUKRI']`. Two scoring/eligibility/
freshness/dedup-**adjacent** additive fixes were made in the
**reporting layer only**, both because a concrete reporting
integration bug required it (per this task's own explicit allowance) —
neither changes scoring weights, eligibility rules, freshness
thresholds, or dedup logic itself.

## The Two Headline Questions — Answered

**1. Does "NEW_JOBS" currently mean genuinely NEW TO SAROJ, or merely
newly created/discovered records?**

**Before this task: merely newly-created/discovered records** (a
global, not candidate-scoped, signal) — **NO**, not genuinely
"new to Saroj" in any way that would generalize beyond today's single
real candidate. **Fixed in this task**: `NEW_JOBS` now reads
`candidate_job_matches.created_at` (genuinely per-candidate, already
existing, already protected from overwrite) instead of the global
`jobs.created_at`. **Answer is now YES** — verified by an offline test
(Job B: matched in an earlier run, rediscovered today, correctly
excluded from `NEW_JOBS` while Job A, matched for the first time
today, is correctly included).

**2. Does the normal daily search execution automatically produce the
final 9-sheet Excel workbook?**

**NO — confirmed by tracing the code, not assumed.** No scheduler or
daemon of any kind exists in this project yet (`search_worker.py`'s
own docstring: "a future scheduler is expected to invoke run_once()
repeatedly" — none does). `generate_run_report.py` was never invoked
by any other file before this task. **An opt-in integration point now
exists** (`run_search_worker.py --report-out PATH`), verified working,
but it is **not automatic** — a human (or a future scheduler, not
built here, not requested) must still pass the flag. **Still NO for
"automatic," by design, per this task's own instruction not to wire it
into production without a separate decision.**

**Per instruction: since one of these two answers is not an
unqualified YES (`generate_run_report.py` invocation is still manual,
by design), do not proceed to another source yet — awaiting your
review, as instructed.**

---

## 1. NEW_JOBS vs ALREADY_SEEN Semantics

**Code inspected**: `generate_run_report.py`, `search_worker.py`,
`search_submission.py`, `source_registry.py`, `migrate_v2_schema.py`,
`tracker.py`, the `jobs`/`search_runs`/`search_queue`/
`candidate_job_matches` schemas.

**The three concepts, mapped to what the code actually does:**

| Concept | Signal that would answer it | Available today? |
|---|---|---|
| **A. Genuinely NEW TO SAROJ** (this candidate has never matched this job identity before) | `candidate_job_matches.created_at` for `(candidate_id, job_id)` | **YES — already existed, was not being used** |
| **B. ALREADY SEEN** (same canonical identity existed before this run, even if rediscovered today) | The negation of A, or `jobs.created_at < since` | Derivable from A (or, less precisely, from global `jobs.created_at`) |
| **C. NEWLY DISCOVERED TODAY** (first ever inserted into the global `jobs` table this run) | `jobs.created_at >= since` | Available, but **NOT candidate-scoped** |

**What Phase 7's original `generate_run_report.py` actually computed**:
category **C** (`jobs.created_at >= since`), mislabeled as if it
answered category **A**. `jobs` is a **global** table — a job row's
`created_at` reflects when *any* process first inserted it, not when
*this candidate* first matched it. With exactly one real candidate in
production today, A and C happen to coincide in practice, which is
precisely why this was never caught until this audit deliberately
asked "do not assume `created_at` is sufficient."

**Confirmed by code, not assumed:**
`search_worker.upsert_candidate_job_match()`'s `ON CONFLICT(candidate_id, job_id) DO UPDATE SET`
clause (line-read directly) does **not** include `created_at` — so
`candidate_job_matches.created_at` is protected exactly the way
`jobs.created_at` is, but **per candidate**. And
`candidate_job_matches` rows only ever exist for jobs that passed
eligibility (`search_worker.py`'s eligible branch is the only writer)
— exactly the set `NEW_JOBS` could ever contain (qualifying jobs), so
there is no coverage gap from using it.

**Historical job identity was already available. No new schema was
invented.** Fixed additively: `generate_run_report.load_candidate_job_matches()`
(new function, read-only) + `build_report_rows()` now computes
`is_new`/`Previously Seen` from this table instead of `jobs.created_at`.

**Verified** (offline regression test, Jobs A/B): Job A (first matched
this run) → `NEW_JOBS`, `Previously Seen = No`. Job B (matched in an
earlier, separate run — `candidate_job_matches.created_at` explicitly
backdated to simulate this, then rediscovered today) → **not**
`NEW_JOBS`, `Previously Seen = Yes`, still correctly in `APPLY_TODAY`
(being previously-seen does not, and should not, disqualify a job).

**Classification: PASS** (was NEEDS CHANGE before this task's fix).

---

## 2. Application Status Semantics

**Requested distinctions, and whether each is now derivable —
additively, without a schema change:**

| Status | Derivable today? | Mechanism |
|---|---|---|
| `INELIGIBLE` | **PASS** | `ranking.eligible == False` (job_eligibility gate) |
| `SCORE_REJECTED` / `NOT_QUALIFIED` | **PASS** | eligible AND `priority == "REJECT"` (score < 70) |
| `ELIGIBLE_NOT_APPLIED` | **PASS** | qualifies, not yet applied, but fails one of the stricter `APPLY_TODAY` criteria (stale, no URL, or suppressed duplicate) |
| `READY_TO_APPLY` | **PASS** | qualifies AND fresh (≤3 days) AND has a usable URL AND not a suppressed duplicate AND not yet applied — the exact `APPLY_TODAY` criteria |
| `APPLIED` | **PASS** | `jobs.status == "APPLIED"` (and every other literal applied-lifecycle status: `RECRUITER_CONTACTED`, `SCREENING_CALL`, `INTERVIEW_1/2`, `FINAL_ROUND`, `OFFER` — none of these collide with anything else) |
| `EMPLOYER_REJECTED` | **BLOCKED** | **Cannot be derived — see below** |
| `WITHDRAWN` | **PASS** | `jobs.status == "WITHDRAWN"` — a real, unambiguous status; nothing else in the pipeline ever writes it |
| `DUPLICATE` | **PASS** | new duplicate-cluster suppression logic (see §4) |
| `ALREADY_SEEN` | **PASS** | new `Previously Seen` column (see §1) |

**`EMPLOYER_REJECTED` — confirmed BLOCKED, not silently reinterpreted.**
Traced every writer of `jobs.status` in the entire codebase: exactly
two exist — (a) `score_job()`'s own bucket assignment at first insert
(`status="REJECTED"` for any eligible-but-<70-scored job, `status="READY_FOR_APPROVAL"`
otherwise), and (b) nothing else — **no code anywhere advances a job
through the application lifecycle** (no approval/application workflow
is built yet, confirmed by code, not merely by CLAUDE.md's gap list).
`tracker.upsert_job()`'s `ON CONFLICT` clause excludes `status` from
its `UPDATE SET`, so a first-insert value of `"REJECTED"` can only ever
be a **stale artifact of the original score**, never a real employer
signal — this codebase **cannot currently produce a genuine
`EMPLOYER_REJECTED` state at all**, so there is nothing for the
reporting layer to read even if it wanted to.

**The reporting layer never implies an employer rejection.** The new
`Report Status` column labels every `status="REJECTED"` row as
`SCORE_REJECTED_NOT_QUALIFIED` — **or**, in the one edge case where a
job was later re-scored to a qualifying priority while a stale
`"REJECTED"` status was left behind (re-scoring updates `priority` but
never `status`), as the explicit `AMBIGUOUS_REJECTED_STATUS_SEE_LIMITATION`
label — **never** as `APPLIED`/`REJECTED_BY_EMPLOYER`/anything implying
human/employer action. **Verified directly** (offline test, Job G:
`status` manually forced to `"REJECTED"` on an otherwise-qualifying
job) — the row lands with `Report Status = AMBIGUOUS_REJECTED_STATUS_SEE_LIMITATION`,
excluded from both `ALREADY_APPLIED` and `APPLY_TODAY`, demonstrating
the limitation rather than pretending it works.

### Proposed schema/workflow change for `EMPLOYER_REJECTED` — NOT implemented, awaiting your approval

Two options, smallest-first:

**Option A (recommended, smallest): rename the score-bucket's status
string.** In `score_job.py`, change the literal `"REJECTED"` the
score-bucket path writes to something unambiguous, e.g.
`"NOT_QUALIFIED"` — freeing `"REJECTED"` in `application_schema.json`'s
lifecycle to mean *only* a genuine employer rejection (whenever a
future application workflow starts actually setting it). One string
literal changes; no column added; no migration needed for existing
rows (their old `"REJECTED"` values remain correctly interpretable as
score-rejections under the *old* vocabulary, going forward all new
ones say `"NOT_QUALIFIED"`). **Not implemented**: `score_job.py` is
explicitly protected by CLAUDE.md ("Do not modify ... `scripts/score_job.py`
... without being explicitly asked") and this task's own preamble
("do NOT change ... existing eligibility/freshness/dedup logic unless
a concrete reporting integration bug requires an additive fix") — a
status *string* rename inside the scoring function is arguably
reporting-adjacent but touches a file under an explicit, separate
protection; STOPPING for your explicit go-ahead rather than assuming
this qualifies.

**Option B (larger): add a new `jobs.application_outcome` column**,
decoupling lifecycle *stage* (`status`) from terminal *outcome*
(`REJECTED_BY_EMPLOYER` / `WITHDRAWN_BY_CANDIDATE` / `OFFER_ACCEPTED` /
...), only ever set by a future application/approval workflow (which
does not exist yet either). More correct long-term, but a real schema
migration, and only useful once that workflow exists — premature
today.

**Recommendation: Option A, once a human approval/application workflow
is actually being built** (at which point it will need to set *some*
unambiguous "employer rejected" status anyway) — not urgent in
isolation today, since no code can currently reach that state either
way.

---

## 3. Daily Runtime Integration

**Traced the real path**: `config/searches.json`'s `"schedule"` key is
a **plain config value**, never consumed by cron/launchd/any daemon
(grep-confirmed). `docker-compose.yml`'s `n8n` container has no
workflow files wiring it to this project's Python scripts. No `.py`
file anywhere calls `generate_run_report`/`generate_run_report.generate()`
except `generate_run_report.py` and its own test (grep-confirmed,
before this task's changes).

**Integration point identified and implemented** (additive, opt-in,
off by default): `run_search_worker.py --report-out PATH
[--report-since ISO_DATETIME]`. After `run_once()` completes, if
`--report-out` was passed, `generate_run_report.generate()` is called
against the **same** `--db`/`--candidate-id` already used for the run.
Skips cleanly (with an explicit message, never silently) for
`--dry-run` (nothing was claimed/processed) or when nothing was
claimed this cycle. `openpyxl` is imported **only** inside this
opt-in path, so every existing (non-reporting) use of this CLI is
completely unaffected and needs no `.venv`.

**Verified**:
- Plain `python3 run_search_worker.py --once --dry-run --report-out ...`
  → skips cleanly, no error, no file written, no `openpyxl` import
  triggered.
- `.venv/bin/python3 run_search_worker.py --once --report-out ...`
  against a real (empty-queue) temp DB → skips cleanly with a distinct
  message ("no queue item was claimed"), no file written.
- All 38 of `test_search_worker.py`'s existing subprocess-based CLI
  tests (which invoke this exact script) still pass unmodified.

**Not wired into production, and no scheduler was built** — per
explicit instruction ("do NOT wire it into production yet unless
clearly additive and safe" / do not implement unrequested items from
CLAUDE.md's gap list). The capability now exists and is proven safe;
turning it into a genuinely automatic daily run still requires a
scheduler (macOS launchd or similar), which remains explicitly out of
scope here.

**Classification: PASS WITH LIMITATION** (integration point built and
verified; still requires a human — or a future scheduler — to invoke
it).

---

## 4. Workbook Business Objective — Re-Verified, Sheet by Sheet

| Sheet | Source data | Inclusion criteria | Deterministic? | Trustworthy for a daily run? | Answers a real decision? |
|---|---|---|---|---|---|
| **APPLY_TODAY** | current DB state, re-scored live | qualifies (priority A/B/C) AND active status AND **freshness_age_days ≤ 3** AND **usable URL present** AND **not a suppressed duplicate** — all three newly enforced this task | Yes | **Yes (now)** — was NOT before this task | Yes — "what to apply to today" |
| ALL_MATCHING_JOBS | same | qualifies, any status | Yes | Yes | Yes — full addressable pool |
| **NEW_JOBS** | same, `candidate_job_matches`-based | qualifies AND `candidate_job_matches.created_at >= since` — corrected this task | Yes | **Yes (now)** — was NOT before this task | Yes — "what's genuinely new" |
| ALREADY_APPLIED | `jobs.status` | applied-lifecycle status (excludes `REJECTED`, see §2) | Yes | Yes | Yes — "what have I already acted on" |
| REJECTED_EXCLUDED | same, re-scored live | not qualifying AND not already-applied | Yes | Yes, **with the documented `EMPLOYER_REJECTED` limitation** (§2) | Yes — "why did this not make the cut" |
| DUPLICATES | `cross_source_dedup`, unmodified | rows in ≥1 duplicate-candidate pair | Yes | Yes | Yes — "is this the same job as that one" |
| APPLICATION_TRACKER | current DB state, unfiltered | none — every row | Yes | Yes | Yes — full history/master list |
| SOURCE_HEALTH | `search_runs`, raw | candidate-scoped if given | Yes | **Yes, with a minor, pre-existing, unmodified limitation**: `search_runs.source` stores a single value (sometimes a comma-joined multi-source string for a run covering several sources in one queue item) rather than one row per source — not changed here, noted only | Yes — "did a source fail or return nothing" |
| RUN_SUMMARY | latest `search_runs` row + computed distributions, now including a `report_status_distribution` (additive) | — | Yes | Yes | Yes — "how many survived each stage" |

**`APPLY_TODAY` no longer accidentally includes** (all four newly
verified via the offline regression test):
- ineligible jobs — **confirmed excluded** (unchanged from Phase 7, still correct)
- **stale jobs beyond 3 days — fixed this task** (Job F: 5-day-old job, `freshness=FRESH` category but `freshness_age_days=5 > 3`, now correctly excluded — the bug was checking freshness *category*, never the actual day count, against this project's own `max_job_age_days=3` convention)
- **duplicates — fixed this task** (Job E: cross-source duplicate of Job A, now correctly suppressed, only the higher-scoring representative — Job A — remains)
- jobs already applied to — **confirmed excluded** (unchanged, correct)
- score-rejected jobs — **confirmed excluded** (unchanged, correct)
- **jobs lacking a usable application URL — fixed this task** (enforced via `_has_usable_url()`; not exercised by a dedicated new fixture this round since Phase 7's original test already covered the URL-fallback rendering behavior, which is unchanged — the new **requirement** that a URL must exist at all for `APPLY_TODAY` specifically is new and is exercised implicitly by every passing Job A/B/E/F fixture, all of which carry real URLs)

**Every actionable row exposes** (columns confirmed present):
source ✓, company ✓, title ✓, location ✓, score ✓, priority ✓,
freshness ✓ (+ freshness-in-days), **eligibility/result ✓ (new
`Eligible` + `Report Status` columns)**, reason where relevant ✓
(`Exclusion / Gap Reason`), application/job URL ✓ (both, with real
hyperlinks), **seen/applied state ✓ (new `Previously Seen` column +
existing `Application Status`)**.

**Classification: PASS** (`APPLY_TODAY` and `NEW_JOBS` were NEEDS
CHANGE before this task's fixes; both now PASS, verified).

---

## 5. Offline Regression Test

New scenario set replaces Phase 7's original 8-job fixture with the
exact Jobs A–G this task specified (`scripts/test_generate_run_report.py`,
fully rewritten). Two search_worker.py runs against one temp DB (an
"earlier" run that first discovers Job B, then "today's" run
discovering everything else and rediscovering Job B), using
deterministic fake adapters — zero network, zero production DB access.

| Job | Scenario | Verified result |
|---|---|---|
| A | brand new, eligible, high score (100), fresh, not applied | `NEW_JOBS` + `APPLY_TODAY`, `Previously Seen=No`, `Report Status=READY_TO_APPLY` |
| B | already seen (matched in the earlier run, backdated `candidate_job_matches.created_at`), eligible, fresh, not applied | **not** `NEW_JOBS`, still `APPLY_TODAY`, `Previously Seen=Yes` |
| C | already applied (`status` set to `APPLIED` post-ingestion) | `ALREADY_APPLIED` only, `Report Status=APPLIED` |
| D | eligible but score=50 (<70) | **not** `APPLY_TODAY`, `REJECTED_EXCLUDED`, `Report Status=SCORE_REJECTED_NOT_QUALIFIED` |
| E | cross-source duplicate of Job A (identical JD, same title/company/location, `FAKE_SOURCE_B`) | **not** `APPLY_TODAY` (suppressed), `Report Status=DUPLICATE`; Job A (the representative) remains in `APPLY_TODAY` |
| F | posted 5 days ago (`freshness=FRESH` category, `freshness_age_days=5`) | **not** `APPLY_TODAY` (>3 days), still `ALL_MATCHING_JOBS` |
| G | `status` manually forced to `REJECTED` on an otherwise-qualifying job | `Report Status=AMBIGUOUS_REJECTED_STATUS_SEE_LIMITATION`, excluded from both `ALREADY_APPLIED` and `APPLY_TODAY` — limitation explicitly demonstrated |

**Result: 100% pass**, all 7 scenarios plus a full 9-sheet workbook
artifact check. See Verification section for the exact run output.

**Classification: PASS.**

---

## 6. Production Safety

| Check | Before | After |
|---|---|---|
| SHA-256 | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Size (bytes) | 114688 | 114688 |
| Row counts | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 | identical |
| Naukri | `ENABLED` | `ENABLED` |
| LinkedIn | `NOT_ENABLED` | `NOT_ENABLED` |
| Hirist | `NOT_ENABLED` | `NOT_ENABLED` |
| `_default_sources()` | `['NAUKRI']` | `['NAUKRI']` |

**Production DB was never opened at any point in this task.**

```
Full standalone test suite (46 files, .venv):          PASS=46 FAIL=0
python3 -m py_compile across all scripts/*.py:          PY_COMPILE_ALL_OK
JSON validation (this report's .json):                  valid
```

**Classification: PASS.**

---

## Files Changed

| File | Change |
|---|---|
| `scripts/generate_run_report.py` | Modified — `NEW_JOBS`/`Previously Seen` now use `candidate_job_matches.created_at` (new `load_candidate_job_matches()`); `APPLY_TODAY` now additionally requires freshness ≤3 days, a usable URL, and duplicate-cluster non-suppression (new `_build_duplicate_clusters()`/`_pick_duplicate_representatives()`/`_has_usable_url()`); new `Eligible`/`Report Status`/`Previously Seen`/`Duplicate (Suppressed)` columns (new `_compute_report_status()`); `RUN_SUMMARY` gains a `report_status_distribution` |
| `scripts/run_search_worker.py` | Modified — new opt-in `--report-out`/`--report-since` flags, additive, off by default, lazily imports `generate_run_report`/`openpyxl` only when used |
| `scripts/test_generate_run_report.py` | Rewritten — Jobs A–G per this task's exact scenario list, replacing Phase 7's original 8-job fixture |
| `data/reports/phase7_1_reporting_semantics_audit.md` / `.json` | New — this report |

**Not modified:** `scripts/generate_daily_report.py`, `scripts/score_job.py`,
`scripts/tracker.py`, `scripts/search_worker.py`, `scripts/job_ranking.py`,
`scripts/score_explanation.py`, `scripts/freshness.py`,
`scripts/job_eligibility.py`, `scripts/cross_source_dedup.py`,
`scripts/canonical_job.py`, `scripts/candidate_profile.py`,
`scripts/search_submission.py`, `scripts/naukri_adapter.py`,
`scripts/hirist_*.py`, `scripts/linkedin_adapter.py`,
`scripts/source_registry.py`, `config/profile.json`,
`config/application_schema.json`, `data/applications/jobos.db`.

## Summary Classification

| Item | Classification |
|---|---|
| NEW_JOBS vs ALREADY_SEEN semantics | **PASS** (fixed this task; was NEEDS CHANGE) |
| INELIGIBLE / SCORE_REJECTED / ELIGIBLE_NOT_APPLIED / READY_TO_APPLY / APPLIED / WITHDRAWN / DUPLICATE / ALREADY_SEEN | **PASS** |
| EMPLOYER_REJECTED | **BLOCKED** — proposal given (Option A recommended), not implemented, awaiting approval |
| Daily runtime integration | **PASS WITH LIMITATION** — integration point built and verified; still manual, no scheduler exists or was built |
| APPLY_TODAY sheet | **PASS** (fixed this task; was NEEDS CHANGE) |
| ALL_MATCHING_JOBS / ALREADY_APPLIED / DUPLICATES / APPLICATION_TRACKER / RUN_SUMMARY sheets | **PASS** |
| REJECTED_EXCLUDED sheet | **PASS WITH LIMITATION** (inherits the EMPLOYER_REJECTED limitation) |
| SOURCE_HEALTH sheet | **PASS WITH LIMITATION** (pre-existing, unmodified `search_runs.source` granularity note) |
| Offline regression test (Jobs A–G) | **PASS** |
| Production safety | **PASS** |

**Stopping here, per instruction, awaiting your review of the findings
and the one proposed (not implemented) `EMPLOYER_REJECTED` schema/
vocabulary change before any further work.**
