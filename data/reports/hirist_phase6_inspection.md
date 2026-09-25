# Phase 6 — Hirist: Discovery/Feasibility Inspection

**Discovery/feasibility gate only. No implementation performed. Hirist
remains `AdapterStatus.NOT_ENABLED`, `capabilities = frozenset()`.**

## 1. Executive Summary

The single live test **succeeded technically and encountered no block,
challenge, login wall, or CAPTCHA.** Hirist served a real, populated
public jobs-search results page (HTTP 200, no redirect) for a
"Senior DevOps Engineer" / Bengaluru query, containing 20 job listings
on page 1 of a claimed 3,441 total. Critically, the page embeds
**machine-readable `schema.org` JSON-LD structured data** (an
`ItemList` of job postings, including at least company name and title
for the first entries) and an explicit `<link rel="next">` tag
revealing the real pagination URL pattern (`?page=2`) — both directly
observed, high-confidence structural findings, not guesses.

`robots.txt` is markedly more permissive than LinkedIn's: no
`Disallow` rule covers any search or job-listing path, a sitemap is
published (a pro-indexing signal), and the only site-wide restriction
is a `Crawl-delay: 10` directive. **This does not, by itself, constitute
explicit authorization for automated data extraction** — that remains
unknown (Section 18) — but the combination of technical reachability,
no anti-bot friction observed, and a non-restrictive `robots.txt` is a
meaningfully more favorable starting picture than LinkedIn's.

## 2. Exact Live Requests Made

| # | Type | URL | Result |
|---|---|---|---|
| 1 | `robots.txt` fetch (plain HTTP GET via `curl`, no browser) | `https://www.hirist.tech/robots.txt` | HTTP 200 |
| 2 | Single Playwright page navigation | `https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru` | HTTP 200 |

**Total live requests: 2. No retries. No detail pages visited. No
login, account creation, application, or CAPTCHA-solving of any kind
occurred.**

## 3. Browser/Runtime Configuration

| | Value |
|---|---|
| `headless` | `true` (project's `JOBOS_BROWSER_HEADLESS` default, unset → headless) |
| `channel` | `'chromium'` — identical, unmodified configuration already used for the Naukri and LinkedIn probes |
| Effective `navigator.userAgent` | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/153.0.0.0 Safari/537.36` |
| User-Agent override applied | **None** — same discipline as the LinkedIn inspection: no customization, no stealth, no fingerprint spoofing, no proxy |

## 4. Exact URL(s)

- **Search page (best-effort URL construction, not verified against any
  prior capture):** `https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru`
  — built from Hirist's own observed SEO-slug convention
  (`/search/<role>-jobs`), by analogy with Naukri's already-verified
  `<role>-jobs-in-<location>` pattern. This inspection is the first
  evidence gathered for Hirist's URL structure in this project.
- **`robots.txt`:** `https://www.hirist.tech/robots.txt`
- **Canonical URL, per the page's own `<link rel="canonical">` tag:**
  `https://www.hirist.tech/search/senior-devops-engineer-jobs`
  — **notably, this drops the `?locations=Bengaluru` query string**,
  which is directly relevant to Section 10 (see below).
- **Confirmed page-2 URL, per the page's own `<link rel="next">` tag:**
  `https://www.hirist.tech/search/senior-devops-engineer-jobs?page=2`

## 5. HTTP Status / Final URL / Redirects

| | Value |
|---|---|
| Requested URL | (Section 4, search page) |
| HTTP status | **200** |
| Final URL | identical to requested URL |
| Redirected (HTTP-level) | **No** |
| `robots.txt` HTTP status | **200** |

**FACT/OBSERVED**, captured via the same `DIAGNOSTIC` stderr pattern
already used for Naukri and LinkedIn probes.

## 6. Search-Page Accessibility

- Loaded successfully without any login redirect, CAPTCHA, or
  access-denied response.
- Page title: *"Search for - Senior Devops Engineer Jobs, 3441 Job
  Vacancies for Search for - Senior Devops Engineer in September 2026 |
  hirist.tech"* — a real, specific, populated results page (matches
  the current date, not a stale cache).
- `<meta name="robots" content="index,follow">` — an explicit,
  page-level instruction permitting search-engine indexing of this
  exact page (a positive signal about the page's own intended
  visibility, distinct from — and not the same as — permission for
  third-party automated data extraction).
