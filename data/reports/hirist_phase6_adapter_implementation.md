# Phase 6 Step 3 — Hirist Adapter Offline Implementation

**Offline implementation only. Zero live Hirist requests were made.
Hirist remains `AdapterStatus.NOT_ENABLED`, `capabilities =
frozenset()`, and is NOT part of the default search plan.**

## Executive Summary

`scripts/hirist_adapter.py` has been transformed from a bare skeleton
(that raised `AdapterNotEnabledError` immediately from every method)
into a genuinely implemented, offline-tested adapter — schema.org
JSON-LD extraction, bounded/loop-protected pagination, and a full
error/status mapping — while remaining exactly as inert to the
production pipeline as before: `status = AdapterStatus.NOT_ENABLED`
means `source_registry.discover_from_sources()` still skips it before
any network call, with zero behavior change to the live system. No
live fetch mechanism was built; the adapter's default fetcher is a
placeholder that raises `NotImplementedError` unconditionally,
guaranteeing it is structurally incapable of a live request even if
misused outside this project's test suite.

## Files Changed

| File | Change |
|---|---|
| `scripts/hirist_adapter.py` | **Rewritten** — real `search()`/`health_check()` implementation, URL construction, `HiristFetcher` placeholder (raises `NotImplementedError`, never live) |
| `scripts/hirist_parser.py` | **New** — JSON-LD extraction, classification (`HiristPageState`), pagination-link discovery. Pure, offline logic only |
| `scripts/test_hirist_adapter.py` | **New** — 14 offline test scenarios (all requested + 2 extra safety checks), zero live calls |
| `scripts/test_multi_source_adapter_architecture.py` | **Minimally modified** — see "Required Narrowly-Scoped Change," below |
| `data/fixtures/hirist/*.html` (10 files) | **New** — synthetic fixtures, clearly labeled as such, not real captures |

**Not modified:** `source_registry.py`, `search_profile.py`,
`search_worker.py`, `score_job.py`, `job_eligibility.py`,
`experience_eligibility.py`, `location_taxonomy.py`, `freshness.py`,
`discover_local.py`, `cross_source_dedup.py`, `canonical_job.py`,
`tracker.py`, any database schema, `data/applications/jobos.db`,
`naukri_adapter.py`, `linkedin_adapter.py`, or any LinkedIn design
document.

## Required Narrowly-Scoped Change (Explained First, As Instructed)

`scripts/test_multi_source_adapter_architecture.py`'s
`test_unsupported_adapter_behavior` previously asserted that **every**
skeleton adapter's `health_check()`/`search()` raises
`AdapterNotEnabledError` immediately. This was true for Hirist before
this step, and remains true for the other 10 skeletons. It is **no
longer true for Hirist**, because Hirist now has real parsing logic —
calling `HiristAdapter().health_check()` (with the default, uninjected
fetcher) now reaches `HiristFetcher.fetch()`'s own
`NotImplementedError` instead, a **different, still-completely-safe**
safety backstop.

**The fix:** `HIRIST` is now excluded from that one specific loop, with
an explanatory comment, and a new, separate, Hirist-specific assertion
was added in the same function confirming: `HiristAdapter.status ==
NOT_ENABLED`, and its default fetcher raises `NotImplementedError`
rather than making any live call. Every other assertion in that file
(capability declarations, source registration, adapter selection,
`_default_sources()` exclusion, the generic `discover_from_sources()`
zero-call guarantee) already covered Hirist generically and needed **no
change** — they still pass unmodified. **This is the only change made
to any file outside `hirist_adapter.py`/`hirist_parser.py`/the new test
and fixture files.**

## Adapter Behavior Implemented

### 1. URL Construction (Conservative, Matching the One Observed Shape)

```
https://www.hirist.tech/search/<role-slug>-jobs?locations=<location>
```

- `role` → slugified (lowercase, non-alphanumeric runs collapsed to a
  single hyphen), matching `naukri_adapter.py`'s own `_slugify()`
  convention exactly.
