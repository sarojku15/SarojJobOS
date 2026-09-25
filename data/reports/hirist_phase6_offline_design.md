# Phase 6 Step 2 — Hirist Offline Integration Design

**Design/specification only. No code implements this design. No live
Hirist request was made in this task.**

`Hirist` remains, unchanged: `AdapterStatus.NOT_ENABLED`,
`capabilities = frozenset()`. `search_profile._default_sources()`
remains `['NAUKRI']`. This document is grounded strictly in
`data/reports/hirist_phase6_inspection.md`/`.json` (Phase 6 Step 1's
single, live-observed data point) and the existing, unmodified adapter
architecture. It invents no field, endpoint, or capability beyond what
Phase 6 actually observed or what the existing schema already defines.

---

## 1. Adapter Boundary

**Class:** `HiristAdapter(JobSourceAdapter)` in the existing
`scripts/hirist_adapter.py` — extended in place, same file, same
registered `ADAPTERS["HIRIST"]` entry; no new file or class name
proposed.

**Methods** (the same abstract contract every adapter already
implements, `scripts/source_adapter.py`, unchanged):
- `health_check(self) -> AdapterHealth` — a single, cheap reachability
  check. **Open design question, not resolved here:** Phase 6 found no
  login wall, so a plain homepage/search-page fetch (mirroring Naukri's
  `health_check()` hitting the homepage) is plausible, but whether
  Hirist needs an *advisory* health check like Naukri's
  (`health_check_is_advisory = True`) or the default hard-gate is
  **undetermined** — Phase 6 only visited one search page, never the
  homepage, so no evidence exists either way.
- `search(self, query: SearchQuery) -> list[dict]` — one query in, one
  list of raw job dicts out, in the exact shape
  `discover_local.normalize_job()` already expects (Section 5). Must
  raise `AdapterBlockedError`/`AdapterTimeoutError` on failure — never
  a silently-empty list to mean "something went wrong" (same
  discipline as `NaukriAdapter`).

**Responsibilities in scope:**
- Constructing the search URL from a `SearchQuery` (Section 3).
- Fetching via an injectable fetcher object, mirroring
  `NaukriAdapter(fetcher=...)`'s constructor pattern (so tests use a
  fake fetcher, zero network).
- Parsing the response: **primarily by extracting and validating the
  page's embedded `schema.org` JSON-LD block** (Section 4) — this is
  the single most important design choice this document makes, since
  it is the one concrete, positive structural finding Phase 6 actually
  produced. A secondary, HTML-anchor-based fallback (mirroring the 20
  `/job-detail/`-pattern anchors Phase 6 counted) is noted as a
  possibility but **not designed in detail here**, since Phase 6 did
  not confirm a reliable card-level selector (its class-name heuristic
  matched zero elements).
- Classifying the response into the states in Section 9.
- Mapping parsed data into the flat raw-job dict shape (Section 5).
- Declaring `status`/`capabilities` truthfully, only once demonstrated
  (Section 11).

**What remains in shared pipeline code (out of adapter scope,
unchanged, identical to every existing adapter):**
- Scoring (`score_job.py`).
- Eligibility (`job_eligibility.py`, `experience_eligibility.py`,
  `location_taxonomy.py`).
- Deduplication (`discover_local.deduplicate()`,
  `cross_source_dedup.py`).
