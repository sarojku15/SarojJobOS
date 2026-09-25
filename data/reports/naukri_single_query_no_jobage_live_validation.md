# Naukri Single-Query Live Validation — `jobAge=3` Removed (Controlled Comparison)

**Query:** role="Infrastructure Engineer", location="Bangalore" —
**identical to the immediately previous controlled test, `max_job_age_days`
completely omitted this time** (unrestricted; not requested at all, not
set to `None` as a URL value — simply absent from the query).

## Pre-Flight Confirmations (all verified before execution)

| Check | Result |
|---|---|
| Query has no `max_job_age_days` | **Confirmed** — `submission.query_plan[0]["max_job_age_days"] is None` |
| `NaukriAdapter.health_check_is_advisory` | **True** (unchanged) |
| Headless default | `JOBOS_BROWSER_HEADLESS` unset → headless (unchanged) |
| `channel: 'chromium'` | Present in `naukri_fetch_bridge.js` (unchanged) |
| Production DB SHA before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` ✓ |
| Production row counts before | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 ✓ |

## A. Homepage health_check

| Field | Value | Label |
|---|---|---|
| reachable | `False` | FACT |
| block_reason | `UNKNOWN_BLOCK` | FACT |
| detail (from `AdapterHealth`) | `""` (empty — `health_check()`'s own return value was never enhanced with the matched-phrase diagnostic; only `classify_search_page()` was) | FACT |
| HTTP status (new diagnostic) | **403** | FACT — never captured for any prior run |
| Final/redirect URL | `https://www.naukri.com/` (identical to requested — no redirect) | FACT |

## B. Search Execution

| Field | Value | Label |
|---|---|---|
| search() attempted? | **Yes, exactly 1 time** (instrumentation-confirmed count) | FACT |
| Requested URL | `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore` | FACT |
| Final/redirect URL | identical to requested — no redirect | FACT |
| HTTP status (new diagnostic) | **403** | FACT — never captured for any prior run |
| Matched block phrase (new diagnostic) | **`'access denied'`** | FACT — never captured for any prior run |
| Classifier state | `SearchPageState.BLOCKED` | FACT |
| Classifier block_reason | `UNKNOWN_BLOCK` | FACT |
| Classifier detail | `"block phrase matched (UNKNOWN_BLOCK): 'access denied'"` | FACT |
| Exception raised | `AdapterBlockedError` (existing, unchanged behavior) | FACT |

## C. Browser Evidence

| Field | Value | Label |
|---|---|---|
| headless | `true` | FACT |
| channel | `chromium` (full binary) | FACT |
| Executable observed | `chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app` | FACT |
| `chromium_headless_shell` observed | **0** of 328 observations | FACT |
| `--headless` flag observed | 74 of 328 observations | FACT |
| Visible UI | None (headless-shell absent from every observation; full binary launched with `--headless`) | FACT |

## D. Results

| Field | Value |
|---|---|
| Raw jobs | 0 |
| Normalized jobs | 0 |
| Unique jobs | 0 |
| Freshness classifications | None (0 jobs — nothing to classify) |

## E. Side-by-Side Comparison

