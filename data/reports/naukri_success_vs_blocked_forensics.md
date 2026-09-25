# Naukri Success-vs-Blocked Differential Forensics

**Scope:** pure offline comparison of existing artifacts. **Zero new Naukri/
network requests. Zero browser launches.** No code changes were made — none
were identified as necessary for this comparison.

## Runs Being Compared

- **A. Most recent successful run** (real listings returned): the first
  5-query live validation, `data/reports/naukri_5_query_validation.md`/`.json`
  — headed mode, before any headless/`jobAge` feature existed.
- **B. Latest blocked run without `jobAge`:**
  `data/reports/naukri_single_query_no_jobage_live_validation.md`/`.json`.
- **C. Immediately preceding blocked run with `jobAge=3`:**
  `data/reports/naukri_advisory_health_check_live_validation.md`/`.json`.

**Headline finding, established as FACT:** Run A's first query (role=
"Infrastructure Engineer", location="Bangalore") used the **exact same base
URL** as Run B — `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore`
— and returned **`VALID_RESULTS`, 20 real job listings**. The identical URL,
for the identical role/location, later returned **`BLOCKED`, HTTP 403, 0
listings** in Run B. This is the single most direct, literal same-URL
comparison available in this project's history.

## 1. Browser Comparison

| Attribute | Run A (successful) | Runs B/C (blocked) | Label |
|---|---|---|---|
| Playwright package version | `1.63.0` | `1.63.0` (unchanged — no `npm install`/version change performed at any point this session) | FACT |
| Chromium revision | `1243` (present in `~/Library/Caches/ms-playwright/` since `19 Sep 11:17`, before any of this session's recorded live tests) | `1243` (confirmed via process evidence in Runs B/C) | INFERENCE (same revision very likely used throughout — no re-install occurred — but Run A predates this session's process-evidence instrumentation, so it was never directly captured for Run A itself) |
| Chromium channel | none specified in code at the time (`chromium.launch({headless: false})`, no `channel` key at all) | `channel: 'chromium'` (added later, forces the full binary) | FACT (reconstructed from this session's own sequential edit history — see §4; **no git history exists to verify this independently**, confirmed via `git log` returning "no commits yet") |
| Headless/headed | **headed** (`headless: false`, hardcoded) | **headless** (`headless: true`, `JOBOS_BROWSER_HEADLESS` unset → default) | FACT |
| Actual executable | not captured (no process-evidence instrumentation existed yet) | `chromium-1243/.../Google Chrome for Testing.app` — full binary, 0 `headless_shell` observations | FACT (Runs B/C only) |
| Launch arguments | not captured | full Chromium's standard Playwright launch args plus `--headless` (captured via `ps aux`) | FACT (Runs B/C only) |
| Context options | `{viewport: {width:1440, height:900}}` — unchanged across every run this project has ever made | same | FACT (from code inspection — unchanged since this file's creation) |
| User agent | not captured, no explicit override in code (then or now) | same | NOT DETERMINABLE (value); FACT (no explicit override exists in code, either time) |
| Locale / timezone | not set explicitly, then or now | same | FACT (no code anywhere sets these) |
| Cookies / storage state | none — every fetch is a fresh, isolated `browser.launch()`→`context.newPage()`→`browser.close()` cycle | same, unchanged | FACT |
| JavaScript / automation-related settings | no stealth/evasion settings, no `navigator.webdriver` patching, anywhere, then or now | same | FACT |

## 2. HTTP/Request Comparison

| Attribute | Run A | Runs B/C | Label |
|---|---|---|---|
| Exact URL (query 1) | `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore` | Run B: identical. Run C: same + `?jobAge=3` | FACT (Run A's exact URL was not logged by that run's own instrumentation, since URL-capture didn't exist yet; **reconstructed** via `_build_search_url()`'s unchanged, deterministic slug logic — INFERENCE, very high confidence, not a captured log line) |
| Query parameters | none (feature didn't exist) | Run B: none. Run C: `jobAge=3` | FACT/INFERENCE as above |
| Headers (all types) | never captured | never captured | NOT DETERMINABLE (both) |
| Cookies | none sent (no mechanism exists) | none sent | FACT |
| Redirects | not captured | none observed (final URL == requested URL) | NOT DETERMINABLE (Run A); FACT (Runs B/C) |
| HTTP status code | **not captured** (diagnostic added only after Run A) | **403** (Runs B and C both) | NOT DETERMINABLE (Run A); FACT (Runs B/C) — **cannot be compared** |
| Response URL | not captured | identical to requested URL | NOT DETERMINABLE (Run A); FACT (Runs B/C) |
| Timing | not captured precisely (this session never measured per-request duration in any run) | same limitation | NOT DETERMINABLE (both) |

## 3. Request Sequence Comparison

| Attribute | Run A | Runs B/C |
|---|---|---|
| Homepage health check | Succeeded (`reachable=True`) | Failed (`UNKNOWN_BLOCK`, HTTP 403 in Runs B/C) |
| Search requests | 5 (all 5 planned queries attempted, pre-advisory architecture didn't matter since health check passed) | 1 each (advisory architecture; attempted despite failed health check) |
| Detail-page requests | 100 (rate-limited, 3s apart, across all 5 search results) | 0 (search itself was blocked before any listing existed to fetch a detail page for) |
| Total live requests in that run | 106 | 2 each (1 health + 1 search) |
| Shared browser/session across requests | **No** — confirmed unchanged: every fetch, in every run ever made, is an independent `subprocess.run(["node", ...])` invocation, its own fresh browser launch/close | Same |
| Fetcher/bridge implementation | `naukri_fetcher.NaukriFetcher` → `naukri_fetch_bridge.js` — the **same files**, just with different launch options at each point in time (see §4) | Same files |

**Cumulative session-level context (FACT, directly countable from the
existing reports):** by the time Runs B/C occurred, this session had already
made a large number of prior live Naukri requests across several tasks — a
20-job-audit's underlying live run, Run A's own 106 requests, plus two
earlier blocked homepage-only attempts (1 request each) before the advisory
architecture existed. **Run A occurred earliest in this cumulative
sequence; Runs B/C occurred latest.** This is a genuine, countable
difference, not a guess.

## 4. Code/Version State Comparison

**No git history exists in this repository** — confirmed via `git log`
(`fatal: your current branch 'main' does not have any commits yet`). The
following is reconstructed from this session's own sequential record of
edits (first-hand, since every edit was made within this same session), not
from an independently-verifiable diff:

| File | State during Run A | State during Runs B/C |
|---|---|---|
| `scripts/naukri_fetch_bridge.js` | `chromium.launch({ headless: false })` — no `channel`, no env var, no HTTP-status/diagnostic logging | `chromium.launch({ channel: 'chromium', headless })`, `headless` driven by `JOBOS_BROWSER_HEADLESS` (default `true`), plus a `DIAGNOSTIC` stderr line (status/URL) added between Run C and Run B |
| `scripts/naukri_fetcher.py` | forwarded stderr only on failure | also forwards stderr on success (added between Run C and Run B) — this is exactly why Run B captured HTTP 403/matched-phrase and Run C did not |
| `scripts/naukri_parser.py` | `classify_search_page()`'s `BLOCKED` detail was generic | now includes the exact matched phrase (added between Run C and Run B) |
| `scripts/source_registry.py` / `scripts/source_adapter.py` | `health_check()` was an unconditional hard gate | advisory opt-in (`health_check_is_advisory`) added before Run C; unchanged for Run B |
| `scripts/naukri_adapter.py` (`_build_search_url`) | no `jobAge` parameter support (feature didn't exist) | supports `max_job_age_days` → `jobAge=N`; role/location slug logic **unchanged** throughout |

**Nothing in `naukri_parser.py`'s classification/block-detection LOGIC
changed between any of these runs** beyond the diagnostic-detail
enhancement (§ above) — the same 5-phrase `_UNKNOWN_BLOCK_PHRASES` list,
unmodified, is what matched in Run B/C.

## 5. Naukri Response Comparison

| Attribute | Run A (query 1) | Run B | Run C |
|---|---|---|---|
| Classifier state | `VALID_RESULTS` | `BLOCKED` | `BLOCKED` |
| HTTP status | not captured | 403 | not captured |
| Matched block phrase | n/a (not blocked) | `'access denied'` | not captured (generic detail only) |
| Job links found | 20 | 0 | 0 |
| Page title / body text | not logged (only link-extraction results were) | not persisted (HTML not saved) | not persisted |
| Redirects | not captured | none | not captured |

## Does Run A's Evidence Allow Offline Reproduction of Its Exact Request Conditions?

**Partially.** The **launch configuration** is fully known and trivially
reproducible offline (headed, no channel override — this determines which
binary Playwright would use). **What is missing and cannot be reproduced
from captured evidence:** the exact HTTP headers, User-Agent string,
Accept/Accept-Language/Referer/Sec-Fetch-* values, and precise timing —
none of these were ever logged by Run A's own instrumentation, and no code
in this project has ever explicitly set them (in either era), so their
values were always whatever Playwright/Chromium's own internal defaults for
that exact binary happened to be — plausible to assume "the same defaults
for the same Chromium revision," but **not verified**, since they were
never captured to compare against.

## Hypothesis Evaluation

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| A | Current full-Chromium **headless mode** causes the 403 | **SUPPORTED** (but confounded — see E) | The exact same URL, same role/location, succeeded under headed mode (Run A) and failed under headless mode with the full binary confirmed (Runs B/C). This is genuine, direct, same-URL evidence — not mere plausibility. However, it cannot be isolated from E (see below); headless and "later in the session" changed together. |
| B | Chromium/headless-shell **binary mismatch** causes the 403 | **CONTRADICTED** | `channel: 'chromium'` (full binary, 0 headless-shell observations) was confirmed in use for both Run B and Run C's predecessor test; both still blocked. |
| C | Request **headers/context** differ materially from Run A | **NOT DETERMINABLE** | No header/context data was ever captured for any run, successful or blocked. Cannot compare what was never recorded. |
| D | Request/session/**cookie state** differs materially | **CONTRADICTED** | No cookie/session/storage-state mechanism exists anywhere in this codebase, in either era. Every fetch, in every run, has zero persisted state — there is nothing that could differ. |
| E | **Request sequence or volume** differs materially | **SUPPORTED** | Countable, factual difference: Run A occurred earliest in this session's cumulative Naukri-request timeline (106 requests in that run alone); Runs B/C occurred after substantially more cumulative requests had already been made across the session. This is a genuine, evidenced difference, not speculation — though, symmetrically with A, it cannot be isolated as the sole cause either. |
| F | **URL/query construction** causes the 403 | **CONTRADICTED** | The byte-identical base URL (no `jobAge`) succeeded in Run A and was blocked in Run B — the construction itself did not change between a success and a failure for the exact same string. |
| G | Naukri currently blocks this automation path **independently of `jobAge`** | **SUPPORTED** | Directly demonstrated by the prior controlled A/B test (Run B vs. Run C): identical outcome (403, BLOCKED, 0 jobs) with and without `jobAge=3`. |
| H | Evidence is **insufficient to identify the cause** | **NOT DETERMINABLE** | Narrowly true only for the *final* root-cause mechanism: B, D, and F are conclusively ruled out, and G is established — but A and E remain genuinely confounded with each other and cannot be separated by the evidence collected so far. This is a narrowed uncertainty (down to two live candidates), not a total absence of findings. |

## Safest Next Single Live Experiment

**Goal: separate Hypothesis A (headless-mode block) from Hypothesis E
(cumulative volume/timing), since these are the only two remaining,
mutually-confounded candidates.**

**Recommended experiment — Option 1 (most informative, requires a
deliberate, one-time headed exception):**

- **Exact query:** role="Infrastructure Engineer", location="Bangalore"
  (identical to Runs B/C, for maximum comparability) — no `jobAge` (already
  ruled out as a variable).
- **Exact browser mode:** **headed**, via the existing, already-documented
  `JOBOS_BROWSER_HEADLESS=0` escape hatch — a deliberate, one-time,
  human-approved diagnostic exception, **not** a change to the automated
  default (which remains headless).
- **Exact configuration:** everything else identical — `channel: 'chromium'`
  (irrelevant in headed mode, since headed can only use the full binary
  anyway), advisory health check unchanged, no code changes.
- **Exact request URL:** `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore`
  (same as Run B).
- **Variable being changed:** headless → headed (isolates A from E, since
  cumulative session request volume continues to increase regardless of
  which mode is used — if headed succeeds NOW, despite the session's
  accumulated volume, that weakens E relative to A; if headed ALSO fails
  now, that weakens A relative to E).
- **What must remain identical:** query, URL, advisory architecture,
  `channel` setting, no retries, exactly one request.
- **Expected outcomes and what each would establish:**
  - **VALID_RESULTS (success):** strong evidence AGAINST E as the dominant
    cause (cumulative volume clearly did not prevent success) and FOR A
    (headless mode specifically is associated with the block). Would also
    finally allow inspecting real jobAge-tagged results for functional
    verification in a **separate**, later, deliberately-scoped test.
  - **BLOCKED (403 again):** strong evidence AGAINST A as the (sole) cause
    — the one variable that changed (headless→headed) did not change the
    outcome — pointing more toward E (or a still-undiscovered factor) as
    dominant.
- **Explicit caveat:** this experiment requires a visible browser window to
  appear, once, as a deliberate diagnostic exception — this must be your
  explicit choice, not an automated default, and is not executed by this
  report.

**Recommended experiment — Option 2 (stays fully headless, less
informative):**

- Same query, but under headless mode, with a **substantially longer
  cooldown period** before attempting it (to test whether time-based
  request-volume decay, rather than mode, explains recovery). This does not
  cleanly separate A from E either (a success would be consistent with
  either "headless was never the issue" or "enough time passed to reset
  rate-limiting"), but avoids any visible window. Weaker information gain
  than Option 1, offered only if a headed exception is unacceptable to you
  under any circumstance.

**Neither experiment is executed by this report.**

## Readiness for Multi-Query Live Validation

**Not ready.** Per your own stated expectation, this forensic comparison
does **not** establish a defensible reason to believe the current 403 can
be meaningfully tested with a *multi-query* experiment right now. It
establishes exactly the opposite: the open question (A vs. E) is best
resolved by **one** more carefully isolated single query, not by more
volume against a source already confirmed blocking on its most recent
attempts. Running 5 or 10 queries now would add exactly the kind of
additional cumulative request volume that Hypothesis E already implicates,
without providing any additional discriminating power beyond what the
single recommended experiment above would provide.

## Production DB Safety

| | SHA-256 | Row counts |
|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | identical |

**Byte-identical. No live Naukri/network request was made during this
investigation. No code changes were made.**
