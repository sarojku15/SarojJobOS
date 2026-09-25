# Phase 11 — Public Multi-Source Discovery: Completion Report

## Source Status Table

| Source | Public Discovery | Acquisition Method | Live Validated | Enabled | Reason |
|---|---|---|---|---|---|
| NAUKRI | Yes | adapter | Yes (prior phases, unchanged) | **YES** | Existing, unmodified |
| **HIRIST** | Yes | adapter | **Yes — this phase** | **YES (newly enabled)** | Fixed: two-stage listing+detail-page fetch (see §Hirist Fix below) |
| **IIMJOBS** | Yes | adapter | **Yes — this phase** | **YES (newly enabled)** | New: shares Hirist's platform, same fix applied |
| LINKEDIN | Yes (human browser) | web_search_discovery/provider | No | NO | robots.txt: blanket "automated access... strictly prohibited without express permission" (re-confirmed fresh) |
| INDEED | Yes (human browser) | web_search_discovery/provider | No | NO | robots.txt explicitly names ClaudeBot/anthropic-ai/Claude-User/Claude-SearchBot and disallows them from `/jobs` |
| FOUNDIT | Partial | web_search_discovery/provider | No | NO | robots.txt explicitly names ClaudeBot (+ other AI bots) and disallows `/jobs/`, `/search/` |
| WELLFOUND | Yes | provider | No | NO | robots.txt disallows `/search`; page embeds active Cloudflare Turnstile + hCaptcha infrastructure |
| CUTSHORT | Yes | provider | No | NO | robots.txt permits; job data lives in a Next.js React Query `dehydratedState` blob, not extracted this phase |
| SHINE | Partial | provider | No | NO | robots.txt disallows most job-search/detail paths for generic agents |
| INSTAHYRE | Yes | provider | No | NO | robots.txt fully permissive; no server-rendered job data found (client-rendered SPA) |
| TIMESJOBS | Yes | provider | No | NO | robots.txt permissive; site's own TLS certificate fails standard verification |
| APNA | Yes | provider | No | NO | robots.txt fully permissive; job data client-rendered, no server-side data found; new source registered as skeleton per explicit request |
| GREENHOUSE | N/A (per-company) | provider | No | NO | Real implementation (unchanged since Phase 10); zero company boards configured |
| LEVER | N/A (per-company) | provider | No | NO | Real implementation (unchanged since Phase 10); zero company boards configured |
| ASHBY | N/A (per-company) | provider | No | NO | Real implementation (unchanged since Phase 10); zero company boards configured |
| CAREER_PAGE | N/A | provider | No | NO | Generic skeleton, unimplemented (unchanged) |
| WEB_SEARCH | N/A | web_search_discovery | No | NO | Backend process cannot invoke a web-search tool at runtime (unchanged since Phase 10) |

**3 sources enabled: NAUKRI, HIRIST, IIMJOBS.** Glassdoor not added (per instruction).

## Public Accessibility vs. Automated-Acquisition Permission — The Distinction Applied

The task correctly noted these are different questions. Applied per source:

- **LinkedIn / Hirist**: both have genuinely public job pages, exactly as the user's URLs demonstrated. LinkedIn's own robots.txt draws the line at *automated* access ("strictly prohibited... without express permission") regardless of path — this is a permission question, not a data-quality one, so it stays NOT_ENABLED no matter how good a parser could be built. Hirist's robots.txt draws no such line for the general job-listing paths (only technical/admin paths are disallowed, `Crawl-delay: 10` is honored) — its historical NOT_ENABLED status was purely a **data-quality** problem, which is exactly the kind of problem this phase was able to fix.
- **Indeed / Foundit**: both public and largely permissive in their generic `User-agent: *` block, but each robots.txt separately, explicitly names AI/LLM crawlers (`ClaudeBot`, `anthropic-ai`, `Claude-User`, `Claude-SearchBot`, `GPTBot`, `CCBot`, etc.) and disallows exactly the job-listing paths for those named agents. This is a direct, unambiguous, deliberate authorization boundary for the class of automation this project represents — treated as a hard NOT_AUTHORIZED regardless of what a generic custom User-Agent string might technically slip past.
- **Wellfound**: public pages, but the live page itself embeds active Cloudflare Turnstile/hCaptcha challenge infrastructure. Discovery being *technically* reachable today does not mean it is *safely automatable* — a scraper that trips this defense would either have to solve a CAPTCHA (forbidden) or start failing unpredictably. Treated as NOT_AUTHORIZED for reliable automation.
- **Cutshort / Instahyre / Apna**: public, permissive robots.txt, no anti-bot signal found — but no server-rendered job data reachable without new JS-rendering infrastructure this phase didn't build. These are SOURCE_NOT_READY (an engineering gap), not an authorization gap — a natural next-phase candidate if prioritized.
- **TimesJobs**: public and permissive, blocked only by an unrelated TLS certificate issue on the site's own end.

