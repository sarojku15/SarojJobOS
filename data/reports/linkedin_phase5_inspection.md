# Phase 5 — LinkedIn Jobs: Discovery/Feasibility Inspection

**Discovery/feasibility gate only. No implementation performed. LinkedIn
remains `AdapterStatus.NOT_ENABLED`, `capabilities = frozenset()`.**

**Live requests made in this task: exactly two, both read-only, both
non-destructive, both to publicly served resources —**
1. One `robots.txt` fetch (plain static text file, via `curl`, no
   browser) — LinkedIn's own machine-readable crawl policy.
2. One single Playwright page navigation to one LinkedIn Jobs public
   search URL — no login, no credentials, no CAPTCHA solving, no
   bypass, no retry, no pagination, no second page visited.

`scripts/linkedin_adapter.py`, `source_registry.py`, `search_profile.py`,
`search_worker.py`, scoring/eligibility/freshness/dedup, and the
production database were **not touched** in this task.

---

## Executive Summary

The single live test **succeeded technically** — LinkedIn served a real,
populated public jobs-search results page (HTTP 200, no redirect, no
CAPTCHA, no access-denied page) containing 61 links to individual job
detail pages. **However, LinkedIn's own `robots.txt` explicitly disallows
automated access for any unrecognized user agent** via a catch-all
`User-agent: * / Disallow: /` rule, with an explicit note directing
would-be crawlers to email `whitelist-crawl@linkedin.com` for
authorization. A generic Playwright/Chromium session is not one of the
~30 explicitly named, allow-listed bots in that file.

**This is the central tension this report documents: what is technically
possible right now is not the same as what LinkedIn has publicly stated
is authorized.** Per this project's explicit safety principles (never
bypass access controls; prefer compliant discovery over automation where
restricted), this finding weighs heavily against building an automated
LinkedIn discovery adapter on the current evidence, independent of the
purely technical feasibility observed.

---

## A. Exact Inspection URL(s)

- **Browser inspection (1 request):**
  `https://www.linkedin.com/jobs/search?keywords=Site%20Reliability%20Engineer&location=Bengaluru%2C%20Karnataka%2C%20India`
  — a standard, publicly-documented LinkedIn Jobs search URL shape (the
  same one a logged-out visitor typing into their own browser would
  land on). Not verified against any prior capture — this inspection
  itself is the first evidence gathered for it in this project.
- **Policy check (1 request, no browser):**
  `https://www.linkedin.com/robots.txt`

## B. HTTP Status and Final URL

| | Value |
|---|---|
| Requested URL | (see A, browser inspection URL) |
| HTTP status | **200** |
| Final URL | identical to requested URL |
| Redirected (HTTP-level) | **No** |
| `robots.txt` HTTP status | **200** |

**FACT/OBSERVED**, directly captured via the `DIAGNOSTIC` stderr line
emitted during the single `page.goto()` call (same pattern this
project already uses for Naukri).

## C. Browser/Runtime Mode