- Freshness classification (`freshness.py`) — see Section 7.
- Persistence (`tracker.py`, `search_worker.py`'s upsert functions).
- Application submission.
- Retry/escalation decisions on a block.

---

## 2. Capabilities

| Capability | Classification | Basis (strictly from Phase 6) |
|---|---|---|
| `SEARCH` | **Potentially available** | Phase 6 observed one successful, populated search-results page (HTTP 200, real content, 20 of 3,441 claimed jobs). Not marked "confirmed" because a single successful fetch does not establish general search reliability, and no second query/role/location combination was tested. |
| `DETAIL` | **Unknown** | No detail page was visited in Phase 6 (explicitly out of scope). 20 detail-link anchors were counted but never followed. |
| `NATIVE_FRESHNESS` | **Unknown / not confirmed** | No freshness/date parameter was attempted in Phase 6. Nothing in the observed URL, canonical tag, or JSON-LD excerpt indicated a freshness filter mechanism. See Section 7 — this must not be marked confirmed. |
| `PAGINATION` | **Confirmed by inspection** | Phase 6 directly observed `<link rel="next" href="...?page=2">` in the live page's own HTML — a real, directly-observed mechanism, not a guess. This is the *only* capability in this table earning "confirmed," because it is the only one with unambiguous, directly-captured evidence. |
| `APPLICATION_URL` | **Unknown** | Not visible in the captured excerpt; no detail page visited. `robots.txt` placed no specific restriction on application paths (unlike LinkedIn), but absence of a restriction is not confirmation of availability. |
| `COMPANY_METADATA` | **Unknown** | The captured JSON-LD excerpt showed a company *name* ("Verint") for one entry, but the excerpt was cut off before any structured company object (e.g. logo, industry, size) could be confirmed present or absent. Marked unknown, not confirmed, and not "not applicable" — genuinely undetermined. |
| `SALARY` | **Unknown** | Not visible in the captured excerpt. Not marked "not applicable" — schema.org's `JobPosting` type does define an optional `baseSalary` field in general, but whether Hirist's specific JSON-LD populates it was not observed. |
| `REMOTE_FILTER` | **Unknown** | Not tested in Phase 6 at all — no remote/work-model parameter was attempted. |
| `LOCATION_FILTER` | **Unknown** | **Explicitly not "confirmed," despite being tempting to mark so.** Phase 6's own inspection report flagged that the page's `<link rel="canonical">` tag *dropped* the `?locations=Bengaluru` query parameter used in the request — directly observed, ambiguous evidence that the parameter may not affect results, may be silently ignored, or may simply not be reflected in the canonical tag while still functioning. This design does not resolve that ambiguity and does not mark the capability confirmed on the strength of a parameter whose effect was never actually verified against a differing (e.g. unfiltered) baseline request. |

**Nothing above is marked "confirmed" merely because it is common on
job sites** (per instruction) — `PAGINATION` is the sole exception,
earned strictly because Phase 6 captured its literal, directly-observed
HTML evidence (`rel="next"`), not because pagination is a common
job-board feature in general.

---

## 3. Search Mapping

**Observed (Phase 6, one data point, FACT):**
```
Search:     https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru
Canonical:  https://www.hirist.tech/search/senior-devops-engineer-jobs   (query param dropped)
Pagination: https://www.hirist.tech/search/senior-devops-engineer-jobs?page=2
```

Existing `SearchQuery` fields (`scripts/source_adapter.py`, unchanged):
`role`, `location`, `experience_years`, `exclude_keywords`, `extra`,
`max_job_age_days`.

| `SearchQuery` field | Proposed Hirist mapping | Observed vs. proposed |
|---|---|---|
| `role` | The `/search/<role-slug>-jobs` path segment (e.g. `senior-devops-engineer` → `senior-devops-engineer-jobs`) — a role-to-slug transform analogous to `naukri_adapter.py`'s existing `_slugify()` | **Observed**: this exact URL shape returned real results for the one role tested. The slug transform *function* (spaces→hyphens, lowercase) is **proposed**, not separately re-verified for other roles. |
| `location` | The `?locations=<value>` query parameter | **Proposed only.** Phase 6 sent this parameter but never confirmed it changes results (Section 2, `LOCATION_FILTER`) — a future implementation would need a dedicated live comparison (with vs. without the parameter, or two different location values) to confirm this mapping actually filters, which Phase 6 did not do and this design does not authorize. |
| `experience_years` | **No mapping proposed.** No evidence of an experience-filter URL parameter was observed or sought in Phase 6. |
| `exclude_keywords` | **No Hirist-side mechanism observed or presumed** — would remain a client-side, post-fetch filter exactly as proposed for LinkedIn in the prior design (Phase 5 Step 2), for the same reason: no evidence of a negative-keyword search parameter. |
| `max_job_age_days` | **Not mapped** — `NATIVE_FRESHNESS` is unconfirmed (Section 7); this field would be silently unsupported for Hirist until/unless a native mechanism is confirmed, identical to the existing "None means unrestricted" fallback discipline already used elsewhere. |
| `extra` | Reserved escape hatch, same pattern as `naukri_adapter.py`'s `extra["naukri_search_url"]` — e.g. a future `extra["hirist_search_url"]` for a verified override when the slug-construction function guesses wrong for a specific role. |

**Pagination as a search input:** not a `SearchQuery` field today for
any adapter (Naukri is page-1-only). See Section 6 for how a future
Hirist adapter might consume the confirmed `?page=N` pattern internally
without necessarily exposing a new `SearchQuery` field.

---

## 4. JSON-LD Extraction Design

**Directly observed in Phase 6 (FACT, from the captured HTML
excerpt):**

```json
{
  "@context": "https://schema.org",
  "@type": "ItemList",
  "name": "Search for - Senior Devops Engineer Jobs",
  "numberOfItems": 20,
  "itemListElement": [
    {"@type": "ListItem", "position": 1, "name": "Verint - Senior ..."}
    /* excerpt cut off here -- see limitation below */
  ]
}
```

A separate `BreadcrumbList` JSON-LD block was also observed (Home →
role name) — not job-listing data, not further discussed here.

**Job-level fields actually observed:**
- `numberOfItems` (top-level `ItemList` field): **observed**, value 20.
- Per-item `position`: **observed** (position 1 shown).
- Per-item `name` (used as a combined company+title string in this one
  captured fragment, e.g. `"Verint - Senior ..."`): **observed, but
  only partially** — the fragment was truncated by Phase 6's 4,000-
  character capture bound before the title portion or any further
  per-item fields (URL, date, description, etc.) could be seen.

**Fields that were NOT captured and therefore remain explicitly
unknown** (per instruction — nothing below is assumed present just
because schema.org's general `JobPosting` type conventionally defines
it):
- A stable per-item identifier (schema.org's `ItemList` items don't
  inherently require one beyond `position`; whether Hirist's
  `ListItem` or an inner `item`/`JobPosting` object carries an `@id` or
  similar was not observed).
- A URL field per item (schema.org `ListItem` conventionally supports a
  `url` property; not confirmed present or absent here).
- A structured company field beyond the plain-text name embedded in
  `name` (e.g. no confirmation of a nested `hiringOrganization` object
  as schema.org's `JobPosting` type would define).
- `datePosted` / any freshness-relevant field.
- `description`.
- `employmentType`.
- `baseSalary`.
- `experienceRequirements` or any experience-related field.
- Location, as a structured field (only the search's own context
  implies "Bengaluru"; no confirmation of a per-item location field).

**Design implication:** a future parser must not assume the `name`
string alone can be reliably split into company and title (the one
observed example, `"Verint - Senior ..."`, suggests a `" - "` delimiter
pattern, but this is a single, truncated data point — **not confirmed
as a general, reliable convention**). The parser should be designed to
**fail closed** (raise a parse error) rather than guess a split point
when the delimiter pattern doesn't match cleanly, consistent with this
project's existing "fail loud, don't guess" discipline (e.g. Naukri's
`PARSE_FAILURE` state).

**Recommended first concrete follow-up** (not performed in this
design): a fresh, separately-authorized single live pass capturing the
**complete** JSON-LD payload (no 4,000-character bound), to actually
see all 20 items' full field sets before any parser is written against
assumed structure.

---

## 5. Normalized Job Mapping

Target shape: `discover_local.normalize_job()`'s existing, unmodified
contract — `source`, `company`, `title`, `location`, `work_model`,
`job_url`, `application_url`, `posted_date`, `jd_text`,
`experience_required`, `mandatory_skills`, `preferred_skills`,
`experience_min_months` (optional). Only `company` and `title` are
strictly required by `normalize_job()` itself.

| Target field | Proposed Hirist source | Status |
|---|---|---|
| `source` | Literal `"HIRIST"` | Certain — matches the registered `ADAPTERS` key |
| `company` | Parsed from the JSON-LD `name` string's presumed `" - "`-delimited prefix (Section 4) | **Derived, not directly confirmed** — the split convention itself is unverified |
| `title` | Parsed from the same `name` string's remainder | **Derived, not directly confirmed** |
| `location` | **Unknown / not observed at the per-item level** — would default to the query's own `location` input value as a fallback only if no per-item field is ever confirmed, explicitly logged as a fallback, not a real extraction, if implemented this way | `null`/empty until confirmed |
| `work_model` | **Unknown** — no signal observed; would default to `""` (→ `WorkModel.UNKNOWN`, the existing safe default) unless a future capture confirms a field | Unknown, defaults per existing pipeline behavior |
| `job_url` | **Unknown** — no per-item URL field was confirmed present in the captured JSON-LD fragment; the 20 HTML anchor elements Phase 6 counted are a plausible alternate source, but their exact `href` values were never extracted | Unknown, needs confirmation |
| `application_url` | **Unknown** — not observed | `null`/unknown |
| `posted_date` | **Unknown** — no `datePosted`-equivalent field was observed in the captured fragment | `null`/unknown until confirmed, then passed through as-is to the existing `freshness.classify_freshness()` (Section 7) |
| `jd_text` | **Unknown** — not observed | `null`/unknown; very plausibly only available on an (unvisited) detail page |
| `experience_required` | **Unknown** — not observed | `null`/unknown |
| `mandatory_skills` / `preferred_skills` | **Unknown** — not observed | Would remain empty lists (the existing `normalize_job()` default) unless a future capture confirms a skills-bearing field |
| `experience_min_months` | **Unknown** | Optional field, omitted unless a numeric source field is confirmed |

**Every field above not directly observed in Phase 6 is marked
`null`/`unknown`, to be derived later by existing pipeline logic
(where a generic fallback already exists, e.g. `work_model` defaulting
to `UNKNOWN`) or left genuinely absent — never fabricated**, per
instruction. Unmapped/unrecognized JSON-LD fields, if any are
eventually confirmed, would flow into `canonical_job.py`'s existing,
unmodified `source_metadata` catch-all, exactly as designed for every
other source.

---

## 6. Pagination

Designed around the one, directly-observed mechanism (Section 2):
`<link rel="next" href=".../search/<slug>?page=N">`.

- **First page:** the adapter's constructed search URL (Section 3),
  with no `page` parameter (implicitly page 1, matching the one URL
  Phase 6 actually tested).
- **Next-page detection:** parse the response HTML for a
  `<link rel="next" href="...">` tag, exactly as Phase 6 observed it
  once. If present, its `href` is the next page's URL. If absent, this
  is the last page (or a single-page result set).
- **Stopping condition:** stop when `rel="next"` is absent from a
  fetched page, **or** when a configured maximum-page safety limit is
  reached (see below), **or** when a page returns zero new job items
  (defensive: never loop indefinitely on a site that might repeat
  `rel="next"` unexpectedly).
- **Duplicate protection:** the adapter itself would track job
  identifiers already seen *within a single `search()` call* to avoid
  returning the same job twice across pages if Hirist's own pagination
  ever overlaps — this is a local, in-adapter safeguard, **separate
  from and in addition to** the existing, unmodified
  `discover_local.deduplicate()`, which already provides the
  authoritative, pipeline-level dedup guarantee regardless of what an
  individual adapter does internally.
- **Maximum page safety limit:** a small, explicit, hardcoded cap
  (e.g. on the order of a handful of pages) would be required before
  any implementation — this design does not propose a specific number,
  since no evidence exists yet about how many pages are reasonable or
  how large "reasonable" result sets typically are for this candidate's
  actual queries. This is an explicit, deferred implementation
  decision.
- **Interaction with existing `SearchQuery` limits:** today, no adapter
  paginates (Naukri is page-1-only, by explicit design). Introducing
  pagination for Hirist would be the **first** adapter-level
  pagination loop in this project — a genuine, non-trivial architecture
  addition requiring its own explicit review at implementation time,
  not merely "the same pattern as everything else." **Not designed in
  further detail here**, beyond flagging it as a first-of-its-kind
  change.

**None of the above is implemented in this task.**

---

## 7. Freshness

**Native freshness filtering was NOT observed in Phase 6.** No date/age
URL parameter was attempted; nothing in the captured evidence (URL
structure, canonical tag, JSON-LD fragment) indicated the existence of
a native freshness mechanism. `NATIVE_FRESHNESS` is correctly classified
in Section 2 as **Unknown / not confirmed** — this document does not
claim, imply, or design around Hirist supporting a native freshness
filter.

**What the existing pipeline already provides, unconditionally, for
any source (including Hirist, once/if enabled):** the existing,
unmodified `freshness.classify_freshness()` performs **post-retrieval**
classification of whatever `posted_date` text a job carries (parsing
absolute dates, "N days/weeks/months ago" phrasing, etc.) into
`HOT`/`FRESH`/`AGING`/`OLD`/`UNKNOWN` — entirely independent of whether
the source's own search supports a native date filter. **If** a future
implementation confirms Hirist provides a `posted_date`-equivalent
field (Section 4/5 — currently unknown), that value would flow through
this existing, unmodified classifier exactly like every other source's
does, with zero new code required for that part. This is the same
architectural distinction already documented for Naukri and LinkedIn:
discovery-time filtering (native, source-side) is completely separate
from post-retrieval classification (this project's own, source-
agnostic logic).

---

## 8. Robots / Policy Boundary

Recorded exactly as Phase 6 observed, without converting it into a
legal conclusion:

| Fact | Phase 6 finding |
|---|---|
| `robots.txt` accessible | Yes — HTTP 200, `https://www.hirist.tech/robots.txt` |
| Jobs/search paths disallowed for generic agents | **No** — the `User-agent: *` block disallows only administrative/internal paths (`/admin/`, `/config/`, `/cache/`, etc.); no `/search/` or job-listing path is disallowed |
| Sitemap information present | Yes — two `Sitemap:` URLs declared |
| `Crawl-delay` observed | Yes — `Crawl-delay: 10`, with an **ambiguous** binding (positioned after the `Sitemap:` lines and the `Yandex`-specific block, with no repeated `User-agent: *` line immediately before it) |
| Exact authorization established | **No.** Phase 6 explicitly did not read or verify Hirist's Terms of Service. `robots.txt`'s absence of a `Disallow` rule is, at most, an absence of one specific kind of prohibition — it is not an affirmative statement of permission for automated data extraction, and this document does not treat it as one |

**This design does not convert any of the above into "Hirist has
authorized automated access."** It records the facts and carries the
same open question Phase 6 raised forward unresolved: whether
Hirist's actual Terms of Service (not read in either Phase 6 or this
step) separately restrict automated querying.

