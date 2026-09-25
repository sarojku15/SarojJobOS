# Phase 6 Step 4 — Hirist One-Query Live Validation

**Result: BLOCKED (`SOFT_BLOCK_OR_CHALLENGE`) on the first and only
page. Per instruction, this validation stopped immediately — no retry
was attempted. Hirist remains `AdapterStatus.NOT_ENABLED`,
`capabilities = frozenset()`, unchanged.**

## Executive Summary

The single authorized live query reached Hirist's real search-results
page successfully at the HTTP level (200, no redirect, no login wall)
— but the adapter's own offline-built classifier (`hirist_parser.py`)
detected the substring `"captcha"` somewhere in the page's rendered
body text and correctly, safely raised `AdapterBlockedError` rather
than proceeding to parse or fabricate any data. **Zero jobs were
returned; this is the correct, designed behavior for a detected
challenge signal, not a bug in that sense.**

A separate, important observation: the blocked page's HTML length
(312,116 characters) is nearly identical to the length captured by
Phase 6 Step 1's confirmed-successful, unblocked inspection of the same
URL shape (312,194 characters — a difference of only 78 characters).
This is **suggestive, not confirmed**, evidence that the `"captcha"`
match may be a **false positive** from an overly broad keyword check
(e.g. a routine reCAPTCHA script tag or footer reference present on the
page regardless of whether an actual interactive challenge was shown),
rather than a genuine block. **This is not resolved in this task** — no
second request was made to investigate further, per the explicit "no
retries" instruction.

## Preconditions (Confirmed Before the Live Request)

| | Value |
|---|---|
| `HiristAdapter.status` | `AdapterStatus.NOT_ENABLED` |
| `HiristAdapter.capabilities` | `frozenset()` |
| `search_profile._default_sources()` | `['NAUKRI']` |
| Production DB SHA-256 | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production row counts | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |

All confirmed exactly matching the required known-good values.

## Live Fetch Mechanism (Narrowly Scoped, Built for This Step)

Two new, permanent, production files were added — mirroring
`naukri_fetch_bridge.js`/`naukri_fetcher.py`'s exact, already-validated
configuration:

- **`scripts/hirist_fetch_bridge.js`** — Playwright, `channel:
  'chromium'`, headless via the existing `JOBOS_BROWSER_HEADLESS`
  convention. **No User-Agent override, no stealth, no fingerprint
  customization** — nothing in Hirist's Phase 6 evidence indicated any
  headless-specific blocking behavior, so applying an unevidenced
  customization would itself have violated this project's strict
  evidence discipline.
- **`scripts/hirist_fetcher.py`** — subprocess wrapper, identical
  contract to `naukri_fetcher.py` (`AdapterTimeoutError` on failure,
  raw HTML on success).

`scripts/hirist_adapter.py` was updated to import this real fetcher as
its default (replacing the Phase 6 Step 3 placeholder that raised
`NotImplementedError` unconditionally), and to enforce a **10-second
delay between successive page fetches** within one `search()` call
(never before the first), honoring `robots.txt`'s directly-observed
`Crawl-delay: 10`. **Safety against accidental production use now comes
entirely from `status = AdapterStatus.NOT_ENABLED`** (the
registry-level pre-flight gate), exactly the same architecture
`NaukriAdapter` itself has always relied on — not from the fetcher
refusing to work.

**Two existing offline tests that previously asserted the placeholder
fetcher's `NotImplementedError` were updated** (necessarily, to avoid
those tests now attempting a real live call) to instead verify the
class-level `NOT_ENABLED` gate and the registry's zero-call guarantee —
see the companion JSON report's `test_updates` section for the exact
diff description.

## Exactly One Query

| | Value |
|---|---|
| Role | Senior DevOps Engineer |
| Location | Bengaluru |
| Entry point | `HiristAdapter().search(SearchQuery(role="Senior DevOps Engineer", location="Bengaluru"))` — the real, unmodified production adapter path |
| Retries | **0** |
| Additional role/location combinations run | **0** |

## Exact Request(s) Made

