# Phase 6 Step 9 — Hirist Raw JSON-LD Payload Capture (One Page, One Request)

**Exactly one live HTTP request was made in this task. Hirist remains
`AdapterStatus.NOT_ENABLED`, `capabilities = frozenset()`.
`scripts/hirist_parser.py` was NOT modified. No production behavior
changed. `source_registry.py` was NOT modified. `naukri_*` files were
NOT touched.**

## How the single-request guarantee was enforced

Per the task's explicit warning ("do not rely on post-hoc stopping —
the request mechanism itself must be one-page-only"), the normal
`HiristAdapter.search()` was **never invoked** — that method's own
pagination loop (which follows `rel="next"` automatically, as
Phase 6 Step 7 already demonstrated by making 3 requests from 1 call)
was structurally avoided rather than capped after the fact.

Instead:
1. The exact search URL was built **offline, with zero network
   access**, by importing and calling the existing, unmodified,
   pure function `hirist_adapter._build_search_url()` directly
   against a `SearchQuery(role="Senior DevOps Engineer",
   location="Bengaluru")` — confirmed to produce
   `https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru`,
   byte-identical to Step 7's own page-1 URL.
2. A **new, diagnostic-only** Node/Playwright script (kept in this
   session's scratchpad directory, not `scripts/` — it is not a
   production file) made the actual request. It contains exactly one
   `page.goto(url, ...)` call and no loop, no `rel="next"` handling,
   and no second navigation of any kind — pagination is structurally
   impossible by construction, not merely avoided by convention. It
   mirrors `scripts/hirist_fetch_bridge.js`'s exact, already-validated
   configuration (`channel: 'chromium'`, headless mode driven by
   `JOBOS_BROWSER_HEADLESS`, viewport 1440×900, `waitUntil:
   'domcontentloaded'`, 60s timeout, 3s settle wait, `page.content()`
   for the HTML) — no stealth, no UA override, no fingerprint change,
   nothing invented — with only two additions, both purely
   observational: capturing `navigator.userAgent` via
   `page.evaluate()`, and writing the full HTML straight to a file
   instead of through stdout (avoiding any stdout truncation risk).
3. `scripts/hirist_fetch_bridge.js` itself was **not modified, not
   invoked, and not read from** in this task's live step — the
   diagnostic script is entirely separate.
4. The Bash tool invoked this diagnostic script **exactly once**, with
   the one pre-built URL, writing directly to
   `data/reports/hirist_step9_raw_capture.html`.

## A. Request Evidence

| | Value |
|---|---|
| Total HTTP requests made | **1** |
| Requested URL | `https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru` |
| Final URL | `https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru` (identical) |
| Redirected | **No** |
| HTTP status | **200** |
| `content-type` response header | `text/html; charset=utf-8` |
| Effective `navigator.userAgent` | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/153.0.0.0 Safari/537.36` |
| Browser mode | Headless (`JOBOS_BROWSER_HEADLESS` unset → default headless) |
| Channel | `chromium` (full "Google Chrome for Testing" binary, same as production) |
| Response length | 312,187 characters (312,191 bytes on disk, UTF-8) |
| Started | 2026-09-20T15:21:09.710Z |
| Ended | 2026-09-20T15:21:13.744Z (≈4.0s total) |
| Retries | **0** |
| Detail-page requests | **0** |
| Second queries | **0** |
| Authentication / CAPTCHA interaction | **0** |

Note: the effective UA string contains `HeadlessChrome` — the exact
signal that caused Naukri to return HTTP 403 in this project's earlier
forensics. Hirist returned a normal `200` with this same UA present,
consistent with all prior Hirist evidence (Steps 1, 4, 7) that no
UA-based blocking has ever been observed for this source.

**Persisted artifact:** `data/reports/hirist_step9_raw_capture.html`
(the complete, unmodified raw HTML response).

## Classifier Result (existing, unmodified `classify_search_page()`)

| | Value |
|---|---|
| `HiristPageState` | `VALID_RESULTS` |
| Matched block/challenge phrase | None |
| Jobs the **current, unmodified** parser extracts from this real page | 10 of 20 (10 skipped — see Section C) |
| `next_url` detected (not followed) | `.../search/senior-devops-engineer-jobs?page=2` |

## B. JSON-LD Evidence (every block, complete)

The page contains exactly **two** `<script type="application/ld+json">`
blocks — both well-formed JSON, no malformed block:

**Block 0 — `BreadcrumbList`** (not job data; navigational breadcrumb
markup): `@context: https://schema.org`, `@type: BreadcrumbList`,
top-level keys: `@context`, `@type`, `itemListElement`. Not analyzed
further — out of scope (not job-identifying data).