---

## 9. Error/Status Model

Mapped onto this project's existing discovery-state vocabulary and the
existing `AdapterStatus`/exception contract — no new enum values
proposed:

| Condition | Mapped state | Behavior |
|---|---|---|
| Search page returns real, non-empty JSON-LD `ItemList` with items | `VALID_RESULTS` | Adapter returns the parsed raw-job list |
| Search page returns a valid page but `numberOfItems: 0` / empty `itemListElement` | `VALID_EMPTY_RESULT` | Adapter returns `[]` — a genuine empty result, not an error |
| Login wall / access-denied page / explicit block phrase | `BLOCKED` | Adapter raises `AdapterBlockedError` (existing contract) |
| Ambiguous page (no clear result data, no clear block signal — e.g. an unexpected redirect or an unfamiliar page shape) | `SOFT_BLOCK_OR_CHALLENGE` | Adapter raises `AdapterBlockedError` with `BlockReason.UNKNOWN_BLOCK` — treated as a block, **never** silently reinterpreted as an empty result (identical safety rule to Naukri's classifier) |
| Page loads but JSON-LD is missing, malformed, or doesn't match the expected `ItemList` shape | `PARSE_FAILURE` | Adapter raises `AdapterTimeoutError` (this project's existing convention for "unrecognized page structure," per `naukri_adapter.py`'s own precedent) — skips this one query, does not block the whole source |
| HTTP failure (non-2xx status, network error, timeout) | HTTP failure | `AdapterTimeoutError`, existing contract |
| Malformed/missing JSON-LD specifically | Subset of `PARSE_FAILURE` above | Same handling — explicitly called out since Section 4 flags this as the primary parsing risk for this source |
| Pagination failure (missing `rel="next"` when more pages were expected, or a malformed next-page URL) | Not a source-level failure | Treated as "no more pages" (Section 6's stopping condition) unless the *first* page itself fails to parse, in which case the whole query fails per the `PARSE_FAILURE` row above |

---

## 10. Offline Fixture/Test Plan (Proposed, Not Implemented)

Static fixtures only, zero live calls, mirroring this project's
existing testing principle:

1. **Valid JSON-LD, multiple jobs** — a complete, well-formed `ItemList`
   fixture with several items, each with a full, hypothetical field set
   consistent with Section 4's confirmed shape — asserts correct
   mapping into raw-job dicts.
2. **Empty `ItemList`** (`numberOfItems: 0`, empty
   `itemListElement`) — asserts `VALID_EMPTY_RESULT` handling, not an
   error.
3. **Malformed JSON** (syntactically broken JSON-LD script content) —
   asserts `PARSE_FAILURE`/`AdapterTimeoutError`, never a crash or a
   silently-wrong result.
4. **Missing fields** — an item with only `name`/`position` present (the
   minimal shape actually observed in Phase 6) — asserts graceful
   degradation to `null`/unknown for everything else, per Section 5.
5. **Duplicate jobs** — the same item appearing twice within one page's
   `itemListElement`, and/or across two page fixtures — asserts the
   adapter's own dedup safeguard (Section 6) behaves correctly without
   relying on it as the sole protection.
6. **Missing `rel="next"`** — a page fixture with no next-page link —
   asserts pagination correctly stops (Section 6).
7. **Pagination** — at least two linked page fixtures (page 1 → page 2
   via a fixture-embedded `rel="next"`), asserting no job lost or
   duplicated across the boundary.
8. **Malformed next URL** — a `rel="next"` tag present but with an
   unparseable or suspicious `href` (e.g. pointing off-domain) — asserts
   the adapter does not blindly follow it; a domain/shape sanity check
   before following any discovered "next" link is a reasonable,
   proposed safeguard, not yet designed in detail.
9. **HTTP 403/429/5xx** — mock/fixture responses for each, asserting
   403 maps toward `BLOCKED`, 429 maps toward
   `BlockReason.RATE_LIMITED`, and 5xx maps toward
   `AdapterTimeoutError` — mirroring Section 9's table.
10. **Robots/policy metadata** — a fixture-based test that the adapter
    (or a shared, source-agnostic helper) can parse a `robots.txt`-like
    fixture and correctly identify a `Crawl-delay` value, decoupled from
    live fetching — useful for the rate-limiting design question raised
    in Section 8, without needing to guess its exact runtime enforcement
    mechanism here.
11. **Schema changes** — a fixture representing a plausible future
    Hirist page where the JSON-LD shape has changed (e.g. `ItemList`
    replaced by a bare array, or `name` replaced by structured
    `title`/`hiringOrganization` fields) — asserts the parser fails
    loud and clear rather than silently misattributing data, per
    Section 4's "fail closed" principle.

**All eleven are static, offline, zero-network fixtures** — exactly
this project's existing convention (`data/fixtures/naukri/*.html`,
consumed with no network access).

---

## 11. Authorization/Enablement Gate

Hirist may only be marked `AdapterStatus.ENABLED` after **all** of the
following are independently satisfied — mirroring, item for item, the
process Naukri already completed and the process already documented
for LinkedIn (Phase 5 Step 2):

1. **Policy/Terms review** — Hirist's actual Terms of Service
   (unread in both Phase 6 and this step) reviewed and understood,
   resolving Section 8's open authorization question.
2. **Permitted automated access established** — a documented basis
   (explicit permission, a clear absence of prohibition in both
   `robots.txt` *and* Terms, or a direct authorization obtained) that
   this project's specific intended use (individual-candidate job
   discovery) is acceptable.