| # | URL | HTTP Status | Final URL | Redirected |
|---|---|---|---|---|
| 1 | `https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru` | **200** | identical | No |

**Total live requests: 1.** No second page was requested — the
adapter's own pagination loop never got past classifying page 1 (a
`SOFT_BLOCK_OR_CHALLENGE`/`BLOCKED` result on the first page raises
immediately, by design — see Phase 6 Step 3's implementation report).

## Browser/Runtime Configuration

| | Value |
|---|---|
| `headless` | `true` (`JOBOS_BROWSER_HEADLESS` unset → default headless) |
| `channel` | `'chromium'` |
| User-Agent override | None applied |
| Timing | Started `2026-09-20T14:30:49.786Z`, ended `2026-09-20T14:30:54.342Z` — **4.6 seconds total** for the single request (well under the 10-second Crawl-delay, since no second page was ever attempted — the inter-page delay was never triggered) |

## Page Classification

| | Value |
|---|---|
| Classifier state | **`SOFT_BLOCK_OR_CHALLENGE`** |
| Matched phrase | `"captcha"` (exact, case-insensitive substring match) |
| Detail | `"challenge phrase matched: 'captcha'"` |
| HTML length | **312,116 characters** |
| Job count parsed | 0 (classification stopped at the block-phrase check, before any JSON-LD extraction was attempted) |
| Pagination link found | Not evaluated — pagination-link discovery never runs after a block classification |

**Comparison to Phase 6 Step 1's successful inspection (same URL
shape, different but adjacent live moment):**

| | Phase 6 Step 1 (exploratory) | This validation (Step 4) |
|---|---|---|
| HTTP status | 200 | 200 |
| HTML length | 312,194 chars | 312,116 chars (78 fewer) |
| Classifier result | No block detected (informal inspection, no formal classifier existed yet) | `SOFT_BLOCK_OR_CHALLENGE` (formal classifier, "captcha" match) |

**INTERPRETATION, not confirmed fact:** the near-identical page length
is suggestive that this was substantively the same kind of page both
times (a real, populated results page), and that the `"captcha"` match
in this run may be a **false positive** — e.g. matching a routine,
always-present reCAPTCHA script reference or footer text rather than an
actual interactive challenge shown to this specific request. **This
project does not confirm or refute that hypothesis in this task** —
doing so would require inspecting the actual matched HTML context, and
no second request was made to capture or investigate it further, per
the explicit "no retries" instruction.

## Error Raised

```
AdapterBlockedError:
  reason: UNKNOWN_BLOCK
  detail: "SOFT_BLOCK_OR_CHALLENGE: challenge phrase matched: 'captcha'"
```

This is the **expected, designed** behavior of the adapter's error
model (Phase 6 Step 3): a `SOFT_BLOCK_OR_CHALLENGE` classification on
the first page raises `AdapterBlockedError` with
`BlockReason.UNKNOWN_BLOCK`. The adapter did **not** crash, did **not**
return fabricated or partial data, and did **not** attempt any
workaround — it failed exactly as designed.

## Validation Objectives — Precise Results

| # | Objective | Result |
|---|---|---|
| 1 | Search URL construction | **Validated** — the adapter's constructed URL exactly matched the live request made, and the live site returned HTTP 200 at that exact URL with no redirect |
| 2 | HTTP accessibility | **Validated** — HTTP 200 reached |
| 3 | JSON-LD parsing | **NOT exercised** — the block-phrase check runs before JSON-LD extraction in `classify_search_page()`; this run never reached that code path |
| 4 | Job extraction | **NOT exercised** (same reason) |
| 5 | Title/company extraction | **NOT exercised** (same reason) |
| 6 | URL extraction | **NOT exercised** (same reason) |
| 7 | Pagination detection | **NOT exercised** — pagination-link discovery never runs after a block classification |
| 8 | Pagination handling | **NOT exercised** — only 1 page was ever requested |
| 9 | Duplicate suppression | **NOT exercised** (trivially — zero jobs) |
| 10 | Block/challenge detection | **Validated** — the safety mechanism itself fired correctly and prevented the adapter from proceeding; whether the specific trigger was a true or false positive is separately unresolved (see above) |
| 11 | Error handling | **Validated** — `AdapterBlockedError` raised with the correct reason/detail, exactly matching the documented contract |
| 12 | No fabricated fields | **Validated** — zero fields were fabricated; the adapter returned nothing rather than guessing |

