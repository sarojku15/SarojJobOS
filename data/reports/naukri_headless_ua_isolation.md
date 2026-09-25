# Naukri Headless + User-Agent Isolation Experiment

**Single, controlled, one-time live experiment.** Exactly one live health
check + one live search query (role="Infrastructure Engineer",
location="Bangalore", no `jobAge` filter) were made against Naukri. No
retry, no second query, no multi-query validation. Production adapter code
(`scripts/naukri_adapter.py`, `scripts/naukri_fetcher.py`,
`scripts/naukri_fetch_bridge.js`) was never modified — this experiment ran
through a temporary, one-off copy of the fetch bridge and a temporary
fetcher wrapper, both located entirely outside the project directory (in
the session scratchpad), while reusing the real, unmodified production
`NaukriAdapter`, `classify_search_page()`, and `SearchQuery`.

## Experiment Configuration

| Setting | Value |
|---|---|
| `headless` | `true` (forced via `JOBOS_BROWSER_HEADLESS=1`) |
| `channel` | `'chromium'` (same full-Chromium binary as production) |
| Viewport | `{width:1440, height:900}` (identical to production) |
| `max_job_age_days` | omitted entirely (no `jobAge` parameter) |
| `health_check_is_advisory` | `true` (production default, unchanged) |
| **Only deliberate change** | `context.newContext({ userAgent: <string> })` set to the exact headed-mode UA string directly measured in the prior offline runtime probe: `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36` |
| Everything else | Byte-identical to `scripts/naukri_fetch_bridge.js` |

No headers (`Accept`, `Accept-Language`, `Sec-Fetch-*`), cookies, JS
properties, WebGL values, screen dimensions, or pointer/hover behavior were
touched. No stealth plugin, anti-detection library, CAPTCHA bypass, proxy
rotation, or cookie manipulation was used anywhere in this experiment.

## Pre-Flight Checks (all confirmed before the live request)

| Check | Result |
|---|---|
| 1. Native headless remains true | **CONFIRMED** — `JOBOS_BROWSER_HEADLESS=1` forced; live process capture (below) shows `--headless` in the actual command line |
| 2. `channel='chromium'` | **CONFIRMED** — temp bridge is a byte-for-byte copy of production's launch call except for the added `userAgent` option |
| 3. No `jobAge` parameter | **CONFIRMED** — `SearchQuery(role="Infrastructure Engineer", location="Bangalore")` with `max_job_age_days` left at its default `None`; `_build_search_url()` (real, unmodified production function) confirmed to produce `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore` with no query string |
| 4. Only the standard `userAgent` context option differs | **CONFIRMED** by direct diff of the temp bridge against `scripts/naukri_fetch_bridge.js` |
| 5. Advisory health-check remains enabled | **CONFIRMED** — `NaukriAdapter.health_check_is_advisory == True` (read directly from the unmodified production class) |
| 6. Production DB SHA before | **CONFIRMED**: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| 7. Production row counts before | **CONFIRMED**: candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |

**One infrastructure-only note (zero network impact):** the first driver
run failed before any browser launch or network contact, with
`Error: Cannot find module 'playwright'` — Node's module resolution
couldn't walk up from the scratchpad script's location to the project's
`node_modules`. This was fixed by setting `NODE_PATH` to the project's
`node_modules` directory (a pure environment/tooling fix, not a
change to the experiment's design, and not a live Naukri attempt — no
network request of any kind occurred on that failed attempt). The
single live experiment reported below is the only one that ever reached
Naukri's servers.

## A. Homepage Health Check — Live Result

| | Value |
|---|---|
| Reachable | `true` |
| Block reason | `NONE` |
| Detail | (empty — no block detected) |
| HTTP status (from bridge diagnostic) | `200` |
| Requested URL | `https://www.naukri.com/` |
| Final URL | `https://www.naukri.com/` (no redirect) |
| Effective `navigator.userAgent` (measured on the live page) | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36` — confirms the override was actually applied, not just requested |

**FACT/OBSERVED.**

## B. Search — Live Result

| | Value |
|---|---|
| Requested URL | `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore` |
| Final URL | identical (no redirect) |
| HTTP status | `200` |
| Classifier outcome | **VALID_RESULTS** (inferred conclusively: `adapter.search()` raised no `AdapterBlockedError`/`AdapterTimeoutError`, and returned 20 non-empty jobs — the only classifier state consistent with that combination in the unmodified production `search()` method) |
| Matched block phrase | none — no block detected |
| Jobs returned | **20** |
| Worker/adapter status | success, `error: null` |
| Effective `navigator.userAgent` on the search request and on every one of the 20 subsequent detail-page fetches | `...Chrome/153.0.0.0...` (headed-style, confirmed on every single request, never `HeadlessChrome`) |

**FACT/OBSERVED.**

## C. Browser Diagnostics — Live Process Capture (mid-flight, during the search request)

Directly captured from `ps aux` while the search request's browser was
open:

```
.../Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing
  ... --enable-unsafe-swiftshader --headless --hide-scrollbars --mute-audio
  --blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4
  --no-sandbox --user-data-dir=... --remote-debugging-pipe --no-startup-window
