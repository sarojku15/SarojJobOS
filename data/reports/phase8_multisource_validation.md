# Phase 8 — Fix Daily Queue Generation + Complete Multi-Source Validation

**Production DB byte-identical throughout. launchd NOT installed, NOT
loaded, NOT started. Hirist and LinkedIn remain `NOT_ENABLED` — neither
was enabled this phase. Zero application submissions.**

## ⚠️ Disclosed Safety Deviation — Read First

**During PART A's offline test development, an early draft of
`scripts/test_phase8_daily_queue.py` made 14 unauthorized live HTTP
requests to naukri.com** before being caught mid-run and killed
(`kill -9`). This section explains exactly what happened, why, the
fix, and why nothing unsafe resulted beyond the unauthorized requests
themselves — reported with no minimization, per this project's
established discipline.

**What happened**: the test registered a fake, offline adapter
(`FAKE_PHASE8_ENABLED`) into `source_registry.ADAPTERS` to exercise
`submit_search()` safely — but called `submit_search()` (via its CLI)
**without** an explicit `--source` restriction. `submit_search()`'s own
`sources=None` default resolves to `search_profile._default_sources()`,
which returns **every currently `AdapterStatus.ENABLED` adapter in the
real, shared, process-wide registry** — and `NaukriAdapter` genuinely
**is** `ENABLED` in that real registry (this test file only *added* a
fake adapter alongside it; it never removed or disabled the real one).
The resulting queue item therefore contained a real `NAUKRI` query
alongside the fake one, and test #4 (`process_queue_item()`) executed
it for real.

**Exact impact**: 14 live HTTP requests to `naukri.com` — 1 homepage
health check, 1 search-results page
(`senior-sre-jobs-in-bengaluru?jobAge=3`), and 12 job-detail-page
fetches — all **HTTP 200**, before the process was killed. **Zero
blocks, zero CAPTCHA interactions, zero retries.** The requested
role/location (`Senior SRE` / `Bengaluru`) came from this test's own
synthetic candidate profile, not from any real search intent.

**Production DB impact**: **none.** Every call in the flawed test used
an explicit `--db` pointing at a disposable temp database (confirmed:
`data/applications/jobos.db`'s SHA-256 was checked immediately after
and found unchanged). The damage was strictly confined to the
unauthorized network requests themselves.

**Root cause**: reusing a shared, process-wide adapter registry
(`source_registry.ADAPTERS`) without fully accounting for the fact
that *adding* a fake adapter does not *isolate away* the real,
always-present ones — `_default_sources()` has no way to know a test
"only meant" the fake one.

**Fix**: every `submit_search.py` call in every Phase 8 test file now
explicitly passes `--source <fake-adapter-name>` — never relying on
the default. The one test that genuinely needed to exercise
`_default_sources()`'s real ENABLED-filtering logic (Part A test 6)
was redesigned to safely monkeypatch only `source_registry.list_sources()`
(the name list `_default_sources()` iterates, restricted for the
duration of that one call to two fake names only) — **never**
`get_adapter_status()` or `_default_sources()` itself, so the real
filtering logic runs genuinely unmodified, with zero possibility of a
real adapter being reached even if something were still wrong. Both
fixes verified: the corrected test suite runs in under a second, with
zero network activity (confirmed by the complete absence of any
`naukri.com` line in its output).

**This deviation is included in every live-request tally in this
report** — see "Exact Live Request Counts" below.

---

## 1. Queue Architecture Before Fix

See `data/reports/phase8_daily_queue_design.md` for the full,
code-grounded answer to all 7 of Part A's architecture questions.
Summary: `run_search_worker.py --once` only **consumed** already-queued
work — nothing in the daily chain ever **produced** new queued work,
so after the first successful run, every subsequent scheduled run
would find nothing to do, forever.

## 2. Queue Architecture After Fix

