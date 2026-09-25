# Phase 5 Step 2 — LinkedIn Offline Integration Design

**Design/specification only. No code implements this design. No live
LinkedIn request was made in this task.**

`LinkedIn` remains, unchanged: `AdapterStatus.NOT_ENABLED`,
`capabilities = frozenset()`. `search_profile._default_sources()`
remains `['NAUKRI']`. This document is grounded entirely in (a) the
existing, unmodified adapter architecture inspected in earlier Phase 4
work, and (b) the factual findings of
`data/reports/linkedin_phase5_inspection.md` (Phase 5 Step 1). It
invents no selector, endpoint, API, or capability beyond what those two
sources already establish or what is explicitly labeled below as
"presumed, pending official API documentation."

---

## 1. Proposed LinkedIn Adapter Boundary

**Class:** `LinkedInAdapter(JobSourceAdapter)` in `scripts/linkedin_adapter.py`
— the same file that exists today as a `NOT_ENABLED` skeleton. No new
file or class name is proposed; a future implementation would extend
this exact file in place, the same way `NaukriAdapter` already
implements the shared `JobSourceAdapter` interface.

**Methods it would eventually implement** (same abstract contract every
adapter already implements — `scripts/source_adapter.py`):
- `health_check(self) -> AdapterHealth` — a single, cheap, read-only
  reachability/auth-validity check (e.g. a lightweight authenticated
  "who am I" or account-status API call, if the eventual official API
  offers one), never a full search.
- `search(self, query: SearchQuery) -> list[dict]` — one query in,
  one list of raw job dicts out, matching the exact shape
  `discover_local.normalize_job()` already expects (Section 4). Must
  raise `AdapterBlockedError`/`AdapterTimeoutError` on failure, exactly
  like `NaukriAdapter` does today — never return a silently-empty list
  to mean "something went wrong."