| | Value |
|---|---|
| `headless` | `true` (project default; `JOBOS_BROWSER_HEADLESS` convention, unset → headless) |
| `channel` | `'chromium'` (same, unmodified project convention already used for Naukri) |
| Effective `navigator.userAgent` | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/153.0.0.0 Safari/537.36` |
| User-Agent override applied | **None** — deliberately left at Playwright's true default for this inspection, so the observation reflects LinkedIn's actual behavior toward an unmodified, honestly-identified headless browser, not a customized one |

**FACT/OBSERVED.**

## D. Authentication Requirement Observed

- The page loaded and rendered real, specific content (title: "6,000+
  Site Reliability Engineer jobs in Bengaluru") **without being
  redirected to `/authwall` or `/login`** — full authentication was
  **not** required to reach this page.
- However, the page's visible body text contains at least one of
  "sign in" / "join now" / "welcome back" (`loginWallText: true`), and
  the DOM contains 6 `<form>` elements including **1 password input**
  — i.e., a sign-in prompt/form is embedded on the page, consistent
  with LinkedIn's known pattern of showing limited public content while
  actively encouraging (not strictly forcing) sign-in.
- **Not tested in this inspection:** whether clicking through to an
  individual job detail page (one of the 61 `/jobs/view/...` links
  found) requires authentication, and whether the full, unpaginated
  result set is accessible without an account. Both remain open
  questions (see Section P).

**FACT/OBSERVED** for what was directly seen; the detail-page and
full-result-set questions are explicitly flagged as **NOT
DETERMINABLE** from this single search-page visit.

## E. Block/Challenge Status

| Signal | Result |
|---|---|
| Login-wall URL (redirected to `/authwall` or `/login`) | **No** |
| CAPTCHA / challenge / "security check" / "checkpoint" text | **No** |
| "Access denied" / "request blocked" / "403 forbidden" text | **No** |
| Empty-result-page text | **No** — real result count shown ("6,000+" jobs) |
| Rate-limit indication | **No** — single request, none expected or observed |

**No block or challenge was encountered on this one visit.**
Additional, directly-relevant page-config evidence found in the HTML
`<head>`:
- `data-is-bot="false"` — LinkedIn's own client-side bot-detection flag
  did **not** flag this specific session as a bot on this occasion.
- `data-recaptcha-v3-integration-lix-value="control"` — a reCAPTCHA v3
  integration exists in this page's feature-flag configuration
  (currently in the "control"/inactive experiment group for this
  session) — evidence the *capability* exists in LinkedIn's stack, even
  though it was not triggered here. This must not be read as "CAPTCHA
  will never appear" — only that it did not appear this one time.

**FACT/OBSERVED**, with the reCAPTCHA-capability note explicitly labeled
as evidence-of-capability, not evidence-of-behavior-under-different-
conditions (e.g. higher volume, different IP reputation, repeated
requests).

## F. Result-Page Structure

- Served by LinkedIn's own **`jobs-guest-frontend`** service
  (`data-multiproduct-name="jobs-guest-frontend"`,
  `data-service-name="jobs-guest-frontend"`,
  `<meta name="pageKey" content="d_jobs_guest_search">`) — this
  confirms the page is an intentional, LinkedIn-provided public/guest
  browsing experience, not an accidental information leak.
- Canonical URL for this content, per the page's own `<link
  rel="canonical">` tag: `https://in.linkedin.com/jobs/site-reliability-engineer-jobs-bengaluru`
  — a cleaner, SEO-friendly `/jobs/<role>-jobs-<location>` slug pattern,
  distinct from the query-parameter URL actually requested. (The
  browser's own address bar/`page.url()` still reported the originally
  requested query-string URL — this is a `<link rel="canonical">` hint,
  not an HTTP redirect; Section B's "Redirected: No" remains accurate.)
- 61 anchor elements matching `a[href*="/jobs/view/"]` were found in the
  DOM — strong evidence of a real, populated list of individual job
  postings on this page.
- A loose class-name heuristic (`[class*="job-card" i], [class*="jobs-search" i]`)
  matched only 1 element — **this heuristic was evidently too narrow or
  simply wrong for LinkedIn's actual class-naming scheme**; it should
  not be relied on, and no corrected selector is proposed here (would be
  inventing one without further evidence).

**FACT/OBSERVED** for counts and page identity; the specific per-card
DOM structure (exact class names, field layout within each card) was
**NOT determined** in this pass — the raw HTML beyond the first 4,000
characters (of 305,271 total) was not further analyzed, since doing so
would not require a new request but was not completed within this
task's scope, and is recommended as the first concrete step of Phase 5
Step 2 (see Section Q).

## G. Detail-Page Availability

61 job-detail links (`/jobs/view/...` pattern) are present in the
search-results page's HTML. **No detail page was visited in this
inspection** (out of scope: "exactly one narrowly scoped page").
Whether a detail page is reachable without login, whether it shows full
JD text, and its exact URL/ID structure are all **NOT DETERMINABLE**
from this pass.

## H. Extractable Fields (Preliminary, From This Page Only)

| Field | Status |
|---|---|
| Job title | Likely extractable — the page title and meta tags demonstrate role/location are known to the page; per-card title extraction not yet confirmed |
| Company | **Not confirmed** — no company name was specifically located/verified in the captured evidence |
| Location | Likely extractable — reflected in page title/meta ("...jobs in Bengaluru") |
| Posted date | **Not confirmed** |
| Job ID | Presumed embedded in the 61 `/jobs/view/<id>` URLs found, but not individually extracted/verified in this pass |
| Description/JD text | **Not confirmed on this page** — likely only on the (unvisited) detail page |
| Application URL | **Not confirmed** |
| Salary | **Not confirmed** — no salary-related text specifically checked for |
| Remote/work-model info | **Not confirmed** |

**Every "not confirmed" above is an honest gap, not a negative
finding** — this single page visit was not designed to extract fields
(explicitly out of scope: "do not implement fixtures or parser logic
yet"), only to observe reachability and gross structure.

## I. Freshness-Filter Feasibility

**Not tested.** No date/freshness URL parameter was attempted in this
inspection (would require a second request to compare against). LinkedIn
is publicly known to support a `f_TPR` (time-posted-range) query
parameter on its standard jobs-search URLs in general web use, but this
was **not verified live** in this task and must not be treated as
confirmed — labeled **UNKNOWN, requires validation** in Section P.

## J. Pagination Feasibility

**Not tested.** No second page or "load more" interaction was performed
(explicitly out of scope). The single page loaded showed 61 detail
links against a claimed "6,000+" total result count — implying
pagination or incremental loading exists, but its exact mechanism
(numbered pages, infinite scroll, a `start=` offset parameter, or a
guest-mode result cap) is **UNKNOWN, requires validation**.

## K. Application-URL Feasibility

**Not tested/not confirmed.** `robots.txt` separately confirms
`Disallow: /jobs/view/externalApply/` and `Disallow: /job-apply/` are
explicitly excluded from crawling for every named bot and, via the
catch-all rule, from unrecognized agents too — i.e., even if application
URLs are discoverable, LinkedIn's own policy signals they should not be
automatically crawled.

## L. Rate-Limit / Anti-Bot Observations

- No rate limiting was observed or expected from a single request.
- `data-is-bot="false"` (this session was not flagged) and
  `data-recaptcha-v3-integration-lix-value="control"` (reCAPTCHA v3
  capability present but inactive) are the only two concrete anti-bot
  signals directly observed — see Section E.
- No conclusion can be drawn about behavior under repeated/sustained
  automated access from one request; this is explicitly **not tested**
  and, per this task's constraints, must not be tested by escalating
  request volume to find out.

## M. Terms/Policy Considerations

**`robots.txt` (FACT/OBSERVED, fetched live in this task,
`https://www.linkedin.com/robots.txt`, HTTP 200, 4,862 lines):**
- Contains 77 named `User-agent:` blocks (Googlebot, Bingbot, Applebot,
  Yandex, DuckDuckBot, and ~70 more specifically-named crawlers), each
  with its own detailed allow/disallow rules.
- The **final block in the file** is:
  ```
  User-agent: *
  Disallow: /
  ```
  — a catch-all rule disallowing **the entire site** for any user agent
  not explicitly named above. A generic Playwright/Chromium session
  (identifying as `HeadlessChrome`, matching none of the ~76 named
  bots) falls under this catch-all rule.
- Immediately following that rule, the file states verbatim: *"Notice:
  If you would like to crawl LinkedIn, please email
  whitelist-crawl@linkedin.com to apply for white listing."*
- Separately (relevant regardless of the catch-all), even the
  explicitly-named bots' rule sets specifically disallow
  `/jobs-guest/`, `/jobs?runSearch*`, `/api/jobPostings/jobs*`,
  `/jsearch*`, `/job-apply/`, and `/jobs/view/externalApply/` — i.e.
  job-search and application-related paths are singled out for
  exclusion even for bots LinkedIn *does* recognize and permit
  elsewhere on the site.

**General policy knowledge (INTERPRETATION — general knowledge carried
into this task, not independently re-verified by reading LinkedIn's full
User Agreement live in this session):** LinkedIn's User Agreement and
Professional Community Policies are widely known to prohibit automated
scraping/data collection without LinkedIn's express written permission,
and LinkedIn has a well-documented history of enforcing this technically
(aggressive bot detection, rate limiting, account/IP restrictions) and
legally (including litigation over scraping, e.g. the *hiQ Labs v.
LinkedIn* case, whose outcome did not establish a general right to scrape
LinkedIn against its terms). **This paragraph is offered as background
context, not as a substitute for the live `robots.txt` evidence above,
and should not be treated as a verified re-reading of LinkedIn's current
terms in this session.**

## N. Technical Feasibility

**Feasible, as observed once:** a plain, unmodified, headless
Playwright/Chromium session (the same configuration already used for
Naukri, no customization) can reach a real LinkedIn Jobs public
search-results page, get HTTP 200, and see real job-listing links,
without hitting a login wall, CAPTCHA, or access-denied page — **on this
one occasion.** Whether this holds reliably (repeated queries, over
time, at any meaningful volume, for detail pages, for pagination) is
**not established** by a single visit.

## O. Compliance/Authorization Assessment

**What is technically possible:** reaching a public jobs-search page
once, as demonstrated above.

**What is permitted/authorized:** `robots.txt`'s catch-all
`Disallow: /` rule for unrecognized user agents, plus its explicit
invitation to email for whitelisting, is a clear, public, LinkedIn-
authored statement that **unauthorized automated crawling is not
permitted** for an agent like this project's Playwright session. This
project has not sought or obtained LinkedIn's whitelisting.

**What remains uncertain:** whether a single, human-paced, non-bulk,
read-only visit by an individual candidate's own tool (rather than a
bulk crawler) would be viewed differently by LinkedIn in practice; this
project has no basis to assume so, and `robots.txt` draws no such
distinction — its rule is agent-based, not volume-based.

**Assessment: automated LinkedIn Jobs discovery, as currently
architected (generic Playwright browser automation, no LinkedIn
partnership/API access, no whitelisting), is technically possible but
not established as authorized.** This is the same class of judgment
call this project's own principles already anticipate ("Do not assume
every site permits the same automation method... implement compliant
discovery and human handoff rather than attempting to bypass controls").

## P. Unknowns Requiring Validation

1. Whether individual job detail pages require login.
2. Whether the full/unpaginated result set is reachable without an
   account, and what mechanism governs pagination.
3. Whether a `f_TPR`-style (or other) freshness/date filter parameter
   is honored on this URL shape.
4. Exact per-card DOM structure (title/company/location/date field
   selectors) — not extracted in this pass.
5. Exact `/jobs/view/<id>` URL/ID structure — links were counted but not
   individually inspected.
6. Whether behavior changes under repeated or higher-volume access
   (explicitly not tested, and not to be tested by escalating volume
   without a fresh, explicit authorization).
7. Whether LinkedIn's enforcement of `robots.txt` differs in practice
   from its literal text (this project has no basis to assume it does,
   and does not plan to test that assumption via violation).
8. Whether a compliant alternative exists (e.g. LinkedIn's official
   Talent/Jobs API programs, which require a formal partnership and are
   outside this project's current scope) that would resolve the
   authorization question definitively.

## Q. Recommendation for the Next Implementation Step

**Do not proceed to offline implementation (Phase 5 Step 2 — fixtures,
parser, capability declarations) on the current evidence.** The
technical reachability demonstrated here is real, but `robots.txt`'s
explicit, site-wide disallow for unrecognized agents is a direct,
public statement against building an automated crawler for this source
without LinkedIn's authorization — and this project's own stated
principles ("do not bypass access controls," "prefer compliant
discovery and human handoff") point the same direction.

**Recommended next step, if this source is to be pursued further, is
not more technical inspection but a policy/scope decision by the
candidate:** either (a) pursue LinkedIn's official, sanctioned access
path (e.g. a Jobs/Talent API partnership, if one is practically
available to an individual candidate — likely not, but worth explicitly
ruling out rather than assuming), or (b) treat LinkedIn as a
**human-handoff-only** source (the candidate manually searches/applies
via their own browser and account; SarojJobOS records/tracks what the
candidate finds and applies to, rather than discovering it
automatically) — consistent with CLAUDE.md's own guidance: *"For sites
where automated interaction is restricted, implement compliant discovery
and human handoff rather than attempting to bypass controls."*

**LinkedIn remains `AdapterStatus.NOT_ENABLED`, `capabilities =
frozenset()`. This recommendation is advisory only — no status change
was made, and none should be made without your explicit decision on
how to proceed given this evidence.**

---

## Regression / Safety

- **New file:** `scripts/linkedin_discovery_probe.js` — diagnostic-only,
  mirrors this project's existing, documented convention (see
  `naukri_discovery_probe.js`/`naukri_search_probe.js`, listed in
  `CLAUDE.md` under "Naukri Playwright Probes — exploratory, not
  production adapters"). Never imported or referenced by
  `linkedin_adapter.py`, `source_registry.py`, or any production file.
- `scripts/linkedin_adapter.py`: **zero changes.** Still
  `status = AdapterStatus.NOT_ENABLED`, `capabilities = frozenset()`,
  `health_check()`/`search()` still raise `AdapterNotEnabledError`
  immediately.
- `source_registry.py`, `search_profile.py`, `search_worker.py`,
  scoring, eligibility, freshness, deduplication: **zero changes.**
- Full standalone regression suite: **39/39 pass.**
- `python3 -m py_compile` across all `scripts/*.py`: clean.

## Production DB Safety

| | SHA-256 | Row counts |
|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | identical |

**Byte-identical. `data/applications/jobos.db` was never opened for
writing in this task; no temporary DB was even needed, since this
inspection performed no candidate/job/scoring/tracking activity of any
kind.**

## Exact Live Request Count

- `robots.txt` fetch: 1 (plain HTTP GET, no browser)
- LinkedIn Jobs search page visits: 1 (single Playwright navigation)
- **Total live requests: 2**
- Retries: 0
- Login attempts: 0
- CAPTCHA solve attempts: 0
- Detail pages visited: 0
- Pagination/"load more" interactions: 0
- Application interactions: 0

**Stopping here, per instruction. Phase 5 Step 2 (offline
implementation) is NOT authorized to begin by this report — it requires
your explicit decision on the compliance question raised in Sections M
and O first.**