**For every field this project extracts, distinguishing actually
observed / unavailable / inferred, as required:** no job fields were
observed live in this run at all (0 jobs parsed) — the "actually
observed" column from Phase 6 Step 3's offline design remains
unchanged by this task; nothing here promotes any inferred field to
observed.

## Rate Limit / Safety Compliance

| | Confirmed |
|---|---|
| `Crawl-delay: 10` respected | Yes — not violated (only 1 request was made; the inter-page 10-second delay exists in the code but was never triggered since pagination never began) |
| Retries | 0 |
| Parallel requests | 0 |
| Detail-page requests | 0 |
| Application requests | 0 |
| Authentication attempted | 0 |
| CAPTCHA interaction attempted | 0 |
| Anti-bot bypass attempted | 0 |

## Pass/Fail Assessment

Per the explicit criteria, a **successful** validation requires *all*
of: live page reachable, no block/challenge, parser produces valid
results, no fabricated fields, correct pagination handling, no
unexpected exceptions, production DB unchanged, no prohibited network
behavior, exact request count documented.

**This validation did not meet all criteria — it is a documented
FAILURE outcome, which is explicitly still useful evidence per
instruction:**

| Criterion | Met? |
|---|---|
| Live page reachable | Yes |
| No block/challenge | **No** — `SOFT_BLOCK_OR_CHALLENGE` was raised |
| Parser produces valid results | **No** — never reached parsing |
| No fabricated required fields | Yes (trivially) |
| Pagination handled correctly | N/A — never began |
| No *unexpected* exceptions | Yes — the raised exception was the correct, designed response, not a crash or bug |
| Production DB unchanged | Yes |
| No prohibited network behavior | Yes |
| Exact request count documented | Yes — 1 |

## Production DB Safety

| | SHA-256 |
|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |

**Byte-identical.** Row counts also confirmed unchanged. **No database
of any kind was opened by this validation** — it exercised
`HiristAdapter.search()` directly (the adapter path only, as
instructed), which has no persistence step; there was no need for even
a temporary DB, since nothing in this validation scores, matches, or
tracks a job.

## Final Verification

```
Hirist-specific tests (scripts/test_hirist_adapter.py):                14/14 PASS
Shared architecture test (scripts/test_multi_source_adapter_architecture.py): 15/15 PASS
Full standalone regression suite (42 files -- no new test file added this step, only edits to the two above): PASS=42 FAIL=0
python3 -m py_compile across all scripts/*.py:                         PY_COMPILE_ALL_OK
JSON validation (this report's .json):                                 valid
```

`HiristAdapter.status` = `NOT_ENABLED`, `capabilities` = `frozenset()`,
`search_profile._default_sources()` = `['NAUKRI']` — all re-confirmed
unchanged after this validation.

## Recommendation for Next Step (No Action Taken)

The near-identical page length between this run's blocked classification
and Phase 6 Step 1's successful inspection is the single most
actionable lead this validation produced. **Recommended, but NOT
performed in this task:** an OFFLINE investigation (no live request)
reviewing whether `hirist_parser.py`'s `_CHALLENGE_PHRASES` list is
too broad — e.g. whether `"captcha"` alone, without additional context
(such as proximity to words like "verify," "solve," or an actual
`<iframe>`/challenge-widget marker), is a reliable signal, versus a
routine, always-present script/footer reference. This is exactly the
kind of refinement a live validation is meant to surface, and it should
be evaluated and, if warranted, fixed **offline, with regression tests**
before any future live re-attempt — not decided or implemented in this
task.

**Stopping here, per instruction. No retry was attempted. No
multi-query validation was run. Hirist was not enabled.**