**Block 1 — `ItemList`** (the one `hirist_parser.py` looks for):

| | Value |
|---|---|
| `@context` | `https://schema.org` |
| `@type` | `ItemList` |
| Top-level keys | `@context`, `@type`, `name`, `numberOfItems`, `itemListElement` |
| `numberOfItems` | 20 |
| `itemListElement` length | 20 (matches `numberOfItems`) |

**Union of every key seen across all 20 `ListItem` entries:**
`@type`, `name`, `position`, `url` — **and nothing else.** No entry, in
any of the 20, contains: a nested `"item"` object, `@id`, `sameAs`,
`jobLocation`, `experienceRequirements`, `baseSalary`, `description`,
or any application/contact field. This is now a **directly confirmed
FACT**, not an inference from truncated data.

## C. Real Company/Title Evidence

All 20 raw `name` values, verbatim, from this real captured page:

```
1.  Verint - Senior DevOps Engineer
2.  Senior DevOps Engineer - AWS & Kubernetes
3.  Vedantu Innovations - Senior DevOps & SecOps Engineer
4.  NTT DATA - Senior DevOps Engineer
5.  SatSure - Senior Cloud/DevOps Engineer
6.  Verint - Senior Software Engineer - DevOps
7.  Lucideus - Senior DevOps Engineer
8.  EXL - Senior DevOps Engineer - Cloud Architecture & Infrastructure
9.  Appzlogic Mobility - Senior DevOps Engineer - GCP/GKE
10. MoveInSync - Senior DevOps Engineer - AWS Infrastructure
11. Optum - Senior DevOps Engineer - AWS/Azure/Google Cloud Platform
12. Senior DevOps Engineer - Cloud Infrastructure
13. Opstree Solutions - Senior DevOps/SRE Engineer - Cloud Infrastructure
14. Moody's - Senior Software Engineer - DevOps & Cloud Engineering
15. Atyeti - Senior DevOps Engineer
16. CG-VAK - Senior DevOps Engineer
17. ZeMoSo Technologies - Senior DevOps Engineer - AWS
18. RHIA - Senior DevOps Engineer - Cloud Infrastructure
19. Normec Verifavia - Senior DevOps Engineer - Cloud Infrastructure
20. Senior DevOps Engineer - AWS & Kubernetes
```

**Delimiter occurrence count for `" - "` across all 20 names:**

| Occurrences | Count |
|---|---|
| 0 | 0 |
| Exactly 1 | 10 |
| More than 1 | 10 |

**Does `name` consistently follow `"Company - Title"`? No — confirmed,
not merely suspected.** This real page shows **at least three distinct
conventions coexisting**, and the delimiter itself does not
disambiguate between them:

1. **`"Company - Title"`** (the assumed convention) — e.g. `Verint -
   Senior DevOps Engineer`, `NTT DATA - Senior DevOps Engineer`.
2. **`"Title"` alone, with no company segment at all** — e.g. `Senior
   DevOps Engineer - AWS & Kubernetes` (entries 2 and 20, identical —
   almost certainly the same underlying job re-listed or a near-duplicate
   posting) and `Senior DevOps Engineer - Cloud Infrastructure`
   (entry 12). In these, the **entire string is the title**; there is
   no company name anywhere in `name` for these postings (plausibly
   anonymous/confidential listings). The current parser's "split on
   the one delimiter" logic **cannot distinguish this case from case 1**
   — it just takes whatever comes before the (single) delimiter and
   calls it the company, which is how the confirmed-wrong split
   happened in Step 7.
3. **`"Company - Title - Tags/Skills"`** (three or more segments) —
   e.g. `EXL - Senior DevOps Engineer - Cloud Architecture &
   Infrastructure`, `MoveInSync - Senior DevOps Engineer - AWS
   Infrastructure`, `Optum - Senior DevOps Engineer -
   AWS/Azure/Google Cloud Platform`. Here company and title **are**
   cleanly separated by the first delimiter, but the current parser's
   "exactly one occurrence" fail-closed rule **skips these entirely**
   rather than taking only the first split — a real, confirmed cost of
   the fail-closed design (10 of 20 entries, half the page, are
   discarded this way).

**Does the delimiter appear inside company names?** Not observed in
this sample (no company name itself contains `" - "`).

**Does the delimiter appear inside job titles?** **Yes, confirmed** —
e.g. entry 6's actual title portion is `"Senior Software Engineer -
DevOps"` (a title that itself contains a hyphenated qualifier), and
entry 13's is `"Senior DevOps/SRE Engineer - Cloud Infrastructure"` if
read as company=`Opstree Solutions`, title=`"Senior DevOps/SRE
Engineer - Cloud Infrastructure"`. This means even the 3-segment case
is not reliably "company, title, tags" — the second delimiter could
just as easily be *part of the title itself*, and nothing in the string
distinguishes the two.