- **Zero** `<input type="password">` elements were found anywhere on
  the page (versus 1 on LinkedIn's equivalent page) — no sign-in
  form is embedded on this results page at all.

## 7. Search Result Structure

- **`schema.org` JSON-LD structured data is embedded directly in the
  page**: a `BreadcrumbList` (Home → "Senior Devops Engineer") and an
  `ItemList` block explicitly declaring `"numberOfItems": 20` with
  `itemListElement` entries beginning with a real company name
  ("Verint - Senior ..."). This is the single most significant
  structural finding of this inspection: Hirist appears to publish
  machine-readable, standards-based job-listing data directly on the
  search-results page itself, rather than requiring per-card HTML
  scraping.
- **Limitation, honestly noted:** the captured HTML excerpt (this
  inspection's bounded 4,000-character capture, out of 312,194 total
  page characters) cuts off partway through the `ItemList` JSON-LD
  block, right after the first entry's partial name. **The full
  structured-data payload (all 20 entries' complete field sets) was
  not captured in this pass** — this is a real, acknowledged gap in
  this specific inspection's evidence, not a claim that the data
  doesn't exist beyond what was seen. No second request was made to
  correct this, per the "no retries" constraint; it is recommended as
  the first concrete follow-up step (Section 20).
- A loose class-name heuristic (`[class*="job-card" i]` and similar)
  matched **0** elements — this heuristic evidently does not match
  Hirist's actual class-naming scheme (identical limitation to the
  LinkedIn inspection); no corrected selector is proposed here.
- A broader anchor-pattern heuristic (`/job-detail/`, `/jobs/`, `/j/`
  substrings) matched **20** elements — consistent with the JSON-LD
  `numberOfItems: 20`, corroborating that 20 real job-detail links are
  present on this one page.
- 16 elements matched a loose "pagination" class/aria-label heuristic,
  consistent with the confirmed `rel="next"` pagination link (Section
  4).

## 8. Job-Card Fields Observed

| Field | Status |
|---|---|
| Job title | **Confirmed present** — visible in the partial JSON-LD `ItemList` entry captured, and in the page's own title/breadcrumb |
| Company name | **Confirmed present** — "Verint" appeared as a real company name in the partial JSON-LD entry captured |
| Location | Reflected in page title/URL context; not individually confirmed per-listing in the captured excerpt |
| Posted/freshness date | **Not confirmed** — not visible in the captured excerpt |
| Experience requirement | **Not confirmed** |
| Salary | **Not confirmed** |
| Description/requirements | **Not confirmed on this page** — would very likely require the (unvisited) detail page |
| Employment/work-model info | **Not confirmed** |
| Job ID / stable identifier | **Not confirmed** — 20 detail-link anchors were counted, but individual `href` values were not extracted in this pass |

**Every "not confirmed" is an honest gap from this single, bounded
observation, not a negative finding** — several of these fields are
very plausibly present in the full (uncaptured) JSON-LD payload.

## 9. Job IDs / URL Structure

**Not individually confirmed.** 20 anchor elements matched a broad,
multi-pattern selector (`/job-detail/`, `/jobs/`, `/j/`), but no
specific `href` value was extracted or inspected in this pass. The
exact detail-page URL/ID pattern remains **unknown, requires
validation** — explicitly not visited in this inspection, per
instruction.

## 10. Freshness Behavior

**Not tested.** No freshness/date filter parameter was attempted (would
require a second, differently-parameterized request, out of scope for
this single-page inspection). **Status: UNKNOWN, requires validation.**

## 11. Pagination Behavior

**Confirmed, directly observed (FACT):** the page's own `<link
rel="next" href="https://www.hirist.tech/search/senior-devops-engineer-jobs?page=2">`
tag reveals the real pagination mechanism: a `?page=N` query parameter
appended to the same role-slug path. This is a directly observed fact,
not a guess — the strongest, most concrete structural finding in this
report alongside the JSON-LD data.

## 12. Application-URL Behavior

**Not confirmed.** No application URL was visible in the captured
excerpt, and no detail page was visited (out of scope). `robots.txt`
places no specific restriction on any application-related path (unlike
LinkedIn's explicit `/job-apply/` and `/jobs/view/externalApply/`
disallow rules) — but this absence of a restriction is not the same as
confirmed availability or authorization; both remain **unknown**.

## 13. `robots.txt` / Policy Observations

**Fetched live in this task** (`https://www.hirist.tech/robots.txt`,
HTTP 200), full content (21 lines):

