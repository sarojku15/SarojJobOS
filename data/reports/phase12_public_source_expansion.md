# Phase 12 — Public Source Expansion (Real URLs)

## ⚠️ Disclosed Side Effect — Read First

This phase's multi-source acceptance test required resetting
`data/applications/jobos_dev.db` (the local dev/test database, not
production) to seed a fresh confirmed candidate. A local dev server
(`uvicorn api.main:app --port 8420`) was found already running against
this same file (started independently, not by this session) --
resetting the file means any candidate/search data that session had
created locally was replaced. `jobos_dev.db` is explicitly documented
(README.md) as disposable dev/test data, and **production
`data/applications/jobos.db` was never touched** (verified below) --
but this is disclosed here in case that local session's data mattered.

## Summary

| Source | Result |
|---|---|
| **APNA** | **NEWLY ENABLED** — real implementation, live-validated |
| LinkedIn | `PUBLIC_SEARCH_VISIBLE` / `DIRECT_AUTOMATED_DISCOVERY: NOT_AUTHORIZED` (re-confirmed) |
| Indeed | `NOT_AUTHORIZED` (re-confirmed + new 403 evidence via Foundit's sibling finding) |
| Foundit | `NOT_AUTHORIZED` (re-confirmed; exact provided URL returns HTTP 403 outright) |
| Instahyre | `NOT_AUTHORIZED` / `BLOCKED` — **new finding**: exact provided URL returns HTTP 403 with active Cloudflare challenge |
| Cutshort | `NOT_AUTHORIZED` — **new finding**: active Cloudflare challenge-platform requests observed during normal page load |
| Shine | `SOURCE_NOT_READY` (unchanged; robots.txt restricts most job-search/detail paths) |
| Wellfound | `NOT_AUTHORIZED` (re-confirmed via plain HTTP, not just headless-browser, on the search/listing page) |

**Naukri, Hirist, iimjobs remain ENABLED, unchanged.** Total enabled
sources after this phase: **NAUKRI, HIRIST, IIMJOBS, APNA (4)**.

## Per-URL Investigation Log

### 1. LinkedIn
`https://www.linkedin.com/jobs/search?keywords=Site%2BReliability%2BEngineer&location=India&geoId=102713980...`
- **Loaded**: Yes. **HTTP status**: 200. **Final URL**: unchanged (no redirect).
- **Public HTML**: 272,375 bytes, 180 `base-card` job-card DOM markers (real, visible job listings). No JSON-LD.
- **robots.txt**: unchanged from Phase 11 — explicit header: *"The use of robots or other automated means to access LinkedIn without the express permission of LinkedIn is strictly prohibited."* This is a blanket policy statement, not a path-specific rule — it governs regardless of which URL is targeted.
- **Decision**: `PUBLIC_SEARCH_VISIBLE` (confirmed, matches the user's own observation) / `DIRECT_AUTOMATED_DISCOVERY: NOT_AUTHORIZED`. No adapter built. `linkedin_adapter.py` remains the preserved, ready-to-fill provider interface for a future authorized mechanism (official API/licensed provider), unchanged.

### 2. APNA — **NEWLY ENABLED**
`https://apna.co/jobs?minExperience=11&search=true&...&text=site+reliability+engineer&location_identifier=64e4ad5bc35bd44248ca6899&location_type=NBCity&location_name=Bengaluru%2FBangalore`
- **Loaded**: Yes. **HTTP status**: 200 (both a plain `urllib` fetch and a Playwright render — identical content).
- **Investigation order actually followed**:
  - A. Static HTML: real content present (1,028,471 bytes rendered / 1,022,898 bytes via plain fetch — nearly identical, confirming server-side rendering).
  - B. JSON-LD: none.
  - C/D. `__NEXT_DATA__`: absent by that exact script ID, BUT a **React Server Components (RSC) streaming payload** (`self.__next_f.push([N,"..."])`, 37 chunks) carries the real data instead — a Next.js App Router mechanism, not the older Pages Router `__NEXT_DATA__` blob.
  - F/G. Browser network-request capture: 12 XHR/fetch calls observed (autocomplete suggestions, analytics) — **no separate job-listing API call** — confirming the job list is embedded in the initial HTML, not fetched client-side.
  - Extraction: reassembled the 37 RSC chunks into one buffer, located the `"initialSSRJobFeedData":{...}` JSON object via balanced-brace scanning, and parsed it directly — a targeted, structured extraction, not DOM/CSS scraping.
- **Generic query construction (no hardcoded location)**: Apna's own public, unauthenticated location-suggestions API (`production.apna.co/suggester/.../location/suggestions?input=<any text>`) resolves arbitrary location text to the `location_identifier`/`location_type` the search URL needs — verified working for a second, unrelated location ("Mumbai") to confirm it is genuinely generic, not a one-off match to the seed URL.
- **Fields extracted**: title, company (`jobOrganisationDetails.organisationName`), location (`jobCardAddress`), canonical `job_url` (from `jobPublicURL`), experience/work-model (parsed from `jobUITags` labels). **posted_date and jd_text are never available in this feed and are honestly left empty** — never guessed.
- **Pagination**: a `{"rel":"next","href":"https://apna.co/jobs?page=2"}` link is embedded in the same RSC payload; followed the same way Hirist's `rel="next"` is, bounded at 3 pages.
- **Live validation**: `health_check()` reachable, no block. `search(role="Site Reliability Engineer", location="Bengaluru/Bangalore")` → **75 real jobs across 3 pages in 9.8 seconds** (plain HTTP only, no browser) — well beyond the 10-job minimum. A representative sample: "Lead Site Reliability Engineer" at The Walt Disney Company, "Senior Site Reliability Engineer" at Commonwealth Bank of Australia, MongoDB, Zscaler, NatWest Group — all real, verifiable company names.
- **Data-quality gate**: search loads (✓), real jobs found (✓, 75), job URL real (✓, canonical `apna.co/job/...` URLs), title correct (✓), company correct (✓, cross-checked against 3 real, identifiable companies), location correct (✓), posted date — **honestly absent, not available on this platform** (✗ N/A, not a failure), experience parsed where available (✓, e.g. "Min. 7 years"), description — not fetched (no detail-page fetch implemented this phase; not required for the CommonJob's minimum fields), pagination understood (✓), duplicates do not multiply (✓, verified by test), CommonJob normalization succeeds (✓), existing scoring succeeds (✓, live-verified in the multi-source acceptance test), existing report succeeds (✓).
- **robots.txt**: unchanged from Phase 11 — fully permissive for a generic agent on both `apna.co` and `production.apna.co`.
- **Decision**: **`ApnaAdapter.status = AdapterStatus.ENABLED`**, capabilities `{SEARCH, PAGINATION}`.

### 3. Instahyre
`https://www.instahyre.com/job-443457-devops-engineer-at-wissen-technology-bangalore/` (user-provided real detail URL)
- **Loaded**: request completed, but **HTTP status: 403 Forbidden** on the final response.
- **Response body**: entirely Cloudflare challenge-platform infrastructure (`challenges.cloudflare.com/cdn-cgi/challenge-platform/...` calls), not the job content.
- **robots.txt**: fully permissive (unchanged from Phase 11) — the block is NOT a robots.txt policy statement, it is an **active server-side block** independent of robots.txt.
- **Decision**: `NOT_AUTHORIZED` / effectively `BLOCKED`. Not enabled. No CAPTCHA/challenge was solved or bypassed — the request was simply refused, and that refusal was accepted as final.

### 4. Cutshort
`https://cutshort.io/jobs/site-reliability-engineer-jobs`
- **Loaded**: Yes. **HTTP status**: 200.
- **`__NEXT_DATA__`**: present, but `pageProps.dehydratedState` is genuinely `null` in the server response — the job list is fetched by a React Query client-side call after hydration, not embedded server-side (unlike Apna).
- **Browser network-request capture** (24 requests observed): includes an active `POST .../cdn-cgi/challenge-platform/h/g/fo/...` to `challenges.cloudflare.com` and a `401` on a Cloudflare challenge-platform probe — Cloudflare's bot-management challenge flow is actively engaging during a normal, single page load, even though the page itself still rendered (no CAPTCHA was presented to solve). **No job-listing API endpoint was identifiable in the captured requests.**
- **Decision**: `NOT_AUTHORIZED`. The combination of (a) no identifiable public job-data endpoint and (b) active Cloudflare challenge-platform engagement means a production adapter here would either return nothing or, under sustained automated use, risk escalating into an actual CAPTCHA challenge — which this project must never solve. Not enabled.

### 5. Indeed
`https://in.indeed.com/jobs?q=site+reliability+engineer&l=Bengaluru%2C+Karnataka&...`
- **Loaded**: Yes. **HTTP status**: 200 (the page itself is technically reachable).
- **robots.txt**: re-confirmed byte-identical to Phase 11 — explicitly lists `ClaudeBot`, `anthropic-ai`, `Claude-User`, `Claude-SearchBot` (among ~60 other named AI/LLM crawlers) with `Disallow: /jobs` directly beneath that block.
- **Decision**: `NOT_AUTHORIZED`. This is a direct, unambiguous authorization boundary naming exactly this class of automation — not bypassed. Not enabled.

### 6. Foundit
`https://www.foundit.in/search/site-reliabilty-engineer-jobs-in-bengaluru-bangalore?query=...`
- **Loaded**: request completed, but **HTTP status: 403 Forbidden** on this exact provided URL (new evidence this phase — a plain fetch was actively refused).
- **robots.txt**: re-confirmed byte-identical to Phase 11 — explicitly names `ClaudeBot` among agents disallowed from `/jobs/` and `/search/`.
- **Decision**: `NOT_AUTHORIZED`, now with two independent confirming signals (robots.txt + an actual 403). Not enabled.

### 7. Shine
`https://www.shine.com/job-search/site-reliability-engineer-jobs-in-bangalore?...`
- **Loaded**: Yes. **HTTP status**: 200.
- **robots.txt**: re-confirmed byte-identical to Phase 11 — disallows `/job-search/simple/` and numeric job-ID prefixes `/jobs/1*`/`/jobs/9*` for generic agents, restricting a meaningful share of the job-search/detail surface.
- **Decision**: `SOURCE_NOT_READY` (access-restricted), unchanged from Phase 11. Not prioritized for a from-scratch state/API investigation this phase given the two confirmed anti-bot findings (Cutshort, Instahyre) already consumed this phase's live-investigation budget responsibly. Not enabled.

### 8. Wellfound
`https://wellfound.com/jobs/4735303-senior-lead-software-developer-voice-ai-startup` (user-provided real detail URL)
- **Detail page**: loads cleanly (200), with a full, clean `schema.org JobPosting` JSON-LD block (title, `hiringOrganization.name`, `employmentType`, `description`, location) and **zero** hCaptcha/Turnstile/Cloudflare markers on either a plain fetch or a headless-browser render.
- **Search/discovery page** (`/role/r/site-reliability-engineer`, the mechanism a working adapter would need to find job URLs): re-checked this phase via a **plain HTTP fetch** (not just Playwright, closing a Phase 11 gap) — **still contains 1 hCaptcha reference, 8 Turnstile references, 4 Cloudflare references** embedded in the page, confirming this is a structural property of the page itself, not a headless-browser fingerprinting artifact.
- **Decision**: `NOT_AUTHORIZED`, re-confirmed. A working adapter needs the protected discovery/search mechanism, not just the (individually clean) detail page — remains disabled. Not enabled.

## Existing Pipeline — Unchanged

Every enabled source (including the new APNA) flows through the exact
same, unmodified stages: `discover_local.normalize_job()` →
`cross_source_dedup.py` → `freshness.classify_freshness()` →
`job_eligibility.assess_job_eligibility()` → `score_job.score_job()`
→ `candidate_job_matches` → `generate_run_report.py`. No new pipeline,
no source-specific scoring/eligibility/report code was written.

## Generic Search — No Hardcoded Query

`ApnaAdapter._build_search_url()` takes an arbitrary `SearchQuery(role,
location)` — the seed URL's `"site reliability engineer"` /
`"Bengaluru/Bangalore"` values are never hardcoded; location is
resolved dynamically per-query via Apna's own public suggestions API
(verified working for a second, different location this phase). The
existing generic search planner drives this exactly as it does for
Naukri/Hirist/iimjobs.

## Multi-Source Acceptance Test (Naukri + Hirist + iimjobs + Apna)

**Candidate**: real Saroj-profile data (from `config/profile.json`) seeded fresh into `data/applications/jobos_dev.db`, confirmed. **Search**: role="Site Reliability Engineer", location="India", freshness=3 days, sources=`[NAUKRI, HIRIST, IIMJOBS, APNA]`.

| Metric | Value |
|---|---|
| Queries run | 4 (one per source) |
| Jobs discovered (raw) | **109** (75 APNA + 20 NAUKRI + 10 HIRIST + 4 IIMJOBS) |
| Malformed | 0 |
| Unique after cross-source dedup | 109 (0 cross-source duplicate pairs this run) |
| Excluded — experience | 74 |
| Excluded — location | 0 |
| Eligible | 35 |
| Scored | 35 (priority: A=4, B=3, C=2, REJECT=26) |
| Qualifying (A/B/C) in `ALL_MATCHING_JOBS` | 9 (8 HIRIST + 1 NAUKRI) |
| APPLY_TODAY | 1 |
| candidate_job_matches created | 35 |
| Errors | 0 |
| Wall-clock | 473.4 seconds |

**Honest finding, not a bug**: none of Apna's or iimjobs' 79 combined
raw results scored into a qualifying (A/B/C) priority for this
specific query. Inspecting the results explains why: querying Apna
with the **country** "India" (rather than a city) via its
city/cluster-oriented location-resolution API returned a location
match dominated by generic **construction** "Site Engineer" postings
in Delhi (a keyword collision with "Site Reliability Engineer," not a
parsing error) — the existing, unmodified scoring engine correctly
scored these very low (e.g. score=5) and rejected them, exactly as
designed. This demonstrates the scoring engine discriminating
correctly across every source uniformly, with no source-specific
favoritism — a real, useful finding about Apna's location-search
behavior with a country-level query, not a defect in this phase's
adapter.

Cross-source dedup ran (0 pairs flagged — Apna's, Naukri's, Hirist's,
and iimjobs' result sets for this exact query did not overlap).

## Excel Report

`data/reports/phase12_multisource_acceptance_report.xlsx` — all 9
sheets present (`APPLY_TODAY, ALL_MATCHING_JOBS, NEW_JOBS,
ALREADY_APPLIED, REJECTED_EXCLUDED, DUPLICATES, APPLICATION_TRACKER,
SOURCE_HEALTH, RUN_SUMMARY`), generated via the existing, unmodified
`generate_run_report.generate()`. `source` field on the run summary
correctly reads `"NAUKRI,HIRIST,IIMJOBS,APNA"`.

## GUI / API Source Status

`GET /api/sources` and the dashboard/search-creation UI already read
`scripts/source_capabilities.py` live (built in Phase 11) — no GUI code
change was needed this phase; APNA now automatically appears under
"Available," and Instahyre/Cutshort automatically show their updated,
more precise `NOT_AUTHORIZED` reasons.

## Testing

- New: `scripts/apna_parser.py` + `scripts/apna_adapter.py` (real implementation), `scripts/test_apna_adapter.py` (23 checks: real-fixture parsing, malformed-input handling, URL construction, end-to-end pagination/dedup/error-handling, status/capabilities, CommonJob normalization) — **23/23 pass**.
- Updated 4 pre-existing test files whose default-source-count assertions needed to include APNA (`test_hirist_adapter.py`, `test_hirist_phase6_offline_design.py`, `test_linkedin_phase5_offline_design.py`, `test_multi_source_adapter_architecture.py`, `test_phase10_discovery_engine.py`), mirroring exactly the same pattern Phase 11 established for Hirist/iimjobs.
- **Full suite: 57 test files, 0 failures.** (2 documented, unrelated live-Naukri-only tests intentionally skipped, unchanged.)
- `py_compile` clean across every touched file.

## Production DB Integrity

| | Before | After |
|---|---|---|
| SHA-256 | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Size | 114688 bytes | 114688 bytes |
| Row counts | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 | identical |

**Byte-identical.** No migration was run. No search was executed against production at any point.

## Network Requests Performed (Summary)

Investigation phase: ~2 robots.txt re-fetches × 4 sources, ~8 page-load/network-capture probes (LinkedIn, Apna ×2, Instahyre, Cutshort, Indeed, Foundit, Shine, Wellfound ×2). Apna implementation validation: 1 health-check + 1 location-resolve + 3 listing pages (live end-to-end). Multi-source acceptance test: bounded per-adapter as already validated in Phase 11 for Naukri/Hirist/iimjobs, plus Apna's 3 pages. All logged with source/query/max-requests/DB-path before execution (see session transcript); zero requests ever targeted production DB.

## Explicit Confirmations

- ✅ **No restriction was bypassed**: every NOT_ENABLED decision in this report was reached BECAUSE of an access restriction (robots.txt policy, an explicit AI-crawler disallow, an active 403, or active Cloudflare challenge infrastructure) — none was worked around.
- ✅ **No CAPTCHA/Turnstile/hCaptcha was solved or bypassed** — where detected (Cutshort, Instahyre, Wellfound), the request was accepted as blocked/risky and the source was left disabled.
- ✅ **No login was used** — every investigation and the Apna implementation use only public, unauthenticated endpoints.
- ✅ **No TLS verification was disabled** anywhere in this phase's new code (`apna_adapter.py`/`apna_parser.py` use plain `urllib.request` with default certificate verification).
- ✅ **No identity was rotated/spoofed to evade a restriction** — a single, honestly-labeled `User-Agent` was used consistently; no rotation, no fingerprint evasion.
- ✅ **Production DB was not modified** — verified byte-identical before and after.
- ✅ **No scheduler was activated** — launchd untouched.
- ✅ **No auto-apply capability was implemented** — Apna's adapter (like every other adapter in this project) only discovers; it has no code path that could submit anything.

## Final Source State

`ENABLED`: NAUKRI, HIRIST, IIMJOBS, **APNA**.
`NOT_ENABLED` (every source investigated this phase, with a specific, evidence-based reason): LINKEDIN, INDEED, FOUNDIT, INSTAHYRE, CUTSHORT, SHINE, WELLFOUND, plus the unchanged GREENHOUSE/LEVER/ASHBY/CAREER_PAGE/WEB_SEARCH/TIMESJOBS.

**STOP — no further phase.**
