# Phase 8 PART A — Daily Queue Generation: Architecture & Fix

## Architecture Questions — Answered From Code, Not Guessed

**1. Who creates `search_queue` rows?**
Exactly one function: `search_submission.submit_search()` (and its
read-only preview half, `build_search_plan()`). Its CLI entry point is
`scripts/submit_search.py`. Nothing else in the codebase inserts into
`search_queue`.

**2. Who consumes `search_queue` rows?**
Exactly one function: `search_worker.claim_next_queue_item()` (an
atomic `SELECT ... WHERE status='QUEUED' ... UPDATE ... SET
status='RUNNING'`), followed by `process_queue_item()`. The CLI entry
point is `scripts/run_search_worker.py --once`.

**3. What causes a queue item to become completed?**
`search_worker._finalize_queue_item()`, called unconditionally at the
end of `process_queue_item()` (including every early-failure path),
sets `search_queue.status` and `search_runs.status` to one of
`COMPLETED` / `PARTIAL` / `BLOCKED` / `FAILED` (never left `RUNNING`
by a normal code path).

**4. What happens after the queue is exhausted?**
`claim_next_queue_item()`'s `WHERE sq.status = 'QUEUED' AND sr.status
= 'QUEUED'` finds nothing; `run_once()`'s loop breaks immediately;
`run_search_worker.py` prints "No eligible QUEUED work found." — a
safe, non-error, non-destructive terminal state. **Before this
phase's fix, nothing in the daily chain ever created a new queue item
to replace it.**

**5. Is the queue intended to be one-shot or recurring?**
**One-shot per submission, by design** — one `submit_search()` call
produces exactly one `search_runs` + `search_queue` row, consumed
exactly once. Recurrence is achieved by calling `submit_search()`
again, not by the queue regenerating itself. **This is not a defect in
`search_submission.py`/`search_worker.py`** — those modules do exactly
what their own scope says. The gap Phase 7.4 found was purely that
**nothing else in the automation chain called `submit_search()` on a
recurring basis.**

**6. Is there already a safe query-planning/submission function that
should be called by the daily wrapper?**
**Yes — `search_submission.submit_search()`, already fully built,
already extensively tested (25 tests in `test_search_submission.py`),
and already exposed via a CLI (`scripts/submit_search.py`).** No new
queue logic needed to be written.

**7. Can daily queue generation be made idempotent?**
**Yes — it already is, and always was.** `submit_search()`'s existing
duplicate-submission policy computes a deterministic fingerprint
(candidate + profile version + query plan + score/result thresholds)
and reuses an existing still-`QUEUED` row with the same fingerprint
instead of creating a duplicate — proven by the pre-existing
`test_search_submission.py` test 20, and re-proven end-to-end by this
phase's new `test_phase8_daily_queue.py` (tests 1–3). A `COMPLETED` or
`FAILED` run with the same fingerprint does **not** block a fresh
submission (test 4) — this is exactly what makes daily recurrence
possible without ever producing duplicate work.

---

## Architecture Before The Fix

```
launchd (not installed)
   -> run_daily_search.sh
        -> run_search_worker.py --once   <-- ONLY consumes, never produces
             -> claim_next_queue_item()
             -> process_queue_item()
             -> [queue exhausted after first successful run -- forever]
```

## Architecture After The Fix

```
launchd (not installed)
   -> run_daily_search.sh
        -> submit_search.py --candidate-id saroj --max-job-age-days 3 --confirm
             -> search_submission.submit_search()   <-- EXISTING, unmodified,
                                                          already-idempotent
        -> run_search_worker.py --once
             -> claim_next_queue_item()
             -> process_queue_item()
        -> generate_run_report.py (via --report-out, existing since Phase 7.1)
        -> run_summary.json (existing since Phase 7.2)
```

## The Fix — Smallest Architecture-Consistent Change

| File | Change | Why |
|---|---|---|
| `scripts/submit_search.py` | Added one new optional flag, `--max-job-age-days N`, passed straight through to the existing `submit_search()` parameter of the same name (already existed, was simply not exposed by this CLI) | The daily automation needs to apply the project's standard 3-day freshness constraint; no other CLI exposed it |
| `scripts/run_daily_search.sh` | Added one call to `submit_search.py --candidate-id saroj --max-job-age-days 3 --confirm` immediately before the existing `run_search_worker.py --once` call | This is the entire fix — one new subprocess call reusing 100% existing logic |

**`sources` is deliberately never hardcoded** — omitted from the new
wrapper call, letting `submit_search()`'s own existing default
(`search_profile._default_sources()`, "every currently
`AdapterStatus.ENABLED` real adapter") resolve it dynamically. The
moment Hirist or LinkedIn is ever actually enabled, the daily
submission automatically includes it, with zero further code change.

**No new queue table, no new status values, no new locking mechanism,
no change to `search_worker.py`, `search_submission.py`, or the
`search_queue`/`search_runs` schema.** The daily wrapper's own
submission-step failure is logged but does not abort the run — the
worker step is always safe to attempt regardless.

## Queue Safety Requirements — How Each Is Met

| Requirement | How met |
|---|---|
| Idempotent | Inherited from `submit_search()`'s existing fingerprint-based duplicate policy — re-proven end-to-end this phase |
| Candidate-scoped | `submit_search()` has always taken `candidate_id` as an explicit, required argument; no global/implicit candidate anywhere in this layer (confirmed by `test_search_submission.py` test 23, and Phase 8's own test 5) |
| Source-aware | `sources=None` resolves to `_default_sources()` — ENABLED-only, confirmed by Phase 8's own test 6, using a safe registry-isolation technique that never risks a real live call |
| Snapshot-aware | `search_runs.query` freezes the exact query plan at submission time (pre-existing, unchanged); confirmed deterministic by Phase 8's own test 7 |
| Safe for repeated daily execution | Confirmed by Phase 8 PART C's full RUN 1 / RUN 2 / "next day" cycle test |
| Compatible with existing duplicate policy | Not reimplemented — the existing policy IS the mechanism used |
| Compatible with existing `search_run`/`search_queue` semantics | No schema or state-machine change of any kind |

## Historical Data — Untouched

No historical `jobs`/`candidate_job_matches`/`search_runs` row was
modified or deleted by this fix. `submit_search()` only ever `INSERT`s
new rows; nothing in this fix ever `UPDATE`s or `DELETE`s an existing
one. Confirmed: production DB byte-identical before and after this
phase's entire body of work (see the main report,
`data/reports/phase8_multisource_validation.md`).

## Offline Test Proof

`scripts/test_phase8_daily_queue.py` (new, 11 checks, all passing) —
see the main report for the full checklist and results.

**See `data/reports/phase8_multisource_validation.md` for the full
Phase 8 results, including one disclosed safety deviation this file's
FIRST draft caused (fixed before any of the above was finalized).**