```
User-agent: *
Disallow: /components/
Disallow: /images/
Disallow: /modules/
Disallow: /admin/
Disallow: /administrator/
Disallow: /dao/
Disallow: /cache/
Disallow: /config/
Disallow: /classes/
Disallow: /language/
Disallow: /help/
Disallow: /util/
Disallow: /includes/
Disallow: /installation/
Disallow: /mambots/
Disallow: /media/
Disallow: /templates/
Disallow: /editor/
Disallow: /old/
User-agent: Yandex
Disallow: /

Sitemap: https://www.hirist.tech/new_sitemap_index.xml
Sitemap: https://www.hirist.tech/blog/sitemap_index.xml

Crawl-delay: 10
```

- **`User-agent: *` block:** disallows only administrative/internal
  infrastructure paths (`/admin/`, `/config/`, `/cache/`, template/CMS
  internals, etc.). **No `/search/`, `/jobs/`, or job-listing path is
  disallowed for the generic `*` agent** — a generic Playwright session
  (matching no other named block) falls under this permissive rule,
  not a blanket disallow.
- **`User-agent: Yandex`:** singled out for a full site disallow
  (`Disallow: /`) — a site-specific policy choice unrelated to generic
  crawlers/browsers.
- **Sitemaps published:** two sitemap URLs are declared — a
  pro-indexing signal (a site actively hiding from crawlers would not
  typically publish sitemaps), though this speaks to search-engine
  indexing intent, not third-party automated-extraction authorization.
- **`Crawl-delay: 10`:** present in the file. **Its exact binding is
  ambiguous as written** — it appears after the `Sitemap:` lines and
  after the `Yandex` block, with no repeated `User-agent: *` line
  immediately preceding it. This report does not assert a confident
  interpretation of which agent group it binds to; it is quoted
  verbatim and flagged as a directly relevant, actionable rate-limiting
  signal regardless of the exact technical binding — any future
  implementation should treat 10 seconds between requests as a floor,
  not attempt to argue the directive doesn't apply.