**Responsibilities (in scope for the adapter):**
- Constructing a LinkedIn-specific query (whatever the eventual
  official API's request shape actually is).
- Making the request via an injectable fetcher object (mirroring
  `NaukriAdapter(fetcher=...)`'s constructor pattern, so tests can
  substitute a fake fetcher with zero network/credentials involved —
  see Section 7).
- Classifying the raw response into a small, LinkedIn-specific set of
  states (mirroring `naukri_parser.SearchPageState`), and raising the
  appropriate existing exception type.
- Mapping the official API's own response fields into the flat raw-job
  dict shape (Section 4) — and only that shape; no adapter-specific
  fields leak downstream.
- Declaring `status` and `capabilities` truthfully, and only once each
  has been demonstrated, not aspirationally (per `CLAUDE.md`'s existing
  rule, unchanged by this design).

**What must remain OUTSIDE the adapter** (unchanged from every existing
adapter's contract):
- Scoring (`score_job.py`) — an adapter never computes a match score.
- Eligibility (`job_eligibility.py`, `experience_eligibility.py`,
  `location_taxonomy.py`) — an adapter never decides who is or isn't
  eligible for a job it found.
- Deduplication (`discover_local.deduplicate()`,
  `cross_source_dedup.py`) — an adapter never suppresses or merges
  jobs; it returns everything it found for the query, and the existing,
  unmodified downstream pipeline dedupes.
- Freshness classification (`freshness.py`) — an adapter may pass
  through a `posted_date` string; it never computes `FreshnessCategory`
  itself.
- Persistence (`tracker.py`, `search_worker.py`'s upsert functions) —
  an adapter never writes to the database.
- Application submission of any kind — completely out of scope for a
  discovery adapter under this project's non-negotiable human-approval
  rule.
- Deciding whether to retry, escalate, or route around a block —
  exactly like Naukri's adapter today, a block is reported upward, not
  worked around.

---

## 2. Proposed Capabilities

| Capability | Classification | Basis |
|---|---|---|
| `SEARCH` | **Potentially available through an official API** | Phase 5 Step 1 showed a public guest search page exists and returns real results, but that access path is not authorized (robots.txt). An official API's search capability is presumed to exist (LinkedIn's Talent/Jobs partner APIs are publicly known to offer search-like functionality), but this project has not seen or been granted access to that API's actual documentation. |
| `DETAIL` | **Unknown** | 61 detail links were observed on the guest search page, but no detail page was visited (Phase 5 Step 1, Section G/P). Whether an official API separately exposes full detail content, and in what shape, is not established. |
| `NATIVE_FRESHNESS` | **Unknown** | A `f_TPR`-style parameter is publicly rumored/commonly documented for LinkedIn's consumer-facing search URL, but this was explicitly **not verified live** in Step 1 (flagged as an unknown there) and is not confirmed for any official API either. |
| `PAGINATION` | **Unknown** | The guest page implied pagination/result-capping exists (61 links shown against a "6,000+" claimed total) but the exact mechanism was not determined (Step 1, Section J). |
| `APPLICATION_URL` | **Not applicable to guest access; unknown for an official API** | `robots.txt` explicitly disallows `/jobs/view/externalApply/` and `/job-apply/` even for named, permitted bots — guest/public access to application URLs is explicitly out of policy scope. Whether an official API surfaces a legitimate application URL is unknown. |
| `COMPANY_METADATA` | **Unknown** | Not confirmed extractable even from the one guest page visited (Step 1, Section H); no basis to assume an official API's shape. |
| `SALARY` | **Unknown** | Not confirmed on the guest page; `canonical_job.CanonicalJob` already has `salary_min`/`salary_max`/`salary_currency`/`salary_period` fields reserved and always `None` today (no adapter populates them) — LinkedIn could plausibly be the first to, if an official API exposes salary, but this is unconfirmed. |
| `REMOTE_FILTER` | **Unknown** | Not tested at all in Step 1. |
| `LOCATION_FILTER` | **Confirmed by current inspection (partially)** | The one guest page visited *did* honor a location value in its URL/title ("...jobs in Bengaluru") — this is the only capability with any direct, positive evidence from Step 1. Still not confirmed for an official API, and not confirmed to mean a structured filter versus simple keyword matching. |

**None of these may be marked `confirmed` for a future `LinkedInAdapter`
until demonstrated the same way Naukri's were: by a real implementation
passing its own offline tests and live validation gates (Section 8).**
This table is a planning aid, not a pre-approval of any capability.

---

## 3. Search Input Mapping

Existing `SearchQuery` fields (`scripts/source_adapter.py`, unchanged):
`role`, `location`, `experience_years`, `exclude_keywords`, `extra`,
`max_job_age_days`.

| `SearchQuery` field | Proposed LinkedIn mapping |
|---|---|
| `role` | Would map to whatever the official API's keyword/title-query parameter is. No parameter name is proposed here — none has been seen. |
| `location` | Would map to whatever the official API's location parameter is. The one Step 1 observation (Section F) suggests a free-text or geo-ID-based location parameter is plausible, consistent with the guest URL's `location=` query string, but the *official API's* actual parameter shape is unknown. |
| `experience_years` | LinkedIn's consumer UI is publicly known to support an "experience level" facet (entry/associate/mid-senior/director/executive) rather than a raw numeric year filter — if true for the official API too, this would need a small, explicit, documented mapping from this project's numeric `experience_years` to LinkedIn's bucketed levels. Not verified; presumed only. |
| `exclude_keywords` | No LinkedIn-side negative-keyword mechanism has been observed or is presumed; this would most likely need to remain a **client-side, post-fetch filter** applied by the adapter itself (or left to the existing, unmodified `discover_local`/eligibility layers), not something sent to LinkedIn. |
| `max_job_age_days` | Would map to a native freshness parameter **only if `NATIVE_FRESHNESS` capability is confirmed** (Section 2 — currently `Unknown`). Until then, this field would be silently unsupported for LinkedIn (identical to how `_map_to_naukri_freshness_option()` documents "None means unrestricted" as the safe default when a source doesn't support it — the same fallback discipline, not a new one). |
| `extra` | Reserved, adapter-specific escape hatch — already used by `naukri_adapter.py` for `extra["naukri_search_url"]`. A future LinkedIn adapter could use `extra["linkedin_search_url"]` the same way, for the same reason (a verified override for one specific query that a general slug-construction function gets wrong). |

**Pagination as a search input:** not a `SearchQuery` field today for
any adapter (Naukri is "page 1 only"). If LinkedIn's official API
supports pagination and this project wants more than one page, that
would require a new, generically-named `SearchQuery` field or a
per-adapter internal loop — a genuine, small architecture decision
deferred to actual implementation time, not decided here.

---

## 4. Normalized Job Mapping

Target shape: exactly what `discover_local.normalize_job()` already
consumes and produces today (unchanged) — the same shape every adapter,
including Naukri, must produce as raw dicts:

```
source, company, title, location, work_model, job_url,
application_url, posted_date, jd_text, experience_required,
mandatory_skills, preferred_skills, experience_min_months (optional)
```

| Target field | Proposed LinkedIn source | Confidence |
|---|---|---|
| `source` | Literal `"LINKEDIN"` | Certain (matches `ADAPTERS` key, already registered) |
| `company` (→ `job_id.py`'s `company` for fallback ID) | Official API's company/organization name field | Presumed field exists; exact name unknown |
| `title` | Official API's job title field | Presumed field exists; exact name unknown |
| `location` | Official API's location field, formatted as free text (matching what `location_taxonomy.parse_job_location_and_work_model()` already expects — no new location taxonomy needed) | Presumed |
| `work_model` | Mapped to this project's existing `location_taxonomy.WorkModel` values (`REMOTE`, `HYBRID`, `ONSITE`, `UNKNOWN`) — **only if** the official API exposes a comparable signal; otherwise left `""` (→ `UNKNOWN`, the existing safe default) | Unknown whether source data exists |
| `job_url` | The canonical, public job-detail URL (`job_id.py`'s `job_id_from_url()` already derives a stable ID from this — no new ID scheme needed) | Presumed exists (61 such URLs were seen even in guest mode) |
| `application_url` | Only populated if a genuinely public, policy-compliant application URL is available (Section 2 — currently marked not-applicable for guest access, unknown for an official API); otherwise left `""`, exactly like a source that doesn't support `APPLICATION_URL` today | Unknown |
| `posted_date` | Official API's raw posted-date/timestamp field, passed through as-is (this project's existing `freshness.classify_freshness()` already parses varied human-readable and relative date text — no new date parser is proposed) | Presumed exists |
| `jd_text` | Official API's full description field, if provided | Unknown whether full text vs. truncated/teaser text is available |
| `experience_required` | Official API's experience-level field, converted to the same free-text shape `experience_eligibility.parse_experience_text()` already parses (e.g. "2 - 5 years") — **only if** a numeric range is derivable from LinkedIn's bucketed experience levels; otherwise left `""` | Unknown, needs an explicit bucket→range mapping decision at implementation time |
| `mandatory_skills` / `preferred_skills` | Official API's skills/requirements field, if any, split into these two lists using the same judgment call every adapter already makes (Naukri's `parse_detail_page()` already does this from free text) — **never invented** if the API doesn't distinguish mandatory vs. preferred | Unknown |
| `experience_min_months` (optional) | Derived the same way Naukri already derives it, if a numeric experience field exists | Unknown |
| *(not a `normalize_job()` field, but relevant)* `salary_min/max/currency/period` | Would need a **new**, explicit extension to the raw-job dict and to `discover_local.normalize_job()`/the `jobs` table if LinkedIn is the first source to actually provide salary data — `canonical_job.CanonicalJob` already reserves these fields but nothing populates them today. **This is a real, out-of-scope-for-this-document schema question**, not decided here. |

**Source metadata:** any official-API field with no dedicated column
above would flow into `canonical_job.py`'s existing, unmodified
`source_metadata` catch-all (already designed for exactly this — "no
source-provided information is ever silently dropped, even if this
module has no dedicated canonical field for it yet").

---

## 5. Authentication/Authorization Boundary

| | Public guest access (Step 1) | Official API access (this design) |
|---|---|---|
| What was actually tested | One anonymous, unauthenticated page visit | Nothing — no official API access exists for this project today |
| Credentials involved | None | Would require LinkedIn-issued API credentials (client ID/secret or partner token, per whatever program governs the specific API surface — Jobs API, Talent Solutions, etc.) obtained through LinkedIn's own official partner/developer process |
| Where credentials would live | N/A | `.env` (already the project's designated location for secrets — `CLAUDE.md`: "credentials must never be committed to Git"), read the same way this project already avoids hardcoding secrets elsewhere |
| Authorization basis | **None** — `robots.txt`'s catch-all disallow, no partnership | Would be LinkedIn's own issued grant/contract for the specific API program |
| What the application must never attempt to bypass | Login walls, CAPTCHA, `robots.txt` disallow rules, rate limits, any anti-bot control — **already this project's non-negotiable, project-wide rule** (`CLAUDE.md` "Application Safety"), not something newly introduced by this design | Same rules apply identically once/if official access exists — official access does not relax any of these; it replaces "no authorization" with "documented, granted authorization" for the *same* non-bypass discipline |

**This boundary is the single most load-bearing fact in this whole
design:** everything in Sections 2–4 above describes what a
*future, officially authorized* integration could look like. None of it
authorizes building against the guest/public surface observed in Step
1 — that surface remains explicitly not sanctioned for automated use
(Phase 5 Step 1's own conclusion, unchanged here).

---

## 6. Error/Status Model

Mapped onto the existing, unmodified `AdapterStatus` /
`SourceRunState` model (`scripts/source_adapter.py`) — no new enum
values proposed:

| Future LinkedIn condition | `AdapterStatus` | `SourceRunState` / exception behavior |
|---|---|---|
| Today — no implementation exists | `NOT_ENABLED` (current, unchanged) | `discover_from_sources()` skips it pre-flight, zero network calls (already true, already tested) |
| Official access sought but not yet granted, or credentials not configured | `REQUIRES_AUTH` | Adapter's `health_check()` would report `reachable=False`; a future decision would determine whether `REQUIRES_AUTH`, like `NOT_ENABLED`, should also be a pre-flight, zero-network skip in `discover_from_sources()` — **this is a real, undecided design question flagged here, not resolved**, since today only `NOT_ENABLED` has that special pre-flight treatment |
| Credentials configured, API call fails auth (expired/revoked token, wrong scope) | Adapter raises `AdapterBlockedError(source, BlockReason.LOGIN_WALL or a closest-fit reason)` at request time, same contract Naukri already uses | `state.blocked = True`, remaining queries for that source skipped — identical existing behavior |
| API confirms this project is not permitted for a given operation (e.g. a scope/plan limitation) | `UNSUPPORTED` (for that specific capability) or `AdapterBlockedError` at request time | Would need per-capability granularity if some operations are permitted and others aren't — a real design decision for implementation time |
| Access previously worked, now persistently failing/blocked | `BLOCKED` | Adapter raises `AdapterBlockedError`; a human reviews before any re-attempt — mirrors this project's Naukri incident-response pattern throughout this whole project's Naukri investigation history |
| Rate limited | Existing `BlockReason.RATE_LIMITED` (already defined, unused by any adapter today) via `AdapterBlockedError` | `state.blocked = True`; **no retry-with-backoff is proposed** — this project's explicit rule ("no retries designed to defeat blocking") applies identically to a rate limit as to any other block |
| API temporarily unavailable (5xx, timeout) | N/A (transient) | `AdapterTimeoutError` — existing, unmodified behavior: that one query is skipped, remaining queries for the source continue (exactly Naukri's existing timeout semantics) |
| Everything working, fully validated | `ENABLED` | Only after Section 8's gate is satisfied |

---

## 7. Testing Strategy (Design Only — Not Implemented)

Mirroring this project's existing testing principle (`CLAUDE.md`:
"Unit tests for normalization, Fixture/test data — static JSON, not live
website calls, Deduplication tests, Failure handling, Source-specific
parsing tests"). A future implementation would need offline fixtures
for:

1. **Valid search response** — a static, hand-constructed (or, once
   authorized, one real captured-and-scrubbed) JSON/HTML fixture
   representing a successful, multi-result response; asserts correct
   mapping into the raw-job dict shape (Section 4).
2. **Empty response** — zero results for a valid query; must map to the
   same "genuinely empty, not blocked" outcome Naukri's
   `VALID_EMPTY_RESULT` state already models.
3. **Malformed/unexpected response shape** — a fixture with missing or
   restructured fields; must fail closed (raise, not silently return
   partial/wrong data) — mirrors Naukri's `PARSE_FAILURE` state.
4. **Pagination** — at least two fixtures representing page 1 and page
   2 (or the equivalent official mechanism, once known) of the same
   query, asserting no job is lost or duplicated across the boundary.
5. **Duplicate jobs** — the same job appearing twice within one
   response (or across two pages) — asserts the adapter itself does
   not need to dedupe (that remains `discover_local.deduplicate()`'s
   job, unchanged), only that it doesn't crash or produce malformed
   dicts.
6. **Missing fields** — a fixture with only the required fields present
   (`company`, `title` — the only two `normalize_job()` actually
   requires) and everything else absent; asserts graceful mapping to
   empty-string/`None` defaults, never a fabricated value.
7. **Authentication failure** — a fixture/mock representing an
   auth-rejected response; asserts the correct `AdapterBlockedError`
   (Section 6) is raised, not a silent empty result.
8. **Rate limiting** — a fixture/mock representing a 429-style response;
   asserts `BlockReason.RATE_LIMITED` is used and no retry is attempted.
9. **API unavailable** — a fixture/mock representing a connection
   failure or 5xx; asserts `AdapterTimeoutError`, matching Naukri's
   existing timeout contract.
10. **Schema changes** — at minimum, a test that asserts the adapter's
    parsing raises a clear, diagnosable error (not silent
    misattribution of fields) if the official API's response shape
    changes unexpectedly — the same "fail loud, don't guess" principle
    already embedded in this project's Naukri classifier work.

**Every fixture above is static test data — no live LinkedIn request is
part of testing**, exactly as this project's Naukri tests already work
(`data/fixtures/naukri/*.html`, consumed with zero network access).

---

## 8. Enablement Gate

LinkedIn may only be marked `AdapterStatus.ENABLED` after **all** of
the following are independently satisfied — mirroring, item for item,
the exact process Naukri already completed:

1. **Official authorization/API access** actually obtained (a real,
   granted credential/partnership — not merely applied for).
2. **Documented permitted use** — the specific API program's terms
   explicitly permit this project's intended use (automated job
   discovery for one individual candidate's own job search), reviewed
   and understood before any code is written against it.
3. **Credentials/configuration** securely stored (`.env`, never
   committed), with a documented, minimal-privilege scope.
4. **Offline fixture coverage** — all ten scenarios in Section 7
   implemented and passing, using only static fixtures, zero live
   calls.
5. **One controlled, authorized live validation** — a single query,
   using the now-legitimate credentials, following this project's
   existing single-query validation pattern (exact same discipline as
   `naukri_ua_single_query_live_validation.md`).
6. **Multi-query validation** — a small, controlled batch (mirroring
   `naukri_5_query_ua_freshness_live_validation.md`'s scope), confirming
   reliability beyond one request.
7. **Production safety checks** — DB SHA/row-count verification before
   and after every live step, exactly as already practiced for every
   Naukri live validation in this project.
8. **Regression tests** — the full existing suite plus the new
   LinkedIn-specific tests all green, with proof (like the Naukri work)
   that no other adapter's behavior changed.

**Only once all eight are independently true** would declaring
`status = AdapterStatus.ENABLED` and populating `capabilities` for real,
demonstrated features be appropriate — matching this project's own
standing rule: "Never mark an adapter `ENABLED` — and never declare a
capability — until it has a real, working implementation backed by
evidence."

---

## 9. Human-Handoff Alternative (Design Only)

A way for LinkedIn to participate in SarojJobOS **without** any
automated discovery at all, fully consistent with `CLAUDE.md`'s existing
guidance ("implement compliant discovery and human handoff rather than
attempting to bypass controls"):

1. **Manually supplied job URL** — the candidate finds a job on
   LinkedIn themselves (in their own logged-in browser session) and
   provides the URL through the existing, already-built manual intake
   path: `data/inbox/job_input.txt` / `scripts/add_job.py` (both
   already exist and already support a `SOURCE:` field — `"LINKEDIN"`
   would simply be one more valid value, no new mechanism required).
2. **Normalization** — the existing, unmodified
   `discover_local.normalize_job()` already handles a manually-entered
   job dict identically to an adapter-sourced one; no special-casing
   needed.
3. **Deduplication** — the existing `(source, job_id)` identity
   (`job_id_from_url()`, keyed on the pasted `job_url`) already
   prevents the same manually-added LinkedIn URL from being entered
   twice; existing, unmodified behavior.
4. **Scoring** — the existing, unmodified `score_job()` scores a
   manually-entered LinkedIn job exactly like any other — no
   source-specific scoring logic exists or would be needed.
5. **Application tracking** — the existing `jobs`/
   `candidate_job_matches` schema and status lifecycle
   (`config/application_schema.json`) already support tracking a job
   regardless of its source; a manually-added LinkedIn job flows
   through the identical, unmodified application-status pipeline.
6. **Source attribution** — `source = "LINKEDIN"` is preserved exactly
   as with any adapter-discovered job; the existing schema does not
   distinguish "found by an adapter" from "entered manually" at the
   data-model level, so no schema change is needed for attribution
   itself (though nothing currently records *which method* added a
   given job — a potential, small, future enhancement, not proposed as
   required here).

**This alternative requires zero new code and zero new schema** — every
piece of it already exists and already works for any source today.

---

## 10. Factual Implications (No Ranking)

**A. Official API / authorized integration**
- Technical: full automated discovery becomes possible, with real
  capability confirmation instead of the "Unknown" marks in Section 2.
- Compliance: resolves the Section 5 authorization gap entirely, for
  whatever scope the granted access actually covers.
- Cost/effort: requires obtaining LinkedIn partnership/API access
  (typically a business/partner process, not a self-service signup for
  an individual candidate's personal job search — this project has no
  evidence such access is practically obtainable at individual-candidate
  scale, and has not attempted to find out).
- Timeline: entirely dependent on a process outside this project's or
  Claude Code's control.

**B. Human-handoff-only LinkedIn**
- Technical: requires zero new adapter code; uses entirely existing,
  already-working project mechanisms (Section 9).
- Compliance: fully compliant — no automated access to LinkedIn of any
  kind occurs.
- Cost/effort: lowest of the three options; available immediately.
- Limitation: no automated discovery — the candidate must find LinkedIn
  jobs themselves; SarojJobOS only tracks/scores/manages what they find.

**C. Leaving LinkedIn `NOT_ENABLED` until authorization exists**
- Technical: no change from today's state — the registered skeleton
  remains exactly as built in Phase 4.
- Compliance: fully compliant by construction (nothing is attempted).
- Cost/effort: zero, ongoing.
- Limitation: LinkedIn contributes nothing to the candidate's job search
  (neither automated nor manual) until either A or B above is separately
  pursued.

**No ranking or recommendation among A/B/C is made in this document**,
per instruction — these are the factual technical/compliance
implications only, for you to decide among.

---

## Offline Verification Performed

- New diagnostic test added: `scripts/test_linkedin_phase5_offline_design.py`
  — confirms this design document's field-mapping claims (Section 4)
  are actually grounded in the real, current schema (not invented),
  and re-confirms `LinkedInAdapter` is unchanged (`NOT_ENABLED`, empty
  capabilities) and `search_profile._default_sources() == ['NAUKRI']`.
  Makes zero live network requests.
- Full standalone regression suite: run and confirmed green (see final
  response for the exact count).
- `python3 -m py_compile` across all `scripts/*.py`: confirmed clean.
- Production DB SHA-256 and row counts: confirmed byte-identical
  before/after this task (see final response for the exact values).
- No production file (`source_registry.py`, `search_profile.py`,
  `search_worker.py`, `linkedin_adapter.py`, scoring, eligibility,
  freshness, deduplication, or the database schema) was modified.

**Stopping here, per instruction. No implementation or enablement was
performed or proposed as ready to begin — Section 8's gate remains
entirely unsatisfied.**
