# Naukri Health-Check Architecture — Investigation and Change

**Scope:** offline investigation, followed by an evidence-justified,
minimal implementation. **Zero Naukri network requests, zero browser
launches** were made while producing this report or the code change below.

## 1. Current Flow (before this task)

```
search_worker.run_once()
  -> process_queue_item()
    -> source_registry.discover_from_sources(queries)
        health = adapter.health_check()          <-- ONE call, before ANY query
        if not (health.reachable and health.block_reason == NONE):
            mark source BLOCKED, skip ALL queries, continue
        else:
            for each query: adapter.search(query)
    -> naukri_adapter.NaukriAdapter
        health_check() -> fetch https://www.naukri.com/ -> detect_block_reason() (NONE/UNKNOWN_BLOCK/TIMEOUT only)
        search(query)  -> fetch https://www.naukri.com/{role}-jobs-in-{location}[?jobAge=N]
                       -> classify_search_page() -> VALID_RESULTS / VALID_EMPTY_RESULT /
                          BLOCKED / SOFT_BLOCK_OR_CHALLENGE / PARSE_FAILURE
                       -> raises AdapterBlockedError (BLOCKED, SOFT_BLOCK_OR_CHALLENGE)
                          or AdapterTimeoutError (PARSE_FAILURE), or returns jobs normally
```

`health_check()` and `search()` share the identical fetch mechanism
(`NaukriFetcher.fetch()` → `node naukri_fetch_bridge.js <url>`, same headless
config, same `channel: 'chromium'`) but request **different URLs**, and
there is **no shared browser session** between any two fetches — every fetch
is its own fresh, isolated Playwright launch/close cycle (confirmed in the
prior diagnosis task, unchanged since).

`health_check()` has no side effects beyond the one HTTP request it makes.
It is called exactly **once per source per call** to
`discover_from_sources()` — never once per query.

`search()` already has its **own, complete, independent** availability
signal: `classify_search_page()`'s 5-state classifier, plus
`AdapterBlockedError`/`AdapterTimeoutError` raised directly from `search()`
itself. It does not depend on `health_check()` in any way once it is
reached.

## 2. Search Classification — Sufficiency Assessment

`naukri_parser.classify_search_page()` already distinguishes:

- `VALID_RESULTS` — real job listings found.
- `VALID_EMPTY_RESULT` — a genuinely empty but structurally-confirmed
  search-results page (shell markers present).
- `BLOCKED` — a known access-denied/WAF phrase.
- `SOFT_BLOCK_OR_CHALLENGE` — reachable, not a recognized results page,
  shows challenge/interstitial evidence.
- `PARSE_FAILURE` — none of the above; unrecognized structure.

This is **sufficient** to make the actual search response the authoritative
per-query health signal: every outcome a request to the search URL could
produce already maps to an explicit state, and `NaukriAdapter.search()`
already converts every one of those states into the correct existing
exception type or return value (`AdapterBlockedError`, `AdapterTimeoutError`,
or a normal job list). No change to `naukri_parser.py` was needed or made.

## 3. Evidence From Prior Live Runs

Reviewing every live run this project has performed:

| Run | health_check() | search() attempted? | search() outcome |
|---|---|---|---|
| First live single-query test (headed) | Succeeded | Yes | VALID_RESULTS (0 raw jobs that day) |
| Naukri classifier hardening live test (headed) | Succeeded | Yes | PARSE_FAILURE (a real defect, since fixed) |
| Ranking-integration live test (headed) | Succeeded | Yes | VALID_RESULTS, 20 real jobs |
| First 5-query test (headed) | Succeeded | Yes (×5) | All 5 VALID_RESULTS, 100 raw jobs |
| 5-query + 3-day filter, headless (headless-shell binary) | **Failed** | No | N/A |
| 5-query + channel='chromium', headless | **Failed** | No | N/A |

**Direct answer to the key question: this project has never once
demonstrated a run where `health_check()` failed AND `search()` was ALSO
attempted to see whether it would have succeeded.** Every prior success had
both succeed together (so we don't know if search() succeeded *because of*
or *independently of* the homepage being reachable); every recent failure
was a homepage-only block that pre-emptively prevented `search()` from ever
running. There is **no evidence either way** that a homepage block predicts
a search-page block for this source — the current architecture has simply
never allowed that data point to be collected. This is not an inference
beyond the evidence; it is the literal absence of the one data point needed.