- No explicit crawl/API/contact/whitelisting instruction (unlike
  LinkedIn's explicit "email whitelist-crawl@linkedin.com" notice) was
  found anywhere in this file.

**This report does not treat `robots.txt` as a complete legal
determination** (per instruction) — it is one input among several, not
a substitute for Hirist's actual Terms of Service, which were not read
or verified in this task (see Section 18).

## 14. Authentication Requirements

**None observed for this search-results page.** No login redirect, no
password field, no sign-in prompt text detected. Whether an individual
job's *detail* page or any deeper functionality (saving jobs, applying)
requires an account was **not tested** — no detail page was visited.

## 15. CAPTCHA / Challenge / Block Behavior

**None encountered.** All marker checks (login-wall URL, login-wall
text, CAPTCHA/challenge text, access-denied text, empty-results text,
"not found" text) returned negative. No iframe-based challenge widget
was distinguishable from the 2 iframes present (likely
advertising/tracking, not confirmed either way — no further
investigation was performed, consistent with the single-page-only
scope).

## 16. Rate-Limit Observations

No rate limiting was observed or expected from a single request.
`robots.txt`'s `Crawl-delay: 10` (Section 13) is the only concrete,
directly relevant signal available; no live behavior under repeated or
sustained access was tested, and this task does not recommend testing
that by escalating request volume.

## 17. Technical Feasibility

**Feasible, as observed once:** a plain, unmodified, headless
Playwright/Chromium session reached a real Hirist Jobs search-results
page, got HTTP 200, encountered no login/CAPTCHA/block friction, and
found machine-readable structured job data embedded directly in the
page. This is a technically favorable single observation. Reliability
across repeated queries, over time, at volume, for detail pages, or for
pagination beyond page 1 is **not established** by one visit.

## 18. Authorization/Compliance Observations

Explicitly distinguishing the four categories requested:

| | Assessment |
|---|---|
| **Technically reachable** | Yes — confirmed (HTTP 200, real content, no block) |
| **Publicly accessible** | Yes — no login/account required to view this page |
| **Explicitly permitted/authorized** | **Not confirmed.** No explicit statement of permission for automated querying/extraction was found. `robots.txt`'s absence of a `Disallow` rule for this path is a *lack of prohibition* under the robots-exclusion convention, not an affirmative grant of permission — and this report does not conflate the two. Hirist's Terms of Service were not read or verified in this task. |
| **Unknown** | Whether Hirist's Terms of Service (not inspected in this task) separately restrict automated data collection; whether the `Crawl-delay` directive's ambiguous binding matters in practice; whether sustained/repeated automated access would be treated differently from this one observation |

**This report does not infer authorization merely from the HTTP 200
response**, per instruction. The overall picture (permissive
`robots.txt`, no anti-bot friction observed, structured data present)
is more favorable than LinkedIn's, but "more favorable" is not the same
as "confirmed authorized."

## 19. Unknowns

1. Whether the `?locations=Bengaluru` query parameter actually filters
   results, or is silently ignored — the page's own canonical URL tag
   dropped this parameter, which is at minimum ambiguous evidence, not
   confirmation either way.
2. The complete `schema.org` JSON-LD payload for all 20 listed jobs
   (only partially captured in this pass' bounded excerpt).
3. Exact `href` values / ID structure for individual job-detail pages.
4. Whether job-detail pages require login or present different
   anti-bot behavior than the search page (not visited).
5. Whether a freshness/date filter is supported and what parameter
   governs it.
6. Whether remote/work-model filtering is supported.
7. Application-URL availability and structure.
8. Salary and experience-requirement field availability.
9. The precise binding of the `Crawl-delay: 10` directive.
10. Content of Hirist's Terms of Service regarding automated access
    (not read in this task).
11. Behavior under repeated or higher-volume access (not tested, and
    not recommended to be tested by escalating volume without fresh,
    explicit authorization).

## 20. Recommended Next Gate (No Ranking)

Two factual paths are available, presented without ranking:

**A. Additional offline analysis of already-legitimate, already-
inspected evidence categories** — most concretely, a fresh, single,
separately-authorized live pass with a larger HTML-capture bound (to
retrieve the complete JSON-LD payload observed but only partially
captured here), OR a design-only step (mirroring the LinkedIn Phase 5
Step 2 pattern) that specifies a field mapping and adapter boundary
using only what has already been observed, explicitly marking the
JSON-LD-completeness gap as an open question.

**B. A policy/scope review of Hirist's Terms of Service** (not
performed in this task) to resolve Section 18's "explicitly
permitted/authorized" unknown before any further technical work —
mirroring the LinkedIn Phase 5 Step 1 conclusion's own logic, applied
to this source's differently-shaped (more permissive, but still not
confirmed-authorized) evidence.

**No implementation, fixture-building, or parser logic was created in
this task. Hirist remains `AdapterStatus.NOT_ENABLED`, `capabilities =
frozenset()`.**

---

## Regression / Safety

- **New file:** `scripts/hirist_discovery_probe.js` — diagnostic-only,
  mirrors this project's existing, documented probe convention (same
  pattern as `naukri_discovery_probe.js` and
  `linkedin_discovery_probe.js`). Never imported or referenced by
  `hirist_adapter.py`, `source_registry.py`, or any production file.
- `scripts/hirist_adapter.py`: **zero changes.** Still
  `status = AdapterStatus.NOT_ENABLED`, `capabilities = frozenset()`,
  `health_check()`/`search()` still raise `AdapterNotEnabledError`
  immediately.
- `source_registry.py`, `search_profile.py`, `search_worker.py`,
  scoring, eligibility, freshness, deduplication, Naukri, LinkedIn
  (behavior and design): **zero changes.**
- Full standalone regression suite: **40/40 pass.**
- `python3 -m py_compile` across all `scripts/*.py`: clean.
- `search_profile._default_sources()`: confirmed `['NAUKRI']`.
- `HiristAdapter.status`: confirmed `AdapterStatus.NOT_ENABLED`.
- `HiristAdapter.capabilities`: confirmed `frozenset()`.

## Production DB Safety

No SQL/database access of any kind was performed in this task (this
inspection needed none). A file-level SHA-256 checksum of
`data/applications/jobos.db` (not a database open) was taken before and
after as a safety verification only:

| | SHA-256 |
|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |

**Byte-identical.**

## Exact Live Request Count

- `robots.txt` fetch: 1
- Hirist Jobs search-page visits: 1
- **Total live requests: 2**
- Retries: 0
- Detail pages visited: 0
- Authentication attempted: 0
- Applications submitted: 0
- CAPTCHA solve attempts: 0

**Stopping here, per instruction. No adapter implementation was
performed. Hirist was not enabled.**
