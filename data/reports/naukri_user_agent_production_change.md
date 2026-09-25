# Naukri User-Agent Production Change

Smallest production-safe change implementing the live-validated finding
from `data/reports/naukri_headless_ua_isolation.md`: Naukri's automated
Playwright fetch now presents a headed-style User-Agent while remaining
genuinely headless, via Playwright's standard, documented context-level
`userAgent` option only. **No live Naukri request was made in this task.**

## 1. Exact Files Changed

| File | Change |
|---|---|
| `scripts/naukri_fetch_bridge.js` | Added `JOBOS_NAUKRI_USER_AGENT`-driven UA computation and passed it into the browser context via `userAgent:` |
| `config/jobos.env.example` | Documented `JOBOS_NAUKRI_USER_AGENT` (and, since the file was previously empty, the pre-existing but previously undocumented `JOBOS_BROWSER_HEADLESS`) |
| `scripts/test_naukri_user_agent_config.py` | **New** — offline regression test, 12 checks, no network requests |

No other file was created, modified, or deleted.

## 2. Exact Configuration Added

Environment variable: **`JOBOS_NAUKRI_USER_AGENT`**

```js
const DEFAULT_NAUKRI_USER_AGENT =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36';

const naukriUserAgent = process.env.JOBOS_NAUKRI_USER_AGENT || DEFAULT_NAUKRI_USER_AGENT;
```

