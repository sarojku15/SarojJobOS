# Naukri User-Agent Production Change — Single-Query Live Validation

**Objective:** confirm the newly implemented `JOBOS_NAUKRI_USER_AGENT`
production change (default headed-style UA, applied via Playwright's
standard context option, genuinely headless, `channel='chromium'`)
actually works against the live Naukri site, through the real,
unmodified production adapter/worker path — not a bespoke test harness.

**Result: SUCCESS.** HTTP 200 on every one of the 22 requests made (1
health check + 1 search + 20 detail pages). Classifier reached
`VALID_RESULTS`. 20 jobs discovered, normalized, and carried through
eligibility/scoring/ranking. Production DB is byte-identical before and
after. Exactly one query was made; no retry.

## Execution Method

Used the real production pipeline end-to-end via
`search_worker.run_once()` — **not** a custom fetch reimplementation like
the earlier isolation experiment. This is the same function the daily
background scheduler will call:

```
search_worker.run_once(temp_db_path, candidate_id="saroj", max_items=1)
  -> source_registry.discover_from_sources()   (real NaukriAdapter, real
     NaukriFetcher, real scripts/naukri_fetch_bridge.js -- byte-identical
     to what Phase 1 implemented, completely unmodified for this test)
  -> discover_local.normalize_job() / deduplicate()
  -> job_eligibility.assess_job_eligibility()
  -> score_job()                                (unchanged, 100-point rubric)
  -> job_ranking.build_ranking_record()          (freshness, ranking)
  -> jobs / candidate_job_matches UPSERT         (into a TEMPORARY DB only)
```

The temporary validation DB was created by copying
`data/applications/jobos.db` (a **read** of production, written only to a
new temp file — production was never opened for writing) so the real
candidate/profile rows were reused exactly as-is. Exactly one
`search_runs` + `search_queue` row was inserted into that temp copy,
containing exactly one query entry (`source=NAUKRI,
role="Infrastructure Engineer", location="Bangalore",
max_job_age_days=None`) — deliberately not using the automatic full
role×location×source query-plan builder, to guarantee exactly one query.

`JOBOS_BROWSER_HEADLESS` and `JOBOS_NAUKRI_USER_AGENT` were both
explicitly unset in the driver's environment before the run, guaranteeing
true defaults (headless=true, validated default UA) rather than relying
on ambient shell state.

## Pre-Flight (before the live request)

| Check | Result |
|---|---|
| Production DB SHA-256 | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production row counts | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| Candidate status | `saroj`, `ACTIVE` |

**FACT/OBSERVED.**

## A. Search / Fetch Diagnostics — FACTS Directly Observed This Run

| | Value |
|---|---|
| Health check URL | `https://www.naukri.com/` |
| Health check HTTP status | **200** |
| Search requested URL | `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore` |
| Search final URL | identical (no redirect) |
| Search HTTP status | **200** |
| `max_job_age_days` / `jobAge` parameter | **not present** (deliberately omitted, per instruction) |
| Detail-page requests | 20, one per discovered job link, **all HTTP 200** |
| Total requests made to Naukri | **22** (1 health check + 1 search + 20 detail pages) |
| Retries | **0** |

Every one of the 22 `DIAGNOSTIC {...}` stderr lines emitted by the real,
unmodified `naukri_fetch_bridge.js` showed `"status":200` with no
redirect. **FACT/OBSERVED.**

## Effective User-Agent — FACT vs. INTERPRETATION

- **INTERPRETATION (design-guaranteed, not empirically re-measured this
  run):** because `JOBOS_NAUKRI_USER_AGENT` was explicitly unset, the
  unmodified `naukri_fetch_bridge.js` code computes
  `naukriUserAgent = process.env.JOBOS_NAUKRI_USER_AGENT || DEFAULT_NAUKRI_USER_AGENT`
  → the validated default:
  `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36`.
  This is a direct reading of the production source code's own logic
  (verified statically and dynamically in
  `scripts/test_naukri_user_agent_config.py`), not a new measurement of
  *this specific run's* live traffic.
- **The production bridge's existing diagnostic contract was
  deliberately left unmodified** for this task (no new `DIAGNOSTIC_UA`
  line was added, per the instruction to keep existing diagnostic
  behavior intact and make no further code changes in a live-validation
  task) — so the effective UA was not re-captured via `page.evaluate()`
  in *this* run.
- **Carried-over FACT from the separate, already-completed isolation
  experiment** (`data/reports/naukri_headless_ua_isolation.md`, a
  different live run, same default UA string, same headless
  configuration): that run *did* directly measure the effective
  `navigator.userAgent` on every request and confirmed it matched the
  configured value exactly. This run's identical HTTP 200 outcome, using
  the same code path and the same computed default, is consistent with
  that prior direct measurement but is not, by itself, a second direct
  measurement of the UA header in this run.

## B. Browser Diagnostics — Headless Confirmed

Directly captured from `ps aux` while the search request's browser was
open (full, untruncated command line):