3. **Rate/crawl-delay handling** — an explicit, implemented minimum
   delay between requests, honoring `robots.txt`'s `Crawl-delay: 10`
   as a floor regardless of its exact binding ambiguity (Section 8).
4. **Offline parser fixtures** — all eleven Section 10 scenarios
   implemented and passing, zero live calls.
5. **Adapter tests** — the adapter's own unit tests (URL construction,
   `SearchQuery` mapping, error/status mapping) green.
6. **One controlled, authorized live validation** — a single query,
   following this project's existing single-query validation
   discipline (`naukri_ua_single_query_live_validation.md`'s pattern).
7. **Multi-query validation** — a small, controlled batch (mirroring
   `naukri_5_query_ua_freshness_live_validation.md`'s scope).
8. **Production safety verification** — DB SHA/row-count checks before
   and after every live step.
9. **Regression suite** — full existing suite plus new Hirist-specific
   tests green, with proof no other adapter's behavior changed.
10. **Source health behavior** — `health_check()`'s design question
    (Section 1 — advisory vs. hard-gate) explicitly resolved with
    evidence, not left as an open question at enablement time.

**Only once all ten are independently true** would declaring
`status = AdapterStatus.ENABLED` and populating `capabilities` for
real, demonstrated features be appropriate.

---

## 12. Human-Handoff Path (No Automated Discovery Required)

Identical mechanism already documented for LinkedIn (Phase 5 Step 2,
Section 9) — **zero new code, zero new schema:**

1. The candidate finds a Hirist job themselves (in their own browser)
   and supplies the URL via the existing manual-intake path
   (`data/inbox/job_input.txt` / `scripts/add_job.py`, both already
   support a free-text `SOURCE:` field — `"HIRIST"` is simply one more
   valid value).
2. **Normalization:** the existing, unmodified
   `discover_local.normalize_job()` handles a manually-entered Hirist
   job identically to any adapter-sourced one.
3. **Deduplication:** the existing `(source, job_id)` identity, keyed
   on the pasted `job_url` via `job_id_from_url()`, already prevents
   the same manually-added Hirist URL from being entered twice.
4. **Eligibility:** the existing, unmodified `job_eligibility.py` /
   `experience_eligibility.py` evaluate a manually-entered Hirist job
   exactly like any other.
5. **Scoring:** the existing, unmodified `score_job()` scores it
   identically — no source-specific scoring logic exists or is
   proposed.
6. **Application tracking:** the existing `jobs`/
   `candidate_job_matches` schema and status lifecycle already support
   tracking regardless of source.

This path is available **today**, with zero implementation work,
independent of whether or when the Section 11 gate is ever satisfied.

---

## Offline Verification Performed

- New diagnostic test added: `scripts/test_hirist_phase6_offline_design.py`
  — confirms this document's schema references (normalize_job() field
  names, WorkModel enum members, RawJob dataclass fields) are grounded
  in the real, current codebase, and re-confirms `HiristAdapter` is
  unchanged (`NOT_ENABLED`, empty capabilities) and
  `search_profile._default_sources() == ['NAUKRI']`. Makes zero live
  network requests.
- Full standalone regression suite: run and confirmed green (see final
  response for the exact count).
- `python3 -m py_compile` across all `scripts/*.py`: confirmed clean.
- Production DB SHA-256: confirmed unchanged (file-hash check only, no
  database access).
- No production file (`source_registry.py`, `search_profile.py`,
  `search_worker.py`, `hirist_adapter.py`, scoring, eligibility,
  freshness, deduplication, Naukri, LinkedIn) was modified.

**Stopping here, per instruction. No implementation or enablement was
performed or proposed as ready to begin — Section 11's gate remains
entirely unsatisfied.**
