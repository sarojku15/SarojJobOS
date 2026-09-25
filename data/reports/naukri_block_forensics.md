# Naukri Search-Page BLOCKED — Forensic Investigation

**Scope:** pure offline forensics against existing code, fixtures, and the
JSON dump already captured by the prior live validation
(`live_1query_advisory_dump.json`), plus three small, additive, offline-
tested diagnostic improvements (documented in §6). **Zero new Naukri/network
requests. Zero browser launches against Naukri.** One offline browser
launch was made against a **local fixture file** (`file://` URL) purely to
verify the new diagnostic code — documented explicitly in §6.

Every claim below is labeled **FACT** (directly observed in a captured
artifact), **INFERENCE** (a reasoned conclusion from facts, not itself
directly observed), or **NOT DETERMINABLE** (the existing evidence cannot
answer it).

## 1. What exact evidence caused the search page to be classified BLOCKED?

**FACT:** `classification_detail` from the live run was
`"block phrase matched (UNKNOWN_BLOCK)"` (captured in
`live_1query_advisory_dump.json`, before this task's diagnostic
improvement). This came from `naukri_parser.classify_search_page()`'s first
branch: `detect_block_reason(html)` returned `BlockReason.UNKNOWN_BLOCK`.

**FACT:** `detect_block_reason()` operates **only on human-visible text**
(page title + body, extracted via `_VisibleTextExtractor`, which explicitly
skips `<script>`/`<style>` content) — never on HTTP status code, headers,
URL, or raw markup/CSS/class names. The signal is exactly one of five
hardcoded phrases: `"access denied"`, `"you don't have permission to
access"`, `"request blocked"`, `"unusual traffic"`, `"edgesuite.net"`.

**NOT DETERMINABLE (exact phrase for this specific run):** the live-test
driver script logged only `html_len`, never the raw HTML, so **which of the
five phrases matched on this specific run is not recoverable** from the
captured artifacts. This is an honest gap, not glossed over.

**Diagnostic improvement made this task** (see §6): `classify_search_page()`
now includes the actual matched phrase in its `detail` string. Verified
offline against the real `blocked_access_denied.html` fixture: `detail`
now reads `"block phrase matched (UNKNOWN_BLOCK): 'access denied'"`. This
means the **next** live BLOCKED result will capture the exact phrase; this
one cannot be retroactively recovered.