**Does another field provide company/title separately?** **No** — per
Section B, `@type`, `name`, `position`, `url` are the only keys any
entry ever has. There is no separate `hiringOrganization`/company
field anywhere in this data.

**Does a stable, deterministic parsing rule exist?** **No — confirmed
negative, not merely unconfirmed.** With genuine variable-arity
hyphen-delimited strings, where the hyphen appears both as a field
separator *and* inside titles, no positional rule (first segment,
last segment, count-based) can be shown to correctly recover
company/title from `name` alone for all three observed conventions
simultaneously, on this evidence. **Per instruction, no heuristic
parser was implemented in this task.**

## D. URL Evidence — Conclusive

**A job URL exists on every single entry.** All 20 `ListItem` objects
have a **top-level `url` field**, sibling to `name` and `position` —
**not** nested inside an `"item"` object.

Example (entry 1): `"url": "https://www.hirist.tech/j/verint-senior-dev-ops-engineer-1672606"`

Checked and confirmed for this page:

| Field/path | Present? |
|---|---|
| `ListItem.url` (top-level, sibling to `name`) | **YES — 20/20 entries** |
| `ListItem.@id` | No — 0/20 |
| `ListItem.sameAs` | No — 0/20 |
| `ListItem.item.url` (nested) | N/A — no entry has a nested `item` object at all (0/20) |
| `ListItem.item.@id` / `.sameAs` | N/A, same reason |
| Other URL-like fields | None found in any entry |

**This directly confirms Phase 6 Step 8's hypothesis (ii) as correct,
and rules out hypothesis (i):** the real Hirist search-results JSON-LD
**does** carry a per-job URL — it was never missing from the data. It
was missing from every one of Step 7's 24 parsed jobs **only** because
`hirist_parser._parse_job_entry()`'s flat-name branch (the only branch
real data ever exercises) never reads `list_item.get("url")` — it only
ever checks for a `url` field nested inside an `"item"` object, a shape
this real page (and, by strong implication, Hirist's real data
generally) **never uses**.

**Field/path where the URL actually lives:** `itemListElement[i].url`
— a plain top-level string on each `ListItem`, e.g.
`https://www.hirist.tech/j/<company-title-slug>-<numeric-id>`.

**A secondary finding from this same evidence:** the trailing numeric
segment of each URL (e.g. `1672606`, `1672338`, `1672535`) is a
distinct integer per job and is very likely Hirist's own internal job
ID — a strong candidate for the "stable job identifier" this project
has never had for Hirist (see Section E). **Not implemented or relied
upon in this task** — noted as evidence only.

**hirist_parser.py was NOT modified to read this field, per explicit
instruction.** The one-line fix this finding would justify (reading
`list_item.get("url")` in the flat-name branch, falling back only if
absent) is documented here as a **recommended, evidence-backed next
step for a future, separately-authorized task** — not implemented now.

## E. Other Fields