```

| | Value |
|---|---|
| Executable | `chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing` |
| Chromium version | `153.0.8010.12` (framework), `153.0.0.0` (browser build) |
| Channel | `chromium` |
| `--headless` flag present | **YES** — confirms this was a genuine native-headless launch, not headed |
| Visible UI | **false** (no window; `--headless` + `--no-startup-window` confirmed present) |
| Effective `navigator.userAgent` from the page | `...Chrome/153.0.0.0...` (headed-style string, confirmed applied) |

**This is the central fact of the experiment: the browser was genuinely
headless (FACT, directly observed in the live process command line) while
simultaneously reporting a headed-style User-Agent to Naukri (FACT,
directly observed via `page.evaluate()`).** These two facts coexisting is
exactly what isolates the User-Agent as the sole changed variable relative
to native headless mode.

## D. Results

- **Raw jobs:** 20 (see `data/reports/naukri_headless_ua_isolation.json` for the full list — company, title, location, JD text, extracted skills, experience).
- **Normalized jobs:** 20 (the adapter's own `parse_detail_page()` — real production code, unmodified — ran against all 20 detail pages successfully).
- **Unique jobs:** 20 (adapter-level URL dedup found zero duplicates).
- **Eligibility / scoring:** not run in this experiment — deliberately out of scope (this was a pure fetch/classify isolation test, not a scoring or DB-ingestion run; nothing was scored, matched, or written to any database).
- **Freshness:** posted dates present in raw text (e.g. "Posted: 2 days ago", "Posted: 3+ weeks ago") — not parsed by `freshness.py` in this experiment, since no `max_job_age_days` was requested and no ranking step was invoked.

## Direct Comparison

| Run | UA | Result |
|---|---|---|
| Native headless (prior validation) | `HeadlessChrome/153.0.0.0` | **HTTP 403 / BLOCKED / 0 jobs** |
| Headed (prior diagnostic) | `Chrome/153.0.0.0` | **HTTP 200 / VALID_RESULTS / 20 jobs** |
| **This experiment: headless + headed-style UA** | `Chrome/153.0.0.0` (headless=true otherwise) | **HTTP 200 / VALID_RESULTS / 20 jobs** |

The headless-plus-overridden-UA run's outcome is **indistinguishable from
the headed run's outcome** (same status code, same classifier state, same
job count, same URL) and **starkly different from the native-headless
run's outcome**.

## Interpretation

**Headless + headed-style UA succeeded.**

- **FACT/OBSERVED:** Changing only the standard Playwright context
  `userAgent` option — while every other measured property (headless flag
  itself, channel, viewport, all launch arguments, advisory architecture,
  query, URL) remained exactly as in the native-headless configuration —
  changed the live outcome from BLOCKED/403/0 jobs to VALID_RESULTS/200/20
  jobs.
- **This strongly supports the User-Agent as the relevant differentiator**
  identified in the prior offline runtime-diagnostic report.
- **Explicitly NOT claimed:** absolute causality in the sense of "Naukri's
  server logic checks the User-Agent header and only that." This single
  experiment isolates the *configured* variable (the UA string presented
  by this client) as sufficient, in this one trial, to reproduce the
  headed outcome from an otherwise-native-headless launch. It does not
  rule out that Naukri's actual server-side decision uses some other
  signal correlated with UA (e.g., a UA-keyed rate limit or reputation
  score that happens to track this exact string), nor does it establish
  that this result would replicate on a second attempt, at a different
  time, or against a different query — none of which this task is
  authorized to test further right now.
- **Hypothesis verdict:** the User-Agent-is-sufficient hypothesis is
  **SUPPORTED by this one live trial** (FACT/OBSERVED for this specific
  request; the generalization "will always work" remains NOT DETERMINABLE
  from a single data point).

## Is UA Normalization Sufficient?

**Sufficient in this one observed trial: yes.** No other variable was
changed, and the outcome matched the headed baseline exactly (status,
classifier state, and job count). Whether it is *reliably* sufficient
(across time, IP reputation drift, rate limiting, or Naukri-side changes)
is **NOT DETERMINABLE** from one request.

## Should Production Be Changed?

**No — not implemented, and not recommended yet, in this task.**

Reasons:
1. This is one successful trial. The project's own established discipline
   throughout this investigation has been to never generalize from a
   single live observation (the same discipline applied to the earlier
   headed-vs-headless discovery itself).
2. A production change to `scripts/naukri_fetch_bridge.js` would need its
   own explicit authorization, a decision on exactly how the UA value is
   sourced/maintained over time (Chromium versions drift; a hardcoded
   string will eventually go stale and itself become a fingerprinting
   signal), and a broader live validation (e.g., a small multi-query test
   run over time) before being trusted as the new default.
3. Per your explicit instruction: "Do NOT change production/default
   configuration."

## Safest Next Step

If you want to pursue this further, the safest next step would be a
**small, explicitly-authorized, separately-scoped validation** (not
performed here) that:
- Applies the same UA-override technique to the **existing production
  `naukri_fetch_bridge.js` file** (a real code change, requiring its own
  authorization and its own before/after test run), and
- Runs a modest number of live queries (e.g., the same 5-query scope
  already used earlier in this project) to confirm the result replicates
  beyond a single trial, and
- Decides how the UA string will be sourced going forward (e.g., derived
  from Playwright/Chromium's own version at runtime rather than a
  hardcoded literal, so it does not silently drift out of date).

**This report does not implement any of that.**

## Regression / Safety

- No production file was created, modified, or deleted. Both new files
  (`naukri_fetch_bridge_ua_diagnostic.js`, `naukri_headless_ua_isolation_driver.py`)
  live entirely in the session scratchpad, outside the project directory.
- No test suite changes were needed for this task (a live network
  experiment cannot be represented as an offline regression test; the
  existing `test_advisory_health_check.py`, `test_naukri_headless_config.py`,
  and the full adapter/classifier test suite already cover the code paths
  exercised here in isolation).
- Full existing standalone suite re-run after this experiment: **37/37
  pass**. `py_compile`: clean.

## Production DB Safety

| | SHA-256 | Row counts |
|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | identical |

**Byte-identical. Exactly one live health check and one live search query
were made. No follow-up or retry request was made regardless of the
(successful) outcome, per instruction.**
