# Same-job / multiple-search schema decision

## Consumers inspected before any schema change (as required)

| Consumer | Reliance on `candidate_job_matches` |
|---|---|
| `application_lifecycle.py` | Reads/writes `candidate_status` by `(candidate_id, job_id)` only — a human's ONE application/shortlist decision per job. Correctly candidate+job scoped, must stay that way. |
| `generate_run_report.py` (`load_candidate_job_matches`, `build_report_rows`) | Reads the candidate-wide "latest" row per job_id for the Excel report and dashboard — intentionally wants candidate-wide latest state, not any one search's view. |
| `search_worker.py` (`upsert_candidate_job_match`) | THE writer. `INSERT ... ON CONFLICT(candidate_id, job_id)` — this is where a later search's write silently overwrote an earlier search's `search_run_id`/`resume_id`/`profile_version`/`fit_score`. Already explicitly protects `candidate_status` from this overwrite; nothing else was protected. |
| `results_store.py` (`_job_ids_for_search`) | THE broken consumer — determines "which jobs belong to search X" via `candidate_job_matches.search_run_id IN (search X's run_ids)`. Since there is only one row per (candidate,job), an earlier search lost a job the moment a different search re-matched it. |
| `resume_store.py`, `search_provider_usage_store.py` | No direct dependency on this table's keying (only incidental mentions). |
| Dashboard, Excel export, API endpoints | All go through `generate_run_report.py`'s candidate-wide loader — unaffected by this fix (see below). |

## Decision

`candidate_job_matches` conflates two different concerns:
- **(a) Candidate+job-scoped facts, correctly singular per pair**: `candidate_status`. Kept exactly as-is, `PRIMARY KEY (candidate_id, job_id)` unchanged.
- **(b) Search-run-scoped scoring output**: `fit_score`, `priority`, `experience_eligibility`, `resume_id`, `resume_variant`, `profile_version`, skill match JSON. A different search (potentially pinned to a different `profile_version`) legitimately computes these differently for the same job.

**Smallest correct fix**: a new, additive table `candidate_job_search_matches`, `PRIMARY KEY (search_run_id, job_id)`, holding only (b). Never overwritten by a different run. `candidate_job_matches` is not restructured — it keeps serving as the candidate-wide "latest snapshot" for the dashboard and full Excel report, which correctly want candidate-wide latest state, not one search's own view.

## What changed

- `scripts/migrate_v9_candidate_job_search_matches.py` (new migration) + inlined into `migrate_v2_schema._create_new_tables()` (so every test DB gets it, since `search_worker.py`'s core upsert path writes to it unconditionally).
- `scripts/search_worker.py`: `upsert_candidate_job_match()` now also writes one row per `(search_run_id, job_id)` to the new table, alongside its existing (unchanged) write to `candidate_job_matches`.
- `api/results_store.py`: `_job_ids_for_search()` now scopes from the new table. `get_results_for_saved_search()`/`_load_persisted_results()` now prefer the new table's `fit_score`/`priority`/skills/`resume_variant` for a search's own scoped view, falling back to the candidate-wide snapshot only when no scoped row exists (a DB predating this migration). `candidate_status` is never sourced from the new table — it has no such column at all.

## What did NOT change

- `candidate_job_matches`'s own schema, primary key, or the application-status/shortlist write path — completely untouched.
- The dashboard and full Excel report — both continue reading the candidate-wide "latest snapshot," exactly as before.
- No existing data was migrated or backfilled — old runs predating this fix have no `candidate_job_search_matches` rows; their search-scoped display falls back to the (potentially collision-prone) candidate-wide snapshot, same as before this fix, for that historical data only. New runs are fully protected.

## Verification

`scripts/test_same_job_multi_search_isolation.py` (17/17 pass) proves the exact required scenario: Candidate A, Search 1 → Job X → Resume A, Search 2 → Job X → Resume B — both searches independently retain their own results, run_id, score, and resume_variant for the identical job. Full regression suite: 87/87 pass. Production DB byte-identical throughout.