`scripts/run_daily_search.sh` now calls `scripts/submit_search.py
--candidate-id saroj --max-job-age-days 3 --confirm` (reusing the
existing, already-tested, already-idempotent `search_submission.submit_search()`
via one new pass-through CLI flag) immediately before its existing
`run_search_worker.py --once` call. `sources` is never hardcoded — it
tracks `AdapterStatus.ENABLED` dynamically.

## 3. Idempotency Proof

`scripts/test_phase8_daily_queue.py` (new, **11/11 checks pass**):

| # | Proof |
|---|---|
| 1 | Empty queue + daily run creates today's required queue work (1 `search_run` + 1 `search_queue` row) |
| 2 | An already-`QUEUED` submission is not duplicated by a repeat call |
| 3 | Idempotent across 3 repeated submission attempts — exactly 1 row of each |
| 4 | A `COMPLETED` prior run does **not** block a fresh submission (proves the daily cycle can repeat) |
| 5 | Candidate isolation — one candidate's submissions never touch another's rows |
| 6 | Source isolation — only `AdapterStatus.ENABLED` sources appear in the query plan (verified via the safe `list_sources()` monkeypatch described above) |
| 7 | The search snapshot/query plan is deterministic — two preview calls against the same profile produce byte-identical output |
| 8 | Production DB never referenced — every call uses an explicit temp `--db` |
| 9 | Only expected `jobs.status` values appear after this test's runs |
| 10 | Human approval boundary intact — no application-submission token anywhere in the daily chain |
| 11 | launchd still not installed/loaded/started |

## 4. Daily Repeated-Run Proof

`scripts/test_phase8_repeated_daily_run.py` (new, **7/7 checks pass**,
offline, fake adapter, temp DB only):