```
.../Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing
  ... --enable-unsafe-swiftshader --headless --hide-scrollbars --mute-audio
  --blink-settings=... --no-sandbox --user-data-dir=... --remote-debugging-pipe --no-startup-window
```

| | Value |
|---|---|
| `--headless` flag present | **YES** |
| `channel` | `chromium` (full "Google Chrome for Testing" binary, confirmed by executable path) |
| Visible UI | **false** |

**FACT/OBSERVED: the browser remained genuinely headless for the entire
run.**

## C. Classifier Result

- **State:** `VALID_RESULTS` (inferred conclusively, same basis as the
  prior isolation experiment: `adapter.search()` raised no
  `AdapterBlockedError`/`AdapterTimeoutError`, `blocked_sources=[]`, and
  20 non-empty jobs were returned — the only classifier state consistent
  with that combination in the unmodified production `search()` method).
- **Matched block phrase:** none — no block detected.
- **Worker-level status:** `search_runs.status = COMPLETED`,
  `errors_count = 0`, `blocked_queries = 0`.

## D. Full Pipeline Results (via the real production path)

| Metric | Value |
|---|---|
| Raw jobs | 20 |
| Malformed | 0 |
| Normalized jobs | 20 |
| Duplicates (in-batch) | 0 |
| Unique jobs | 20 |
| Excluded — experience | 15 |
| Excluded — location | 0 |
| Eligible jobs | 5 |
| Scored jobs | 5 |
| Ready-for-approval (score ≥ 70 gate reflected via priority) | 0 |
| Matches created | 5 |
| Matches updated | 0 |
| Timed-out queries | 0 |
| Succeeded queries | 1 |
| Errors | none |

**Score range (eligible/scored jobs only): 35–70.**

| Job ID | Company | Title | Score | Priority | Experience Eligibility |
|---|---|---|---|---|---|
| NAUKRI-bd37539119ff67c9 | Cradlepoint | Infrastructure Engineer | 70 | C | MATCH |
| NAUKRI-294fc43398b45f23 | Cisco | AI Infrastructure Engineer | 60 | REJECT | MATCH |
| NAUKRI-551b61c54c707f22 | Rarr Technologies | AI Infrastructure Engineer | 65 | REJECT | ABOVE_PROFILE |
| NAUKRI-f08dc042eea0f871 | IG Group | Infrastructure Engineer | 50 | REJECT | MATCH |
| NAUKRI-fffbd26836b276ef | Accenture | Infrastructure Engineer | 35 | REJECT | ABOVE_PROFILE |

The other 15 of the 20 discovered jobs were excluded at the eligibility
gate on experience (`excluded_experience=15`) before scoring ever ran —
expected, unmodified `job_eligibility.py` behavior given this candidate's
confirmed profile and this particular role's typical experience-range
spread on Naukri; not a defect introduced by this task.

Freshness (post-retrieval classification, `freshness.py`, unrelated to
the intentionally-omitted `max_job_age_days` discovery filter) was
computed for all 20 unique jobs regardless of eligibility, ranging from
`HOT` (2 days) to `OLD` (21 days) — confirms `freshness.py` continues to
operate correctly and independently of discovery-time filtering, exactly
as designed.

**Neither scoring, eligibility, ranking, freshness, deduplication, query
planning, worker logic, nor the database schema was modified by this
task.** All of the above numbers are the unmodified production functions
running against real, live Naukri data.

## Production DB Safety

| | SHA-256 | Row counts |
|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | identical |

**Byte-identical.** All writes (jobs, candidate_job_matches, search_runs,
search_queue) landed only in the temporary copy of the database, which
was deleted after this validation. No `if_version`/write ever targeted
`data/applications/jobos.db`.

## Regression Suite

```
Full standalone suite (38 files): PASS=38 FAIL=0
python3 -m py_compile across all scripts/*.py: PY_COMPILE_ALL_OK
```

No test file was added or modified in this task (this was a live
validation task, not an implementation task).

## Files Changed

**None.** This was a pure live-validation task: no production file, test
file, or configuration file was created, modified, or deleted. The only
new files were a temporary driver script and a temporary DB copy, both
created and deleted entirely within the session scratchpad (outside the
project directory).

## Exact Live Request Counts

- Health checks: 1
- Search queries: 1
- Detail-page fetches: 20
- **Total live requests to Naukri: 22**
- Retries: 0

## Summary Table

| | Value |
|---|---|
| HTTP status (search) | 200 |
| Classifier | VALID_RESULTS |
| Jobs found (raw / unique) | 20 / 20 |
| Eligible jobs | 5 |
| Score range | 35–70 |
| Production DB SHA before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production DB SHA after | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production row counts before/after | identical (candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0) |
| Full regression suite | 38/38 pass |
| `py_compile` | clean |
| **Validation succeeded** | **YES** |
| **Next gate** | **Controlled multi-query Naukri validation** (Phase 3 per the project's phase plan) — not started in this task |

**Stopping here, per instruction.** No multi-query validation, other job
board work, Excel generation, or application automation was started.