## 4. Search URL Construction — Verified Offline

`naukri_adapter._build_search_url()`, called with
`SearchQuery(role="Infrastructure Engineer", location="Bangalore", max_job_age_days=3)`,
produces (confirmed by `scripts/test_advisory_health_check.py`, case 9,
executed during this task — no live request):

```
https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore?jobAge=3
```

`jobAge=3` is present exactly as expected. This is unaffected by
`channel='chromium'`/headless (those only affect *how* the fetch happens,
never the URL string itself) and unaffected by the health-check change made
in this task (§8).

## 5. Health-Check Design Options — Evaluated

| Option | Correctness | Naukri requests | Block/challenge handling | Background-compatible | Rate-limit effect | Reduces unnecessary requests | Worker-compatible |
|---|---|---|---|---|---|---|---|
| **A. Mandatory gate (status quo)** | Currently prevents collecting the one missing data point (§3) | 1 (homepage) if it fails; 1 + N if it passes | Homepage-only; never sees search-page state when it fails | Yes | No extra requests, but also never confirms search viability | N/A | Fully compatible (current) |
| **B. Advisory health check; search() self-classifies** | **Directly closes the evidence gap in §3** | 1 (homepage, for diagnostics) + up to 5 (search), even if homepage failed | Full 5-state classifier reached for every query | Yes | Slightly more requests when homepage fails and search also fails (homepage + N failed searches, each caught individually) | No — but see §6 | Fully compatible: search()'s own AdapterBlockedError already stops the remaining queries for that source, exactly as gating always has |
| **C. Replace homepage check with a real search-page check** | Would answer availability precisely, but consumes a query slot just to test health, and duplicates what search() itself would do anyway | 1 (a search-shaped URL) + up to 5 | Full classifier | Yes | Same or more requests than B for no extra benefit | No | Requires re-plumbing which query "is" the health check |
| **D. Remove health_check entirely for Naukri** | Loses the (currently unused, but free) diagnostic homepage signal entirely | 5 (search only) | Full classifier | Yes | Fewer requests only if homepage would have failed | Yes, marginally | Would require deleting/no-op'ing health_check(), more invasive than B |
| **E. health_check for diagnostics only, never blocking** | **Equivalent in effect to B**, framed as "always call, never gate" rather than "call, but ignore its gating for one adapter" | Same as B | Same as B | Yes | Same as B | Same as B | Same as B |

**B and E are functionally the same outcome** — the difference is only
whether the "never gate" behavior is a global registry default (E) or an
explicit per-adapter opt-in (B). **B is the smaller, safer change**: it
preserves the existing hard-gate default for every other adapter (including
future ones) and requires NaukriAdapter to explicitly declare, with its own
documented justification, that its homepage check is not proven predictive
— rather than silently changing behavior for every source at once.

## 6. Rate-Limiting Consideration (§6 of the task)

This session has made a meaningful number of live Naukri requests across
recent tasks (a 20-job audit's captured data, an earlier successful
5-query run of 106 requests, and two recent single-homepage-request
attempts that were blocked before any query). **No evidence in this
codebase supports a specific claim about Naukri's rate-limiting
thresholds** — this is noted explicitly rather than guessed at, per
instruction. What the evidence does show: the two most recent homepage-only
attempts both failed, while the two live runs before them (also homepage
checks, just followed by real queries) succeeded — consistent with, but not
proof of, a rate-limiting/reputation explanation (already flagged as an
open, unresolved possibility in the prior diagnosis report). The advisory
change does not add a *guaranteed* extra request in the case that matters
most (homepage failing) — it converts "1 wasted homepage request, 0 signal"
into "1 homepage request (for diagnostics) + real per-query signal," which
is more informative per request made, not more requests than necessary to
get an answer.

## 7. Recommended Architecture (implemented)

**Adopted: Option B, as a minimal, opt-in adapter flag** —
`JobSourceAdapter.health_check_is_advisory` (default `False`), set to
`True` only on `NaukriAdapter`, with `source_registry.discover_from_sources()`
updated to respect it.