| | **Previous test** | **Current test** |
|---|---|---|
| URL | `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore?jobAge=3` | `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore` |
| Homepage health_check | Failed, `UNKNOWN_BLOCK` | Failed, `UNKNOWN_BLOCK` — **unchanged** |
| Homepage HTTP status | Not captured (diagnostic didn't exist yet) | **403** (new evidence, no comparison possible) |
| search() attempted? | Yes (1x) | Yes (1x) — **unchanged** |
| Search HTTP status | Not captured (diagnostic didn't exist yet) | **403** (new evidence, no comparison possible) |
| Final/redirect URL | Not captured then | Identical to requested — no redirect (new evidence) |
| Matched block phrase | Not captured then (generic `"block phrase matched (UNKNOWN_BLOCK)"` only) | **`'access denied'`** (new evidence) |
| Search classifier state | `BLOCKED` | `BLOCKED` — **unchanged** |
| Jobs returned | 0 | 0 — **unchanged** |
| Browser: full chromium confirmed | Yes (327 obs., 0 headless-shell) | Yes (328 obs., 0 headless-shell) — **unchanged** |
| Worker aggregate status | `BLOCKED` | `BLOCKED` — **unchanged** |

### Direct Answers

1. **Did removing `jobAge=3` change the outcome?** **No.** Every observable outcome (health check result, whether search was attempted, the search classifier's verdict, jobs returned, worker status) is identical with and without the parameter.
2. **Did homepage health_check result change?** No — `UNKNOWN_BLOCK`/unreachable in both. *(HTTP status is new data this run, not a change from a known prior value — the prior run simply never captured it.)*
3. **Did search-page HTTP status change?** **NOT DETERMINABLE as a comparison** — the previous run never captured any HTTP status code at all (that diagnostic did not exist until after that run). This run's 403 cannot be compared to a value that was never recorded.
4. **Did final URL change?** The *requested* URL differs by design (no `jobAge`); no redirect occurred in this run (final URL == requested URL). Whether a redirect occurred in the previous run is **NOT DETERMINABLE** (not captured then).
5. **Did the matched block phrase change?** **NOT DETERMINABLE as a comparison** for the same reason as #3 — the previous run's exact phrase was never captured. This run's phrase (`'access denied'`) is new information, not evidence of a change.
6. **Did search-page classifier change?** No — `BLOCKED` in both.
7. **Were listings returned?** No, in either run.
8. **Freshness data?** None available in either run (0 jobs both times).

## Interpretation (per the stated rules)

**Both homepage and search remain blocked without `jobAge=3`.** Per the
governing interpretation rule: **this documents that `jobAge` is NOT
supported as the cause of the block, based on this controlled comparison.**
This directly reinforces (with a genuine controlled A/B removal, not just a
cross-run inference) the forensic report's Hypothesis F verdict:
**CONTRADICTED**.

**New, independent evidence — HTTP 403 on both requests.** This is the
first time this project has ever captured an HTTP status code from a live
Naukri request. A `403 Forbidden` response on both the homepage and the
search page is a genuine HTTP-layer access-control signal, **independent**
of the page-text phrase classifier. This materially strengthens confidence
that the `BLOCKED` classification is correct and not a text-classifier false
positive (Hypothesis E from the forensic report) — two independent signals
(a real 403 status code, and a real "access denied" phrase in the body)
now agree.

**Root cause remains unresolved.** Per the explicit instruction: *"If both
remain blocked, root cause must not be attributed to headless mode, rate
limiting, WAF policy, or fingerprint without direct evidence."* This run
provides **no new evidence to distinguish** the still-open hypotheses
(categorical headless block vs. cumulative rate-limiting/reputation vs.
another cause) — removing `jobAge` did not change whether the block
occurred, so it gives no signal either way on *why* the block is occurring.
That question remains **NOT DETERMINABLE**.

**Advisory architecture value confirmed again.** For the second consecutive
live run, `search()` was genuinely attempted despite a failed homepage
health check, and its own outcome (this time also `BLOCKED`, with far more
diagnostic detail than before) correctly drove the worker's aggregate
status. The architecture continues to behave exactly as designed.

## Production DB Safety

| | SHA-256 | Size | Row counts |
|---|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | 114688 | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | 114688 | identical |

**Byte-identical. Exactly one live Naukri search was attempted. No retry
occurred.**

## No Further Action Taken

Per instruction, no additional live request was made after this one. No
application behavior was changed as part of executing this test (the three
diagnostic additions were made and verified in the prior forensic task, not
this one). The BLOCKED classifier was not weakened or bypassed. The
advisory health-check architecture was not disabled. The browser
fingerprint/configuration was not changed.