- `location` → sent as the `locations=` query parameter, on the same
  **honest-uncertainty basis** already established for Naukri's own
  `jobAge` parameter: it is sent, but **its filtering effect is
  explicitly NOT claimed as confirmed** (Phase 6's own canonical-URL
  observation dropped this parameter — see the code's own docstring).
- `query.extra["hirist_search_url"]` escape hatch, mirroring
  `naukri_adapter.py`'s identical pattern.
- **No** experience, freshness, remote, or any other parameter is
  added — none was observed, none is implemented.

### 2. JSON-LD Extraction (`hirist_parser.py`)

- Scans every `<script type="application/ld+json">` block for one
  parsing as a dict with `"@type": "ItemList"` — the exact type Phase
  6 directly observed.
- For each `itemListElement` entry, two extraction paths in preference
  order:
  1. A nested `"item"` object (schema.org's standard `ItemList` →
     `JobPosting` shape) — reads `title`, `hiringOrganization.name`,
     `url`, `datePosted`, `description`, `employmentType`,
     `jobLocation` **only if each is individually present**. This path
     was **not directly confirmed** against the real Hirist page in
     Phase 6 (the capture was truncated); it is implemented defensively
     against schema.org's own real, external vocabulary and exercised
     only by this task's own synthetic fixtures.
  2. A flat `"name"` string (the **one** field Phase 6 actually,
     directly observed) — split on `" - "` **only when that exact
     delimiter occurs exactly once**. Zero or multiple occurrences →
     the entry is skipped entirely (fails closed), never guessed.
- An entry that can't be safely resolved to a `company`+`title` pair by
  either path is **skipped**, never fabricated.
- If every entry on an otherwise-valid page fails to parse, the page is
  classified `PARSE_FAILURE` (not silently treated as an empty result —
  Hirist's own data claimed jobs existed).

### 3. Fields Intentionally NOT Implemented

Per the explicit strict-evidence rule and the Phase 6 offline design:

| Not implemented | Why |
|---|---|
| Detail-page scraping | No detail page was ever visited in Phase 6; `DETAIL` capability is `Unknown` |
| Application-URL extraction | Never observed; `APPLICATION_URL` capability is `Unknown` |
| Native freshness filtering | No freshness parameter was ever attempted or observed; `NATIVE_FRESHNESS` is `Unknown` |
| Remote/work-model filtering (as a search input) | Never tested; `REMOTE_FILTER` capability is `Unknown` |
| Salary extraction beyond what a fixture demonstrates | Never observed on the real page; the parser *can* read `baseSalary` if a nested `item` object provides it (schema.org-standard defensive handling), but no fixture in this task actually populates it, and this is not claimed as a confirmed Hirist capability |
| Experience extraction | Never observed on the real page; not implemented at all (no field reads an experience-equivalent value) |
| Location extraction based on assumption | Only extracted from the nested `item.jobLocation` object, and only when unambiguously present as plain text — never derived from the query's own `location` input as a substitute |
| Undocumented query parameters | None added beyond the one path segment and the one, honestly-caveated `locations=` parameter |

## Pagination Implementation

- **First page:** the constructed search URL, no `page` parameter.
- **Next-page detection:** the directly-observed `<link rel="next"
  href="...">` tag, parsed defensively (two attribute orderings
  handled).
- **Domain safety:** a discovered "next" URL is **never followed** if
  its host differs from `www.hirist.tech` — offline-tested (fixture
  `next_link_off_domain.html`).
- **Loop protection:** every visited URL is tracked in a set within one
  `search()` call; a URL seen again stops pagination — offline-tested
  (fixture `pagination_self_loop.html`).
- **Maximum-page safety bound:** `_MAX_PAGES = 3`, a small, explicit,
  clearly-labeled constant. **Not evidenced by any live observation of
  typical result-set sizes** — this is a deliberate, conservative,
  placeholder value pending real evidence from a future live
  validation, exactly as the Phase 6 offline design flagged as an open,
  deferred decision. Offline-tested against a synthetic, never-ending
  "next page" sequence to confirm the bound is actually enforced.
- **Cross-page deduplication:** an in-`search()`-call safeguard on
  `(company, title)`, **in addition to, not instead of**, the existing,
  unmodified pipeline-level `discover_local.deduplicate()`.
- **Partial-failure tolerance:** a `BLOCKED`/`SOFT_BLOCK_OR_CHALLENGE`/
  `PARSE_FAILURE` result on the **first** page raises (query-level
  failure, matching `NaukriAdapter`'s own contract). The identical
  result on a **later** page stops pagination gracefully, keeping
  whatever was already successfully collected — offline-tested.

## Error/Status Model

| Condition | State | Adapter behavior |
|---|---|---|
| Real, non-empty JSON-LD `ItemList` | `VALID_RESULTS` | Returns parsed jobs |
| Valid page, `itemListElement: []` | `VALID_EMPTY_RESULT` | Returns `[]` |
| Hard-block phrase ("access denied", "request blocked", "403 forbidden") | `BLOCKED` | `AdapterBlockedError` (first page) / stop pagination (later page) |
| Challenge phrase ("captcha", "checkpoint", "security check", etc.) | `SOFT_BLOCK_OR_CHALLENGE` | `AdapterBlockedError` with `BlockReason.UNKNOWN_BLOCK` (first page) / stop pagination (later page) |
| Malformed/missing JSON-LD, or every entry unparseable | `PARSE_FAILURE` | `AdapterTimeoutError` (first page) / stop pagination (later page) |
| HTTP/network failure (raised by the fetcher) | n/a | `AdapterTimeoutError` propagates (first page) / stops pagination gracefully (later page) |

No new `AdapterStatus`/`BlockReason` enum values were added — every
mapping reuses the existing, unmodified vocabulary.

## Test Coverage

**New file:** `scripts/test_hirist_adapter.py` — 14 scenarios, zero
live network calls, using `data/fixtures/hirist/*.html` (10 synthetic
fixtures, each clearly labeled as such in its own header comment, none
a real capture):

1. Valid `ItemList` with jobs — parsed correctly across 2 pages,
   including nested-item field extraction (`job_url`, `posted_date`,
   `location`).
2. Empty `ItemList` — `VALID_EMPTY_RESULT`, zero jobs.
3. Malformed JSON-LD — `PARSE_FAILURE`.
4. Missing JSON-LD entirely — `PARSE_FAILURE`.
5. Missing optional fields — default to empty string/list, never
   fabricated.
6. Malformed job URL — entry still parsed via company+title; the bad
   URL is left empty, not passed through.
7. Duplicate jobs within one page — deduplicated by the adapter's own
   in-call safeguard.
8. `rel="next"` pagination — correctly extracted; an off-domain next
   link is never followed (extra safety check beyond the requested
   scenario).
9. Pagination loop protection — a self-referencing next link does not
   loop.
10. Maximum-page protection — bounded at `_MAX_PAGES=3` even against a
    pathological, never-ending "next page" sequence.
11. HTTP error handling — first-page failure propagates; later-page
    failure stops pagination gracefully, keeping earlier results.
12. Parser never invents unsupported fields — confirmed directly, plus
    confirmation that ambiguous delimiter cases (zero or multiple `" -
    "` occurrences) are rejected, never guessed.
13. Adapter remains `NOT_ENABLED` — status, empty capabilities, inert
    default fetcher, and `discover_from_sources()`'s zero-call
    guarantee against the real (not a stand-in fake) `HiristAdapter`,
    all directly verified.
14. `search_profile._default_sources()` remains exactly `['NAUKRI']`.

## Authorization Limitation (Unchanged, Explicitly Reaffirmed)

Nothing in this implementation step changes the authorization picture
established in Phase 6 Steps 1–2: `robots.txt` did not disallow the
observed jobs/search paths, but **explicit authorization for automated
data extraction remains unconfirmed** — Hirist's Terms of Service were
never read or verified. This implementation exists to be ready and
offline-tested, **not** to imply authorization has been resolved.

## Exact Enablement Status

```
HiristAdapter.status        = AdapterStatus.NOT_ENABLED   (unchanged)
HiristAdapter.capabilities  = frozenset()                 (unchanged)
```

**Deliberately left empty**, consistent with the Phase 6 offline
design's own conclusion (Section 2): no capability is marked confirmed
"until demonstrated the same way Naukri's were" — i.e. via the full
enablement gate (policy review, one live query, multi-query validation,
etc.), none of which has happened. Implementing the parser/pagination
logic offline does not, by itself, satisfy that gate.

## Live Requests Made in This Task

**Zero.** No network call, no browser launch, no Node/Playwright
process was invoked anywhere in this implementation or its test suite.
The adapter's own default fetcher (`HiristFetcher`) is structurally
incapable of a live request — its `fetch()` method raises
`NotImplementedError` unconditionally, before any network-capable code
would ever run.

## Production DB Safety

| | SHA-256 |
|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |

**Byte-identical.** The database was never opened for writing, and no
test in this task required persistence of any kind (this is pure
parsing/logic testing, entirely in-memory, no DB access at all).

## Regression Results

```
New dedicated test (scripts/test_hirist_adapter.py):           14/14 PASS
Shared architecture test (scripts/test_multi_source_adapter_architecture.py): 15/15 PASS (updated, see above)
Full standalone regression suite (42 files, including both):   PASS=42 FAIL=0
python3 -m py_compile across all scripts/*.py:                 PY_COMPILE_ALL_OK
```

`HiristAdapter.status`, `HiristAdapter.capabilities`, and
`search_profile._default_sources()` all independently re-verified
unchanged after the full suite run (see the JSON companion report for
exact values).

## Git / Changed-File Inspection

This repository has zero commits (`git log` → "does not have any
commits yet"), so a meaningful `git diff` against a prior baseline is
not possible — every file in the project is untracked from git's
perspective. Verified instead by direct inspection: the only files
touched in this task are exactly those listed under "Files Changed"
above; every file listed under "Not modified" was confirmed untouched
by direct review of this task's own edit history.

**Stopping here, per instruction. No live Hirist validation was
performed. Hirist was not enabled. The search worker was not modified
to execute Hirist.**