This was **not** adopted merely because it "sounds cleaner": it directly
targets the specific, evidenced gap in §3 (this project has never observed
the health-check-fails/search-succeeds data point, because the architecture
has never allowed it to be observed), uses machinery (`classify_search_page()`,
`AdapterBlockedError`, `AdapterTimeoutError`) that is already complete and
already tested, and changes behavior for **exactly one adapter**, leaving
every other adapter (present and future) on the existing, safe default.

## 8. Exact Code Changes

1. **`scripts/source_adapter.py`** — added
   `JobSourceAdapter.health_check_is_advisory = False` (class attribute,
   documented). No other change.
2. **`scripts/source_registry.py`** — `discover_from_sources()`: the
   pre-flight-health-check gate now checks
   `getattr(adapter, "health_check_is_advisory", False)`; when the adapter
   opts in and health failed, the function no longer sets `state.blocked`
   or skips the query loop — `health` is still recorded on `state.health`
   for diagnostics. Docstring updated to describe both branches. No other
   logic changed.
3. **`scripts/naukri_adapter.py`** — `NaukriAdapter.health_check_is_advisory = True`,
   with a docstring explaining the exact evidence-based justification
   (§3 of this report, referenced by name). `health_check()` and `search()`
   themselves are **completely unchanged**.

Nothing else was modified: `naukri_parser.py` (classifier), `score_job.py`,
`job_eligibility.py`, `freshness.py`, `job_ranking.py`, `query_planner.py`,
`search_submission.py`, `naukri_fetch_bridge.js` (headless/channel
configuration) are all byte-for-byte unchanged from the prior task.

## 9. Test Results

- New file: `scripts/test_advisory_health_check.py` — all 10 required
  scenarios plus 2 explicit backward-compatibility checks, **all pass**,
  fully offline (test-local fake adapters, no network, no browser launch).
- Full existing standalone suite: **36/36 pass** (35 pre-existing + 1 new).
- `py_compile`: clean across all changed and unchanged files.
- `pytest`: **not installed** in this environment (confirmed via explicit
  check, not assumed or falsely claimed).

## 10. Remaining Unknowns

- Whether the actual Naukri search URL is reachable when the homepage is
  blocked is **still unverified** — this task deliberately made no live
  request, per explicit instruction. The next live validation is exactly
  what will answer this.
- Whether `?jobAge=3` is honored by Naukri remains unverified for the same
  reason.
- The rate-limiting/reputation hypothesis from §6 remains open and
  unconfirmed.
- Whether `SOFT_BLOCK_OR_CHALLENGE` (mapped to `AdapterBlockedError` in
  `naukri_adapter.py`, same as `BLOCKED`) or `PARSE_FAILURE` (mapped to
  `AdapterTimeoutError`) will actually be the observed outcome if Naukri's
  search page also blocks is unknown until the next live test.

## 11. Precise Plan for the Next Live Validation

1. Fresh isolated temp DB, Saroj's confirmed profile, `max_job_age_days=3`,
   exactly 5 queries via the existing deterministic planner — unchanged
   from the previously-built validation scripts.
2. `JOBOS_BROWSER_HEADLESS` left unset (headless default, `channel='chromium'`
   already in place) — unchanged.
3. Run `search_worker.run_once()` exactly once. With the advisory change
   now in place, `health_check()` will still run and be recorded, but even
   if it reports blocked, **all 5 queries will now actually be attempted**
   — giving us, for the first time, real evidence about search-page
   availability independent of the homepage result.
4. Capture, per query: the actual requested URL (confirming `?jobAge=3`),
   `classify_search_page()`'s state, raw/normalized/eligible counts, and
   freshness-age evidence for `jobAge=3` — exactly the instrumentation
   already built in the prior (blocked) live-test driver scripts.
5. If real results come back: proceed with the full freshness/eligibility/
   scoring/dedup/accounting audit exactly as previously planned. If search()
   itself also reports BLOCKED/SOFT_BLOCK_OR_CHALLENGE: that is now a
   genuine, direct data point about the search page specifically (not a
   homepage proxy), to report and evaluate on its own terms — not a reason
   to further modify the architecture without new evidence.
6. **This live validation is explicitly NOT run as part of this task** —
   per instruction, awaiting your authorization to proceed.