Behavior:
- **Unset** -> the validated default UA is used.
- **Set to a non-empty value** -> used verbatim (passed through exactly, no transformation).
- **Set to an empty string (`""`)** -> `||` treats it as falsy, so it also
  falls back to the validated default. This was a deliberate, documented
  choice (per instruction 5: "choose the safest deterministic behavior...
  do not silently create malformed browser configuration") — an empty
  User-Agent string is never sent to Naukri.

## 3. Exact Default UA

```
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36
```

This is the exact string directly measured from a genuinely headed
Chromium launch in `data/reports/naukri_browser_runtime_diagnostic.md`,
and the exact string used in the single successful live isolation
experiment in `data/reports/naukri_headless_ua_isolation.md` (HTTP 200 /
VALID_RESULTS / 20 jobs, native headless mode).

## 4. How the UA Is Applied

Via Playwright's standard, documented context-level option only:

```js
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  userAgent: naukriUserAgent,
});
```

This is the **only** change to context/page/browser behavior. Nothing
else was added:
- No stealth library, no fingerprint-spoofing framework.
- No JavaScript modification of `navigator.*` properties.
- No WebGL alteration.
- No pointer/touch/hover behavior alteration.
- No CAPTCHA bypass.
- No proxy rotation.
- No cookie manipulation.
- No other HTTP header (`Accept`, `Accept-Language`, `Sec-Fetch-*`, etc.) touched.

The setting is read and applied **only** inside `naukri_fetch_bridge.js`
(the one and only Playwright launch point in the real adapter/worker
path). It has no effect on any other adapter — there is currently only
one real adapter (Naukri); `MockJobSourceAdapter` and every future adapter
are entirely unaffected, since `JOBOS_NAUKRI_USER_AGENT` is referenced
nowhere else in the codebase (verified by test F, below).

## 5. Headless Remains Default — Confirmed

```js
const headless = process.env.JOBOS_BROWSER_HEADLESS !== '0';
```

Byte-identical to before this change. `chromium.launch({ channel:
'chromium', headless })` is unchanged. Default automated behavior remains
headless; `JOBOS_BROWSER_HEADLESS=0` remains the only, unchanged,
interactive-debugging escape hatch.

## 6. Chromium Channel Remains Unchanged — Confirmed

`channel: 'chromium'` in the `chromium.launch(...)` call is untouched —
still forces the full "Google Chrome for Testing" binary in both headless
and headed mode, exactly as before this change.

## 7. No Stealth / Fingerprint-Spoofing Mechanisms Added — Confirmed

The only change to browser behavior anywhere in this task is the single,
standard, documented Playwright `userAgent` context option described
above. No stealth plugin, anti-detection library, `navigator.webdriver`
patch, WebGL override, timezone/locale spoofing, canvas fingerprint
randomization, or any other evasion mechanism was added, considered, or
referenced in the implementation.

## 8. Tests Added

`scripts/test_naukri_user_agent_config.py` — 12 checks, all offline, all
real browser launches (where used) target `about:blank` only, zero
network requests:

| # | Check | Type |
|---|---|---|
| 1 | Validated default UA string present in source | static |
| 2 | `JOBOS_NAUKRI_USER_AGENT \|\| DEFAULT_NAUKRI_USER_AGENT` fallback expression present | static |
| 3 | `userAgent: naukriUserAgent` passed into `newContext(...)` | static |
| 4 | `JOBOS_BROWSER_HEADLESS` expression unchanged (headless default) | static |
| 5 | `channel: 'chromium'` unchanged | static |
| 6 | Existing `DIAGNOSTIC` stderr line still present | static |
| 7 | `process.stdout.write(html)` stdout contract unchanged | static |
| 8 | `JOBOS_NAUKRI_USER_AGENT` referenced only in `naukri_fetch_bridge.js` (not leaked into any other adapter/worker file) | static |
| 9 | No production file (`naukri_fetcher.py`, `naukri_adapter.py`, `naukri_parser.py`, `search_worker.py`, `source_registry.py`, `source_adapter.py`, `naukri_fetch_bridge.js`) references the offline diagnostic probe | static |
| A | **Default behavior**: `JOBOS_NAUKRI_USER_AGENT` unset -> real browser context's `navigator.userAgent` equals the validated default, verified via a live `about:blank` launch | dynamic |
| B | **Explicit override**: custom UA (`TestAgent/9.9 (isolation-test)`) passed through exactly, verified the same way | dynamic |
| C | **Empty/malformed configuration**: `JOBOS_NAUKRI_USER_AGENT=""` -> real context's `navigator.userAgent` equals the validated default (never an empty UA), verified the same way | dynamic |

Every dynamic check also asserts `headless === true` was preserved for
that launch, directly covering requirement D from the task (headless
default unchanged) at the same real-launch call site as the UA check.

## 9. Full Test Result

```
Full standalone suite (38 files, including the new test):
PASS=38 FAIL=0

python3 -m py_compile across all scripts/*.py:
PY_COMPILE_ALL_OK
```

`scripts/test_naukri_user_agent_config.py` output:

```
NAUKRI USER-AGENT CONFIGURATION TEST
=======================================
PASS: 3 -> validated default UA string is present in source
PASS: 1/4/A -> JOBOS_NAUKRI_USER_AGENT falls back to the default via '||'
PASS: 2/7 -> userAgent is passed into the Naukri browser context
PASS: D -> headless default (JOBOS_BROWSER_HEADLESS-driven) unchanged
PASS: E -> channel: 'chromium' unchanged
PASS: G -> existing DIAGNOSTIC stderr line still present
PASS: G -> stdout contract (raw HTML only) unchanged
PASS: F -> JOBOS_NAUKRI_USER_AGENT is referenced only in naukri_fetch_bridge.js
PASS: H -> no production file references naukri_browser_runtime_probe.js
PASS: A: JOBOS_NAUKRI_USER_AGENT unset -> validated default used
       effective navigator.userAgent = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36'
PASS: B: explicit override passed through exactly
       effective navigator.userAgent = 'TestAgent/9.9 (isolation-test)'
PASS: C: empty string -> deterministic fallback to validated default (never an empty UA)
       effective navigator.userAgent = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36'

All Naukri User-Agent configuration tests passed.
```

Also confirmed unchanged and still green: `test_naukri_headless_config.py`
(headless default), `test_advisory_health_check.py` (advisory
architecture), `test_max_job_age_days.py` (freshness constraint),
`test_scoring.py` / `test_tracker.py` / `test_adapter_pipeline.py` /
`test_registry_pipeline.py` / `test_query_orchestrator.py` (scoring,
eligibility, ranking, dedup, query planning, worker behavior) — none of
this task's changes touched any of that logic.

## 10. Production DB SHA / Rows Before and After

| | SHA-256 | Row counts |
|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | identical |

**Byte-identical.**

## 11. No Live Naukri Request Was Made

**Confirmed: zero live Naukri or network requests were made anywhere in
this task.** Every browser launch performed while testing (the 3 dynamic
checks in `test_naukri_user_agent_config.py`, plus the pre-existing
dynamic checks in `test_naukri_headless_config.py` that this task's test
run also re-exercised) navigated to `about:blank` only. No production DB
write occurred, no scoring/eligibility/ranking/dedup/freshness/query-
planning/worker logic was touched or exercised beyond what the pre-existing
test suite already covers, and no browser was ever launched against any
`naukri.com` URL.

Per instruction, the live validation of this change is **not** performed
in this task and is not scheduled — it requires its own separate,
explicit authorization.