| Field | Present in this page's JSON-LD? | Evidence |
|---|---|---|
| Location | **No** | Not a key on any of the 20 `ListItem` entries (Section B's union-of-keys) |
| Experience | **No** | Same |
| Salary | **No** | Same |
| Description | **No** (within JSON-LD). The literal string `"description"` appears twice elsewhere in the raw HTML, unrelated to the `ItemList`/`ListItem` JSON-LD — not investigated further, out of this task's JSON-LD scope | `grep` count = 2, neither inside the `ItemList` block |
| Stable ID | **Not directly as an `@id` field** — but see Section D's secondary finding: the URL's trailing numeric segment is a strong (not yet exploited) candidate |
| Application URL | **No** — no field distinct from the one `url` found; no `applicationContact`/`applyUrl`/similar key anywhere in either JSON-LD block |

**One additional, out-of-scope observation, noted for completeness
and honesty, not analyzed further in this task:** the page also
embeds a Next.js `__NEXT_DATA__` script tag
(`props.pageProps.initialState`, keys: `ads`, `auth`, `app`, `job`,
`user`, `jobDetail`, `competition`, `botDetection`). This is **not**
JSON-LD and was outside this task's requested scope, but a brief,
non-invasive check was made (still zero network — reading the already
-captured HTML only) to see whether it might hold richer job data than
the JSON-LD: **it does not, in this capture** — `initialState.job.jobfeed`
is an empty list (`len 0`), `totalJobs: 0`, `isLoading: true`, and
`initialState.jobDetail` has only an empty `jobDetailInsights` key.
This confirms the visible job list is rendered from server-side JSON-LD
independently of the client-side Redux-style state, which appears to
populate only after further client-side interaction/API calls this
single page load did not trigger. **This rules out `__NEXT_DATA__` as
an easy alternative richer data source from this same page** — noted
as a negative finding, not pursued further.

## Safety Verification

| Check | Result |
|---|---|
| Exactly one HTTP request made | **Confirmed** — 1 (see Section A) |
| Zero pagination requests | **Confirmed** — the capture mechanism contains no loop or `rel="next"` handling; `next_url` was detected by the offline classifier afterward but never fetched |
| Zero detail-page requests | **Confirmed** — 0 |
| Zero retries | **Confirmed** — single invocation, exit code 0 on first attempt |
| Zero second queries | **Confirmed** — one Bash invocation of the capture script, total |
| No authentication / CAPTCHA interaction | **Confirmed** — none encountered, none attempted |
| Headless Chromium only, no stealth/fingerprint modification | **Confirmed** — identical config to the production bridge, no stealth libraries, no UA override |
| Production DB unchanged | **Confirmed** — SHA-256 byte-identical before/after (below) |
| `HiristAdapter.status` remains `NOT_ENABLED` | **Confirmed** |
| `HiristAdapter.capabilities` remains empty | **Confirmed** |
| `search_profile._default_sources()` remains `['NAUKRI']` | **Confirmed** |
| `scripts/hirist_parser.py` modified | **No** |
| `scripts/hirist_adapter.py` / `hirist_fetcher.py` / `hirist_fetch_bridge.js` modified | **No** |
| `scripts/source_registry.py` modified | **No** |
| Any `naukri_*` file modified | **No** |

## Production DB Safety

| | SHA-256 |
|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |

**Byte-identical.** Never opened.

## Final Verification

```
Full standalone regression suite (44 files):     PASS=44 FAIL=0
python3 -m py_compile across all scripts/*.py:    PY_COMPILE_ALL_OK
JSON validation (this report's .json):            valid
```

`HiristAdapter.status` = `NOT_ENABLED`, `capabilities` = `frozenset()`,
`search_profile._default_sources()` = `['NAUKRI']` — all re-confirmed
unchanged after this capture.

## Files Changed

| File | Change |
|---|---|
| `data/reports/hirist_step9_raw_capture.html` | New — the complete, unmodified raw HTML response from the single live request |
| `data/reports/hirist_raw_payload_capture.md` / `.json` | New — this report |

**Not modified:** `scripts/hirist_parser.py`, `scripts/hirist_adapter.py`,
`scripts/hirist_fetcher.py`, `scripts/hirist_fetch_bridge.js`,
`scripts/naukri_parser.py`, `scripts/naukri_adapter.py`,
`scripts/naukri_fetcher.py`, `scripts/naukri_fetch_bridge.js`,
`scripts/source_registry.py`, `scripts/search_profile.py`,
`scripts/search_worker.py`, `scripts/score_job.py`,
`scripts/job_eligibility.py`, `data/applications/jobos.db`,
any existing Hirist fixture, any LinkedIn file. The one-off diagnostic
capture script used for the live request lives only in this session's
scratchpad directory — it was never added to `scripts/` and is not a
project file.

## Summary

1. **The job URL evidence gap from Step 8 is now resolved,
   conclusively.** Every real `ListItem` entry has a top-level `url`
   field — the data was never missing; the current parser simply never
   reads it on the (only-ever-exercised) flat-name path. This is a
   confirmed, evidence-backed, one-line fix candidate — **not
   implemented in this task**, per explicit instruction.
2. **The delimiter issue is confirmed to be a genuine, structural
   ambiguity in the source data, not a parser bug in the ordinary
   sense.** `name` mixes at least three different conventions (bare
   title, `Company - Title`, and `Company - Title - Tags`), and the
   hyphen delimiter appears both between fields and inside titles.
   **No deterministic rule recovers company/title correctly for all
   three conventions from `name` alone** — this remains an open,
   unresolved limitation. No heuristic parser was implemented.
3. **Location, experience, salary, description, and a dedicated
   application URL are confirmed absent from this page's JSON-LD** —
   not merely unobserved, but positively absent from the complete
   key set of all 20 real entries.
4. **A strong, unexploited stable-ID candidate exists**: the numeric
   suffix of each job's URL.

**Stopping here, per instruction. No further live Hirist query was
made. Hirist remains `AdapterStatus.NOT_ENABLED` and was not enabled
globally. `hirist_parser.py` was not modified.**