## The Hirist Fix (Tier 1, explicitly requested)

**Root cause, finally isolated this phase**: earlier phases correctly found the listing page's flat `name` string (e.g. `"Verint - Senior DevOps Engineer"`) mixes 3+ conventions with no deterministic split rule. What was missing was *why*: a live DOM inspection this phase found Hirist's listing cards **do not render a company name at all** for most postings (many are recruiter/agency-anonymized) — the ambiguity was never fixable by parsing the string harder, because the data genuinely isn't there.

**The fix**: a live inspection of one job's own **detail page** found a complete `schema.org JobPosting` JSON-LD block with a clean, unambiguous `hiringOrganization.name` — plus a `data-testid="company-name"` DOM anchor confirming it independently. `HiristAdapter.search()` now:
1. Fetches the listing page(s) for **title + job_url only** (`hirist_parser._parse_job_entry_title_only()` — no string-splitting attempted at all).
2. Fetches **each job's own detail page** and reads `hiringOrganization.name`, `jobLocation`, `datePosted`, `employmentType`, `description` via the new `hirist_parser.parse_job_detail_json_ld()`.
3. Drops (never fabricates) any job whose detail page yields no resolvable company.

**Validation, in order**:
- 1 live listing-page fetch (20 real entries).
- 1 live detail-page fetch (confirmed the JobPosting shape).
- **10 real detail pages fetched and parsed — 10/10 correct**, including a genuinely anonymized posting (`"Verified Company"`, Hirist's own honest placeholder, preserved verbatim) and a listing whose title had **zero** `" - "` delimiters at all (proving the old heuristic could never have recovered it under any splitting rule).
- **Full end-to-end live validation via the real adapter + real Playwright fetcher**: 14 requests (1 health-check + 3 listing pages + 10 detail pages), **every one status 200, zero blocks/CAPTCHA**, 10/10 jobs correctly resolved. 182 seconds wall-clock (10s `Crawl-delay` honored between every fetch).

`HiristAdapter.status` is now `AdapterStatus.ENABLED` with capabilities `{SEARCH, DETAIL, PAGINATION}`.

## iimjobs (Tier 2, "validate next if practical" — it was)

Investigating iimjobs (identical robots.txt to Hirist: same admin-path disallows, same `Crawl-delay: 10`, same Yandex block) found it shares Hirist's **exact underlying platform** — byte-identical schema.org `ItemList`/`JobPosting` shapes, even the same S3 asset bucket for company logos. `iimjobs_parser.py` reuses `hirist_parser.py`'s generic schema.org logic directly (no duplicated parsing code); `iimjobs_adapter.py` mirrors `HiristAdapter`'s two-stage pipeline. Live-validated end-to-end: 6 requests (1 health-check + 1 listing + 4 detail pages), all status 200, zero blocks, 4/4 jobs correctly resolved (including one where the listing title read `"J - Senior Manager..."` but the detail page correctly resolved company=`"Sigma Consultants"` — the fix generalizes). Now `AdapterStatus.ENABLED`.

## Sources Investigated and Deferred (with exact reasons)

See the Source Status Table above for the one-line reason on each. In more detail:

- **Cutshort**: `__NEXT_DATA__` present with a React Query `dehydratedState` object that almost certainly contains the job list, but extracting a reliably-shaped job dict from a dehydrated query-cache blob (vs. a stable, documented `ItemList`) needs more parser engineering than this phase's remaining scope allowed.
- **Instahyre / Apna**: both checked at the static-HTML level (after a full Playwright render, for Apna) with no `ItemList` JSON-LD and no other detectable server-rendered job array — a full implementation would need new site-specific selectors or a different data channel, not investigated further.
- **Shine**: robots.txt itself disallows enough of the job-search/detail surface (`/job-search/simple/`, `/jobs/1*`, `/jobs/9*`) that a compliant implementation would be working with a meaningfully restricted subset; not prioritized further given Tier ordering.
- **TimesJobs**: blocked purely by a TLS certificate problem on the site's own end, unrelated to permission or data quality; not investigated further.
- **Greenhouse/Lever/Ashby**: unchanged from Phase 10 — real, working implementations against each platform's public API exist, but `config/career_pages.json` intentionally ships with zero real company boards (no company is ever hardcoded into this generic product), so there is nothing safe to live-validate against this phase.

## Multi-Source Acceptance Test (Section 18)

**Candidate**: a real Saroj-profile candidate seeded into `data/applications/jobos_dev.db` from `config/profile.json`'s actual data (name, 11 years experience, real cloud/kubernetes/IaC/CI-CD/observability skill lists) — confirmed via `promote_to_confirmed()`, never production.

**Search**: role = `"Site Reliability Engineer"`, location = `"India"`, freshness = 3 days, sources = `["NAUKRI", "HIRIST", "IIMJOBS"]` (only the sources that passed live validation).

**Safety log printed before execution** (per Section 21): source list, query, database path (`data/applications/jobos_dev.db`, never production), expected request bounds — all confirmed in the session transcript.

**Result** (via the real, unmodified `search_worker.run_once()` → `generate_run_report.generate()`):

| Metric | Value |
|---|---|
| Queries run | 3 (one per source) |
| Jobs discovered (raw) | 34 (20 NAUKRI + 10 HIRIST + 4 IIMJOBS) |
| Malformed / dropped | 0 |
| Unique after cross-source dedup | 34 (0 cross-source duplicate pairs found this run) |
| Excluded — experience | 14 |
| Excluded — location | 0 |
| Eligible | 20 |
| Scored | 20 (priority distribution: A=7, B=3, C=2, REJECT=8) |
| APPLY_TODAY (report-level) | 4 |
| candidate_job_matches created | 20 |
| Errors | 0 |
| Wall-clock | 462.1 seconds |

Every result row carries `source`, `title`, `company`, `location`, `job_url`, `posted_date`/`freshness`, `score`, `priority`, `eligibility`, and a match-reason (via the existing `score_explanation` -- reused, not reimplemented) -- exactly the acceptance criterion's required field set. One representative row (`HIRIST`, "Hitachi Solutions - Site Reliability Engineer - Azure Infrastructure", company **correctly** resolved as "Hitachi Solutions", score 95, priority A) and a second (`HIRIST`, listing title mentions "NCR Voyix" but the detail page correctly resolves the *actual* hiring organization as "Terafina" -- a real-world case the old heuristic would have gotten wrong) confirm the fix is working as designed, not just on the validation fixtures.

Cross-source dedup ran (0 pairs flagged this run — the three sources' result sets for this exact query happened not to overlap; the dedup mechanism itself is unchanged, unit-tested, and already proven in Phase 8's multi-source dedup tests).

## Excel Report (Section 19)

Generated via the existing, unmodified `generate_run_report.generate()` — no second implementation. `data/reports/phase11_multisource_acceptance_report.xlsx`, all 9 sheets present and populated: `APPLY_TODAY, ALL_MATCHING_JOBS, NEW_JOBS, ALREADY_APPLIED, REJECTED_EXCLUDED, DUPLICATES, APPLICATION_TRACKER, SOURCE_HEALTH, RUN_SUMMARY`. `ALL_MATCHING_JOBS`'s `Source` column correctly shows `HIRIST`/`NAUKRI`/`IIMJOBS` interleaved and sorted by priority/score together — multi-source reporting confirmed working with zero source-specific report code.

## GUI Verification (Section 20)

Full click-through path re-verified this phase (via existing Phase 9/10 test suites, still 100% passing, plus a fresh live-server spot-check specifically for source visibility): candidate creation → resume upload/skip → profile review/edit → saved-search creation → **source selection now correctly lists NAUKRI, HIRIST, and IIMJOBS as available**, with every other source shown under "Unavailable" with its exact fresh reason → run (QUEUED→RUNNING→COMPLETED) → results with source/score/priority/application-status filters → `[Open Job]` external link only, no apply button anywhere → Excel download. `/`, `/profile`, `/searches`, `/searches/new`, `/dashboard` all confirmed returning HTTP 200 on a live server this phase.

## Web Search Status (Section 14)

Unchanged from Phase 10: `WEB_SEARCH` remains `NOT_ENABLED`. This backend runs as a plain FastAPI/uvicorn process with no mechanism to invoke Claude Code's own WebSearch/WebFetch tools at runtime, and no search-API credential is configured. The provider interface, bounded query-string generation, and quality-control validation all exist and are tested; nothing changed here this phase because nothing about the underlying boundary changed.

## Testing (Section 22)

- New offline test files this phase: `test_hirist_phase11_detail_fetch.py` (real-capture regression, 7 checks), `test_iimjobs_adapter.py` (11 checks), `test_apna_adapter.py` (7 checks) — all passing, zero live calls.
- Updated `test_hirist_adapter.py` (14 scenarios, all passing against the new two-stage pipeline, using newly-separated `phase11_*` fixtures so the historical `valid_results_page1/2.html`/`duplicate_jobs.html`/`pagination_self_loop.html` regression fixtures were left byte-identical to their pre-Phase-11 state).
- Updated 6 other pre-existing test files whose assertions assumed only Naukri was ever default-enabled (`test_search_submission.py`, `test_multi_source_adapter_architecture.py`, `test_hirist_phase6_offline_design.py`, `test_hirist_phase8_offline_gate.py`, `test_linkedin_phase5_offline_design.py`, `test_phase10_discovery_engine.py`) — each fix either pins an explicit source list (where the test's real intent was unrelated to default-source membership) or updates the expected default set to `{NAUKRI, HIRIST, IIMJOBS}` (where that membership was genuinely the thing being tested), with an inline comment explaining why in every case.
- **Full suite: 56 test files run, 0 failures.** (2 documented, unrelated live-Naukri-only tests intentionally skipped, exactly as every prior phase.)
- `py_compile` clean across every `scripts/*.py`, `api/*.py`, and the discovery-engine Skill's scripts.
- Every `config/*.json` file (including the newly edited `site_adapters.json` and `career_pages.json`) parses as valid JSON.

## Production DB Integrity (Section 23)

| | Before | After |
|---|---|---|
| SHA-256 | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Size | 114688 bytes | 114688 bytes |
| Row counts | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 | identical |

**Byte-identical.** Checked before any Phase 11 work began, after the Hirist/iimjobs live validations, after the multi-source acceptance test, and after the full test suite run. All live/development work used `data/applications/jobos_dev.db` exclusively.

## Scheduling (Section 24)

launchd was not installed, loaded, or started. No production background scheduler was created. Manual execution (this phase's own scripted runs) remains the only activation mechanism.

## Final Product Status (Section 25/27)

**YES** — a user can open the GUI, create a search for "Site Reliability Engineer" / "India" / experience 11 / freshness 3 days, select from the sources actually shown as available (Naukri, Hirist, iimjobs), run it, and receive one unified result set where every job has source, title, company, location, URL, posted date, freshness, eligibility, score, priority, and match reason — then download the existing 9-sheet Excel report. Confirmed end-to-end this phase, live, not simulated.

**Final state, matching the task's own success criteria exactly:**
- Naukri = ENABLED (unchanged)
- Hirist, iimjobs = ENABLED (this phase, real live-validation gate passed)
- Every other public source = NOT_ENABLED, each with a specific, freshly-checked reason (permission, anti-bot, or engineering gap — never "just because")
- ATS (Greenhouse/Lever/Ashby) = real implementations, safely NOT_ENABLED pending a configured company board
- Web Search = provider interface implemented; not callable in this runtime, boundary explicit
- GUI = working, verified live
- Multi-source discovery = working, verified live (34 jobs, 3 sources, one report)
- Existing scoring = preserved, untouched
- Existing cross-source dedup = preserved, untouched, exercised successfully
- Existing 9-sheet Excel = working, unmodified, multi-source rows confirmed
- Human approval = preserved (no code path submits anything)
- Auto-application = never (re-confirmed: zero application-submission code anywhere in this codebase)
- Production DB = byte-identical throughout
- launchd = untouched

**STOP per Section 27 — no further phase, no further source-enablement push.**