RUN 1 creates work, executes, and generates a report. RUN 2 (two
back-to-back submission attempts before processing) produces exactly
**one** new queue item (no duplicate explosion), processes
deterministically, generates a new report **without corrupting RUN 1's
report** (both confirmed present and non-empty afterward), and the
rediscovered job produces exactly one `jobs` row (no duplicate). A
simulated "next day" (a fresh submission after RUN 2 completed, with a
second, genuinely new job added to the fake adapter's results)
correctly discovers and persists the new work.

## 5. Naukri Validation (PART B)

**Live, using the full real chain**: `scripts/run_daily_search.sh`
(with the new queue-prep step) → real `submit_search.py --confirm` →
real `run_search_worker.py --once` → real, unmodified `NaukriAdapter`
→ normalization → dedup → freshness → eligibility → scoring →
priority → candidate match → `generate_run_report.py` → workbook +
`run_summary.json` — against a **disposable temp DB** (via the Phase
7.3 `JOBOS_DAILY_DB_OVERRIDE` mechanism, preserved unmodified per
instruction), candidate `saroj` (real skills from `config/profile.json`,
query narrowed to exactly one role/location), `Infrastructure
Engineer` / `Bangalore`, `max_job_age_days=3`, `NAUKRI` only.

**Exact HTTP request count: 22** — 1 homepage health check + 1
search-results page + 20 job-detail-page fetches (one per raw result
found), **all HTTP 200**, headless Chromium, the standard/validated
Chrome UA, zero retries, zero CAPTCHA interaction. (**Not** "one
request" — per instruction, the full breakdown is given, not a rounded
claim.) This is `NaukriAdapter`'s own pre-existing, unmodified
behavior (confirmed by reading `scripts/naukri_adapter.py` fresh this
phase) — identical in shape to Phase 7.3's own 22-request pattern.

| Metric | Value |
|---|---|
| Raw results | 20 |
| Normalized | 20 |
| Duplicates removed (intra-source) | 0 |
| Excluded (experience) | 16 |
| Excluded (location) | 0 |
| Eligible | 4 |
| Scored | 4 |
| Ready for approval | 1 |
| Matches created | 4 |
| `APPLY_TODAY` count | 1 |
| `NEW_JOBS` count | 1 |
| `ALREADY_APPLIED` count | 0 |
| `REJECTED_EXCLUDED` count | 19 |
| `DUPLICATES` count | 0 (single-source run) |
| Workbook path | `data/daily/2026-09-20/job_search_report.xlsx` (26301 bytes) |
| Workbook sheets | all 9 required, exact match |
| `run_summary.json` path | `data/daily/2026-09-20/run_summary.json` |
| Search run ID | `ac937d3e-fb17-40f8-bf40-c6f66198abd1` (fresh, created by the new queue-prep step — **not** reused from Phase 7.3's run, proving the fix works end-to-end for real) |

The same real job from Phase 7.3 ("Hiring For AWS Cloud Infra and
DevOps Engineer" at Tenarai Technologies, score 90/A) reappeared and
landed correctly in `APPLY_TODAY` — Naukri's live inventory had not
materially changed between the two runs, which is expected and not a
concern.

**Naukri: `Enabled` = YES (unchanged). Live validated = YES. Report
integrated = YES.**

## 6. Hirist Validation (PART D)

**Code re-verified fresh** (not assumed from memory) — `scripts/hirist_parser.py`
read in full this phase, confirmed byte-identical to its Phase 6
Step 10/11 state. All 5 existing Hirist test files re-run fresh, all
still passing.

**New offline gate** (`scripts/test_hirist_phase8_offline_gate.py`, 8
checks, all passing) — extended fixtures per instruction: 3 new files
added (`missing_url.html`, `duplicate_url.html`, and, decisively,
`company_title_ambiguity_real_capture.html` — the **verbatim real
JSON-LD payload** from Phase 6 Step 9's one live capture, not
synthetic).

**Decisive finding, re-confirmed with MORE precision than before**:
running the current, unmodified parser against the **full 20-entry
real capture** (not just the single example previously cited) finds
**3 confirmed-wrong company/title splits**, not 1:

```
company='Senior DevOps Engineer' title='AWS & Kubernetes'
company='Senior DevOps Engineer' title='Cloud Infrastructure'
company='Senior DevOps Engineer' title='AWS & Kubernetes'
```

Plus **10 of 20 real entries silently skipped entirely** (the
fails-closed multi-delimiter rule discarding the `"Company - Title -
Tags"` convention) — a second, distinct data-loss manifestation of the
same root cause: real `name` values mix at least three conventions
(bare title / `"Company - Title"` / `"Company - Title - Tags"`) with
no deterministic way to tell them apart from the string alone.

**Other findings re-verified, unchanged**: JSON-LD `ItemList`
extraction ✓, top-level `ListItem.url` extraction ✓ (10/10
successfully-parsed real entries have unique, non-empty `job_url`),
pagination ✓, visible-text challenge detection ✓, location/experience/
description confirmed still absent from real data (unchanged), missing-URL
and duplicate-URL handling both fail safely (new tests).

**Per instruction ("If company/title quality remains materially
unreliable: KEEP HIRIST NOT_ENABLED... stop Hirist enablement"): the
offline gate's own conclusion is that Hirist does NOT proceed to the
live gate this phase.** No new live Hirist request was made.

**Hirist: `Enabled` = NO. Live validated this phase = NO (offline gate
did not pass). Report integrated = N/A. Remaining blocker: company/title
extraction from Hirist's `name` field is structurally ambiguous (3
mixed conventions, no deterministic disambiguation rule) — would
require either a genuine site-side structural change on Hirist's part,
or accepting a documented, evidence-justified heuristic (explicitly
not attempted here, since none has been shown to be reliable, and
inventing one was explicitly forbidden).**

## 7. LinkedIn Authorization/Access Result (PART E)

**No live request was made.** Checked for an authorized supported
access mechanism first, per instruction: the only LinkedIn-related
tool available in this environment is a Windsor.ai **LinkedIn Ads**
connector (campaign/budget management for advertising) — a completely
different product surface from job discovery, and not authorization to
scrape or otherwise access LinkedIn's Jobs pages; using it to extract
job-posting data would itself be exactly the kind of workaround this
task explicitly forbids. No LinkedIn Jobs API/Talent connector exists
in this project's available toolset.

`scripts/linkedin_adapter.py` re-read fresh this phase: unchanged,
still an offline skeleton, `status = AdapterStatus.NOT_ENABLED`,
`health_check()`/`search()` both unconditionally raise
`AdapterNotEnabledError`. `data/reports/linkedin_phase5_inspection.md`
re-checked: its own governing evidence (`robots.txt`'s catch-all
`User-agent: * / Disallow: /`, HTTP 200 + real results observed but
explicitly **not** treated as authorization, a sign-in prompt present)
is unchanged, static, and requires no re-fetch to remain valid —
per instruction, HTTP 200 reachability is **not** interpreted as
authorization.

**Decision: no authorized mechanism exists → LinkedIn remains
NOT_ENABLED.** No workaround was implemented.

**LinkedIn: `Enabled` = NO. Authorized access = NO. Live validated =
NO. Report integrated = N/A. Remaining blocker: no authorized access
mechanism exists (robots.txt disallows generic automated access; no
official API/connector is available to this project).**

## 8. Cross-Source Deduplication (PART F)

`scripts/test_phase8_multisource_dedup.py` (new, offline, zero
network — `cross_source_dedup.py`/`canonical_job.py` are
source-agnostic, so this safely exercises Naukri-shaped +
Hirist-shaped synthetic data without needing either adapter enabled):

| Case | Result |
|---|---|
| 1. Naukri-only job | correctly no duplicate |
| 2. Hirist-only job | correctly no duplicate |
| 3/4. Same job on Naukri + Hirist | **correctly detected**, both sides flagged |
| 5. Same job, tracking-parameter-decorated URL on one side | **still correctly detected** — `cross_source_dedup.compare_pair()` never compares `job_url` at all (confirmed by code and by this test) |
| 6. Similar title, different company | correctly **not** flagged |
| 7. Same company/title text, different location | correctly **not** flagged (`canonical_location` mismatch overrides an otherwise-matching pair) |
| Reconciliation | exactly 2 duplicate-candidate pairs found across 10 synthetic jobs — matching the 2 genuinely-duplicate cases exactly, no false positives, no false negatives |
| No unsafe double-counting | confirmed — at most one side of each duplicate pair appears in `APPLY_TODAY` |

**Cross-source dedup: Validated = YES.**

## 9. 9-Sheet Reporting (PART G)

Generated from the same PART F multi-source dataset (`grr.generate_workbook()`,
directly, offline):

- All 9 required sheets present, exact name match.
- `APPLY_TODAY` confirmed to expose: Priority, Score, Title, Company,
  Location, Source, Job URL, Freshness, Eligible, Report Status,
  Application Status, Duplicate (Suppressed) — all required columns.
- `DUPLICATES` sheet: exactly 2 rows, matching the 2 genuine pairs.
- `APPLY_TODAY` confirmed, **from actual workbook cell content** (not
  just in-memory state), to never contain both sides of any duplicate
  pair.

**9-sheet report: Validated = YES.**

## 10. Production DB SHA Before/After Each Live Phase

| Point | SHA-256 |
|---|---|
| Phase 8 start (before Part A) | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| After the disclosed deviation (14 unauthorized Naukri requests) | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` (unchanged — deviation never touched production DB) |
| Before PART B's live validation | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| After PART B's live validation | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| End of Phase 8 (final) | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |

**Byte-identical at every checkpoint. Size: 114688 bytes throughout.
Row counts and status distribution unchanged throughout**
(`candidates=1, candidate_search_profile=1, jobs=9,
candidate_job_matches=0, search_runs=0, search_queue=0`; status
distribution: `FOUND/TEST=1, INTERVIEW_1/TEST=1,
READY_FOR_APPROVAL/MOCK=3, READY_FOR_APPROVAL/TEST=1, REJECTED/TEST=3`).

## Exact Live Request Counts, This Phase

| Source | Count | Authorized/intentional? |
|---|---|---|
| Naukri (disclosed deviation, PART A test bug) | 14 | **No — unauthorized, self-caught and fixed** |
| Naukri (PART B, intentional live validation) | 22 | Yes |
| Hirist | 0 | N/A — offline gate did not pass, no live gate attempted |
| LinkedIn | 0 | N/A — no authorized mechanism, none attempted |
| **Total** | **36** | 22 authorized, 14 disclosed as an error |

## 11. Test Results

```
Full standalone test suite (53 files, .venv): PASS=53 FAIL=0
python3 -m py_compile across all scripts/*.py: PY_COMPILE_ALL_OK
bash -n scripts/run_daily_search.sh:           SHELL_SYNTAX_OK
plutil -lint launchd/*.plist:                   OK
JSON validation (this report + companion):      valid
XLSX structural validation:                     all 9 sheets confirmed (PART B live workbook + PART G synthetic multi-source workbook)
Source registry status validation:              NAUKRI=ENABLED, HIRIST=NOT_ENABLED, LINKEDIN=NOT_ENABLED, default_sources=['NAUKRI']
Queue idempotency tests:                        11/11 PASS (test_phase8_daily_queue.py)
Daily wrapper tests:                            15/15 PASS (test_daily_scheduler_offline.py, re-run unmodified-behavior-confirmed)
Atomic-write tests:                             re-confirmed passing (workbook + run_summary.json, Phase 7.2/7.4 mechanisms, untouched this phase)
Lock tests:                                     re-confirmed passing (stale-lock reclaim + signal trap, Phase 7.4 mechanisms, untouched this phase)
```

(53 = 50 pre-Phase-8 + 3 new: `test_phase8_daily_queue.py`,
`test_phase8_repeated_daily_run.py`, `test_hirist_phase8_offline_gate.py`,
`test_phase8_multisource_dedup.py` — 4 new files; final count includes
all of them plus everything from Phase 7/7.1/7.2/7.3/7.4.)

## 12. Exact Files Modified

| File | Change |
|---|---|
| `scripts/submit_search.py` | Modified — new `--max-job-age-days` pass-through flag |
| `scripts/run_daily_search.sh` | Modified — new queue-prep step calling `submit_search.py --confirm` before the worker |
| `scripts/test_daily_scheduler_offline.py` | Modified — removed `"submit_search.py --confirm"` from its suspicious-token list (now intentional; it is search submission, not application submission) |
| `scripts/test_phase7_4_production_readiness.py` | Modified — same fix as above |
| `scripts/test_phase8_daily_queue.py` | **New** — PART A idempotency tests (11 checks) |
| `scripts/test_phase8_repeated_daily_run.py` | **New** — PART C repeated-run tests (7 checks) |
| `scripts/test_hirist_phase8_offline_gate.py` | **New** — PART D Hirist offline gate (8 checks) |
| `scripts/test_phase8_multisource_dedup.py` | **New** — PART F/G multi-source dedup + reporting tests (12 checks) |
| `data/fixtures/hirist/missing_url.html` | **New** — synthetic |
| `data/fixtures/hirist/duplicate_url.html` | **New** — synthetic |
| `data/fixtures/hirist/company_title_ambiguity_real_capture.html` | **New** — real data (Phase 6 Step 9's verbatim capture) |
| `data/daily/2026-09-20/*` | New — real live-validation artifacts (PART B), git-ignored |
| `data/reports/phase8_daily_queue_design.md` / `.json` | **New** |
| `data/reports/phase8_multisource_validation.md` / `.json` | **New** — this report |

**Not modified:** `scripts/search_worker.py`, `scripts/search_submission.py`,
`scripts/generate_run_report.py`, `scripts/naukri_adapter.py`,
`scripts/naukri_fetcher.py`, `scripts/naukri_fetch_bridge.js`,
`scripts/hirist_parser.py`, `scripts/hirist_adapter.py`,
`scripts/linkedin_adapter.py`, `scripts/source_registry.py`,
`scripts/score_job.py`, `config/profile.json`, `config/searches.json`,
`config/site_adapters.json`, `launchd/com.sarojjobos.dailysearch.plist`,
`data/applications/jobos.db`.

## 13. Exact Source Enablement Status

| Source | Status | Changed this phase? |
|---|---|---|
| Naukri | `AdapterStatus.ENABLED` | No (unchanged) |
| Hirist | `AdapterStatus.NOT_ENABLED` | No (unchanged — offline gate did not pass) |
| LinkedIn | `AdapterStatus.NOT_ENABLED` | No (unchanged — no authorized access mechanism) |

## 14. Scheduler Readiness

**A scheduler is not considered ready merely because it is safe — it
must also be operationally capable of generating fresh daily search
work. Both conditions are now met:**

- **Safety** (Phase 7.4): stale-lock reclaim, explicit signal trap,
  atomic workbook + `run_summary.json` writes, no application
  submission path, all re-confirmed unchanged and still passing this
  phase.
- **Operational capability** (Phase 8, this report): the daily wrapper
  now generates its own fresh search work every day, idempotently,
  live-proven end-to-end via PART B.

**Decision: `READY_FOR_SCHEDULER_ACTIVATION`.**

## 15. Remaining Limitations

1. **Hirist**: company/title extraction remains structurally
   unreliable (3 confirmed wrong splits + 10/20 entries lost on real
   data). Not enabled. No further live Hirist work planned until this
   is genuinely resolved (a real site-structure change, or an
   explicitly-approved, evidence-justified heuristic — neither exists
   today).
2. **LinkedIn**: no authorized access mechanism exists. Not enabled.
   Would require either an official API/connector becoming available,
   or a separate, explicit authorization decision this project has not
   made.
3. **`node` PATH under launchd's minimal environment** (Phase 7.4,
   unchanged, unresolved this phase) — confirm `which node` and add a
   plist `PATH` entry if needed before activating.
4. **No log rotation** (Phase 7.4, unchanged, unresolved this phase) —
   not urgent at current cadence.
5. **The disclosed Part A test-safety deviation** (this report) is
   fixed in the test code itself, but stands as a reminder: any future
   test touching `source_registry`/`submit_search()` in this codebase
   MUST explicitly restrict `sources`, never rely on the default,
   because `NaukriAdapter` is genuinely, permanently `ENABLED` in the
   real shared registry every test process loads.

## 16. Human Approval Boundary

Unchanged, re-verified this phase (PART A test 10, and by direct grep
across the entire daily chain including the new `submit_search.py`
call): `search_worker.process_queue_item()` still ends at
`jobs`/`candidate_job_matches` persistence and (optional) reporting.
`submit_search.py --confirm` queues a **search query**, never a job
**application** — confirmed distinct in every test and in the actual
code path. No code anywhere writes `jobs.status = "APPLIED"` or any
other applied-lifecycle value.

## 17. Application Submission

**NONE.** Confirmed by static analysis (no submission-capable token
anywhere in the daily chain) and by the live PART B run's own result
(`Application Status` values observed: `FOUND`, `NOT_QUALIFIED`,
`READY_FOR_APPROVAL` only).

## 18. launchd Status

**NOT ACTIVATED.** Not installed (`~/Library/LaunchAgents/com.sarojjobos.dailysearch.plist`
does not exist). Not loaded (`launchctl list` shows nothing matching
`sarojjobos`). Not started.

---

**Stopping here, per instruction. Not proceeding to LinkedIn. Not
proceeding to another phase automatically. launchd was not activated.**