**Source of signal:** response **body text** (title + visible body copy),
**not** HTTP status code, not headers, not URL — confirmed by direct code
inspection (§2 below expands on what is/isn't captured at all).

## 2. What HTTP/browser evidence is already captured for this request?

| Evidence | Captured? | Detail |
|---|---|---|
| HTTP status code | **NOT CAPTURED** (FACT, before this task) | `naukri_fetch_bridge.js` never called `response.status()` prior to this task's diagnostic addition. |
| Response/final URL (redirects) | **NOT CAPTURED** (FACT, before this task) | `page.url()`/`response.url()` was never called in the bridge script. |
| Redirect chain | **NOT CAPTURED** | No redirect inspection exists anywhere in this pipeline. |
| Response body/page text | Fetched, but not persisted | Full HTML was returned to the classifier in-memory; the live-test driver script logged only `html_len`, not content. |
| Page title | Extracted internally, not surfaced | `detect_block_reason()`/`classify_search_page()` compute it internally but never return/log it. |
| Headers | **NOT CAPTURED** | Never requested from Playwright's response object anywhere in this codebase. |
| Timing/duration | Partially | Driver script logged a start timestamp only, no end timestamp/duration; the bridge has a fixed 60s nav timeout + 6s settle wait, but no per-request duration is measured or logged anywhere. |
| Browser/channel/headless | **Captured, this run — clean** (FACT) | 327 process observations, 100% full `chromium-1243`/"Google Chrome for Testing", 0 `chromium_headless_shell`, 75 with literal `--headless` flag. |

## 3. Comparison With Earlier Successful Validations

| Run | Browser mode | Channel/binary | jobAge param | Health check | Search attempted? | Search outcome |
|---|---|---|---|---|---|---|
| First 5-query test (headed, before this session's headless/3-day work) | headed | full Chromium (necessarily — headed cannot use headless-shell) | none (feature didn't exist yet) | Succeeded | Yes (×5) | 5× **VALID_RESULTS**, 100 raw jobs |
| 5-query + 3-day filter (headless, default binary) | headless | `chromium_headless_shell` | `jobAge=3` | **Failed** (UNKNOWN_BLOCK) | No (pre-advisory architecture) | N/A |
| 5-query + `channel='chromium'` (headless) | headless | full Chromium (confirmed offline; live-run process evidence was self-contaminated that time) | `jobAge=3` | **Failed** (UNKNOWN_BLOCK) | No (pre-advisory architecture) | N/A |
| 1-query, advisory architecture (this investigation's subject) | headless | full Chromium (confirmed cleanly, 327 obs.) | `jobAge=3` | **Failed** (UNKNOWN_BLOCK) | **Yes** | **BLOCKED** (UNKNOWN_BLOCK) |

**FACT:** every headless attempt (3 of 3) has failed the homepage health
check with the identical `UNKNOWN_BLOCK` reason. **FACT:** every headed
attempt (all prior sessions) succeeded at both the homepage and every
search query attempted. **FACT:** the one time the search URL was actually
tried under headless conditions (this run), it was also `BLOCKED`.

**INFERENCE:** these facts are consistent with headless execution being
associated with the block. **This does not by itself establish causation**
— see §4 for why this is confounded with request-volume/timing.

**Rate limiting/timing (FACT):** every headless attempt occurred **after**
a substantial volume of prior live requests earlier in this session (a
20-job audit's underlying live run, a 106-request 5-query run, etc.). The
headless attempts and the "later in the session, after more cumulative
requests" attempts are the **same set of runs** — headed-mode success and
headless-mode failure are perfectly confounded with early-session vs.
late-session timing in the data collected so far.

**URL structure/query parameters:** **FACT** — the base URL pattern
(`https://www.naukri.com/{role}-jobs-in-{location}`) is identical in every
row of the table above; only `jobAge=3` was added for the 3-day-filter
attempts. **FACT** — the homepage URL (`https://www.naukri.com/`, no query
parameters at all) has also failed on every headless attempt, ruling out
the search-specific query parameter as the sole/root explanation (see
Hypothesis F below).

**Number of requests / rate limiting within a single run:** **FACT** — this
run made exactly 2 live requests total (1 health check + 1 search), the
fewest of any live run this session, and still failed. This is evidence
against "too many requests in one call" as a same-run cause, though it says
nothing about cumulative session-level volume.

**Health-check vs. search-page classifier behavior:** **FACT** — both used
the identical detection mechanism (`detect_block_reason()`, the same 5
phrases) on this run; `classify_search_page()` additionally runs the fuller
5-state classifier, but the specific outcome here was the same underlying
phrase-based `UNKNOWN_BLOCK` detection for both the homepage and the search
page.

## 4. Hypothesis Evaluation

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| A | Naukri categorically blocks this exact search URL | **NOT DETERMINABLE** | Only one data point exists for this specific URL+jobAge combination; no comparison (e.g. the same URL without `jobAge`, or a different role/location, under the same headless conditions) has been tried. Cannot distinguish "this URL" from "any URL right now." |
| B | Naukri blocks the current browser/request fingerprint | **PARTIALLY CONTRADICTED (narrow form) / NOT DETERMINABLE (broad form)** | The specific "headless-shell binary" fingerprint sub-hypothesis is **CONTRADICTED**: `channel='chromium'` (full binary, confirmed via clean process evidence) was used this run and still blocked. A broader fingerprint signal (e.g. the `--headless` flag's runtime effects, distinct from which binary file is used) remains **NOT DETERMINABLE** — confounded with D. |
| C | Naukri blocks headless execution categorically | **NOT DETERMINABLE (independently of D)** | 3/3 headless attempts blocked vs. 100% headed success is consistent with C, but every headless attempt also occurred later in the session (higher cumulative request count) than every headed success — C and D cannot be separated with the current data. |
| D | Naukri blocks after a rate/request threshold | **NOT DETERMINABLE (independently of C)** | Same reasoning as C — perfectly confounded in the available timeline. |
| E | The access-denied classifier is too broad / misclassifying a legitimate response | **NOT DETERMINABLE, but no supporting evidence found** | The 5-phrase list was originally validated against real fixtures specifically to avoid known false-positive sources (dormant CSS/class names containing "captcha"/"verify" on the real `search_results_sre_bengaluru.html` fixture do NOT trigger it). No captured evidence from this run (the raw HTML was not saved) either confirms or refutes a false positive here. Classified as not determinable rather than contradicted, in the strict absence of the raw response. |
| F | The `jobAge=3` parameter causes the block | **CONTRADICTED** | The homepage request (`https://www.naukri.com/`, no query parameters at all, no `jobAge`) was **also** blocked, and blocked first, before the search URL was even attempted. A parameter that isn't present on the homepage cannot be the cause of the homepage's own block, and the homepage's block long predates (both in this run and across two prior runs) any attempt to use `jobAge`. |
| G | The current URL/query construction is invalid | **CONTRADICTED (by inference, not certainty)** | The base URL pattern (without `jobAge`) was already proven to produce real `VALID_RESULTS` in headed mode this session (100 real jobs across 5 different role/location combinations, including this exact `infrastructure-engineer-jobs-in-bangalore` pattern for a different location suffix). The observed outcome was `BLOCKED` (an access-denied phrase match), not `PARSE_FAILURE` (which would indicate an unrecognized/malformed page more consistent with an invalid request). This is an inference, not a certainty, since the raw response was not saved. |
| H | Cause cannot currently be determined | **PARTIALLY TRUE** | The **proximate mechanism** (a genuine WAF/access-control interception, detected via a body-text phrase match) is established as FACT, and is very unlikely to be a classifier false-positive (see E). The **root cause** of why the WAF triggered (C vs. D, or another factor) is genuinely **NOT DETERMINABLE** from the evidence collected so far. |

## 5. `jobAge=3` — Separate Investigation

**FACT (native UI evidence):** the real captured fixture
(`data/fixtures/naukri/search_results_sre_bengaluru.html`) contains a
genuine "freshness" filter widget with exactly five discrete options
(`data-id="filter-freshness-{1,3,7,15,30}"`), including `title="Last 3
days"` for the value `3`.

**FACT (URL construction):** `_build_search_url()` maps `max_job_age_days=3`
to the query parameter `jobAge=3` exactly, and the live-requested URL in
this run was confirmed byte-for-byte as
`https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore?jobAge=3`.

**FACT (parameter name itself remains unverified):** the query-string
**parameter name** `jobAge` was always a best-effort, documented guess (see
`naukri_adapter.py`'s own comment) — no captured artifact has ever shown
Naukri's own UI generating a filtered URL to confirm this name is correct.

**FACT:** no live search using `jobAge=3` has ever returned any job
listings — every attempt so far has been blocked before reaching results.

**Conclusion, unchanged from the live-validation report:** `jobAge=3` is
**URL-ONLY / UNVERIFIED**. It is **not** upgraded to functionally verified
by this investigation — no returned listing has ever been inspected for
freshness under this parameter, and none is available to inspect now.

## 6. Safe Offline Diagnostic Improvements — Investigated and Implemented

Three small, purely additive, offline-tested changes were made, since they
directly address the evidence gaps in §1–2 and are needed to make the next
live test maximally informative:

1. **`scripts/naukri_parser.py`** — added `_find_matched_unknown_block_phrase()`
   and used it to include the actual matched phrase in
   `classify_search_page()`'s `detail` string for a `BLOCKED` result.
   `detect_block_reason()`'s own signature and behavior are **completely
   unchanged**; no classification outcome (which state is returned) is
   altered, only the informational `detail` text is enriched.
   **Verified offline** against the real `blocked_access_denied.html`
   fixture: `detail` now reads `"block phrase matched (UNKNOWN_BLOCK):
   'access denied'"`.

2. **`scripts/naukri_fetch_bridge.js`** — after `page.goto()`, prints a
   `DIAGNOSTIC {...}` line to **stderr** (never stdout, preserving the
   existing "stdout is pure HTML" contract) containing the requested URL,
   the final/post-redirect URL, and the HTTP status code. **Verified
   offline** by running the bridge script against a **local `file://` URL**
   pointing at the existing `blocked_access_denied.html` fixture — zero
   Naukri contact, zero network request:
   ```
   DIAGNOSTIC {"requestedUrl":"file:///.../blocked_access_denied.html",
               "finalUrl":"file:///.../blocked_access_denied.html","status":200}
   ```
   stdout was confirmed **byte-identical** to the source fixture, proving
   the change has zero effect on the HTML returned to callers.

3. **`scripts/naukri_fetcher.py`** — `NaukriFetcher.fetch()` now forwards
   the bridge's stderr to Python's own stderr on the **success** path too
   (previously only surfaced on failure, folded into the exception
   message). Return value and exception behavior are unchanged. **Verified
   offline** the same way — a `file://` fetch through the real
   `NaukriFetcher` correctly surfaced the diagnostic line.

**Regression:** all 36 pre-existing standalone tests pass unmodified after
these three changes; `py_compile` clean.

**None of these changes weaken the block classifier** — no phrase was
removed, added, or reweighted; no state transition logic changed. They only
make already-computed information visible.

## 7. Recommended Next Single Controlled Live Test

**Exactly one query**, unchanged from the just-completed run:
`role="Infrastructure Engineer"`, `location="Bangalore"`.

**`max_job_age_days`: OMIT it (use `None`/unrestricted).** Rationale:
Hypothesis F is already **CONTRADICTED** (§4) — the parameter is not the
cause of the homepage block, and this run already confirmed it reaches the
URL correctly. Omitting it for the next attempt removes one more variable,
so a block (or a success) can be attributed with less ambiguity to the base
search request itself, not to anything `jobAge`-related. If this next
attempt succeeds, `max_job_age_days=3` can be re-added in a **subsequent**
single-query test specifically to pursue functional verification — not
combined with this one.

**`health_check_is_advisory`: remain `True` (unchanged).** It cost nothing
this run (one extra diagnostic request) and correctly did not prevent the
real query from being attempted. No evidence suggests reverting it.

**Evidence to capture (using this task's new diagnostics):**
- Health check result (reachable/block_reason), as before.
- Whether `search()` is attempted (as before).
- The exact requested URL (as before) — this time without `jobAge`.
- **New:** the bridge's `DIAGNOSTIC` stderr line for both the health-check
  fetch and the search fetch — HTTP status code and final/redirect URL for
  each.
- **New:** if `BLOCKED`, the exact matched phrase from `classify_search_page()`'s
  enriched `detail` field.
- Raw job count, and — only if any jobs are returned — normalized/eligible
  counts and freshness data (though `max_job_age_days` is omitted, so no
  jobAge-specific freshness claim would be made from this particular run).

**What each outcome would prove/disprove:**
- **VALID_RESULTS with real jobs:** would show the base search URL (no
  `jobAge`) works under headless right now — directly narrowing toward D
  (the earlier attempts' `jobAge`-bearing URLs being blocked was
  coincidental with rate-limiting/timing, not the URL shape) and away from
  A/C as categorical, permanent blocks.
- **BLOCKED again, same phrase:** strengthens (but still does not prove)
  C/D as the dominant factor, and further weakens A (since two different
  URL variants — with and without `jobAge` — would now both be blocked
  under headless).
- **A different HTTP status code or a redirect (via the new diagnostics)
  this time:** would be new evidence not available from any prior run,
  potentially pointing at a different mechanism (e.g. a 403 vs. a 200 with
  a block page rendered client-side) not distinguishable before this task's
  diagnostic additions.

**Headless guarantee:** leave `JOBOS_BROWSER_HEADLESS` unset (default
headless per `naukri_fetch_bridge.js`), `channel: 'chromium'` unchanged —
identical, already-proven-clean configuration from this run.

**Production DB guarantee:** identical procedure as every prior live test —
a fresh `tempfile.mkdtemp()`-created isolated DB (never a copy of
production), production DB opened only in `mode=ro` for before/after SHA
verification, never opened for writing.

## 8. Are We Ready for a Multi-Query Live Validation?

**No — not yet, and not merely because the architecture works.**

The architecture itself is now proven correct (advisory health check,
per-query classification, worker status aggregation all behaved exactly as
designed in this run). But the **underlying question this whole
investigation exists to answer — why Naukri is currently blocking, and
whether that condition is transient, permanent, or specific to something
this project controls — remains genuinely unresolved** (Hypotheses C and D
both `NOT DETERMINABLE`, confounded with each other). Running 5 or 10
queries now would:
- multiply live requests against a source already observed blocking on its
  very first (and only) headless search attempt, with no new information
  gained proportional to the extra requests, and
- not by itself distinguish any of the open hypotheses better than one
  more single, carefully-instrumented query would.

**Recommendation:** run the single query specified in §7 first. Only after
that result is captured and analyzed should a multi-query test be
considered, and that decision should explicitly weigh whatever new evidence
that single query provides.

## Production DB Safety

| | SHA-256 | Size | Row counts |
|---|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | 114688 | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | 114688 | identical |

**Byte-identical. No live Naukri/network request was made during this
investigation.** The only browser launch performed was against a local
`file://` fixture, to verify the new diagnostic code offline.
