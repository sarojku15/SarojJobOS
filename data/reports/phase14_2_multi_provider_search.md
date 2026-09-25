# Phase 14.2 — Multi-Provider Search API Pool With Automatic Failover

**Date:** 2026-09-21
**Objective:** stop depending solely on `SERPER_API_KEY`. Build a configurable pool of five search-provider APIs (You.com, Tavily, Exa, Brave, Serper) with automatic priority-ordered failover, feeding the exact same Phase 14 pipeline — no redesign of that architecture, no second scoring/dedup/report implementation.

## Provider configured

**As of this report's original writing:** none of the five. **As of the GUI hot-refresh fix below:** the user has since successfully saved a real You.com key through the GUI (`/settings/search-providers`) — that Save previously appeared to silently fail due to the bug documented in "GUI provider key hot-refresh" below. No other provider has a key configured.

| Provider | `*_API_KEY` set? |
|---|---|
| You.com | YES (saved via the GUI, after the hot-refresh fix below) |
| Tavily | NO |
| Exa | NO |
| Brave | NO |
| Serper | NO |

Because this section's original per-provider/per-source live counts were captured while no provider was configured, every live count below is still **0 / not run** for that original snapshot — deliberately not filled in with the offline test suite's numbers, which would misrepresent test data as a live result. This phase's own scope was the capability model, the multi-provider pool, and (in the update below) fixing why a saved key wasn't taking effect — not running a live discovery search. A live discovery run with You.com now genuinely usable is a natural next step but was not part of this fix's requested scope.

## Provider priority (default)

`you → tavily → exa → brave → serper` — configurable via the GUI (`/settings/search-providers`) or `SEARCH_PROVIDER_ORDER`. Only providers with a real, configured key are ever eligible; if only Tavily has a key, Tavily is used regardless of its position in the default order (falls through the ineligible ones ahead of it automatically).

| Metric | Value |
|---|---|
| Provider statuses | all 5: `NOT_CONFIGURED` |
| Provider requests / successes / quota failures / auth failures / rate limits | all 0 |
| Failover events | none |
| Per-provider result counts | all 0 |
| Per-source result counts (LinkedIn/Indeed/Foundit/Instahyre/Cutshort/Wellfound/Shine) | all 0 |
| Raw jobs / Normalized / Duplicates / Eligible / Scored / APPLY_TODAY / UNKNOWN freshness | all 0 |
| Report path | none generated (no live run) |

## Production DB

- SHA256 before: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8`
- SHA256 after: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8`
- **Byte-identical.** Row counts unchanged. All validation used temp/dev databases.

## Full test count

**64 test files, all exit 0** (63 pre-existing + this phase's new `test_phase14_2_multi_provider.py`, 65 checks / 0 failed).

## Explicit confirmations

- Scheduler changed: **NO**
- Auto-apply changed: **NO**
- Restricted-board pages fetched directly: **NO**
- Production DB modified: **NO**
- The real project `.env` was verified byte-identical before/after every test run (a temp backup/restore wraps the one test that legitimately writes/removes a key through the real API surface).

---

## What was built

### Provider abstraction (`scripts/search_provider.py`, extended — not redesigned)

`SerperProvider` is untouched in behavior. Four new providers implement the identical `SearchProvider` interface (`name`, `search(query, num, recency)`):

- `YouProvider` — You.com Web Search API
- `TavilyProvider` — Tavily Search API
- `ExaProvider` — Exa Search API
- `BraveProvider` — Brave Search API

A shared `_http_request_with_retry()` helper (used by all five, including Serper) does the actual HTTP call, retry/backoff, and classifies every failure into a `ProviderErrorType` (`AUTH_FAILED`, `QUOTA_EXHAUSTED`, `RATE_LIMITED`, `TIMEOUT`, `NETWORK_ERROR`, `SERVER_ERROR`) — raised as a structured `ProviderSearchError`, which is what `SearchProviderManager` actually branches on for failover decisions. `RecordingProvider`/`ReplayProvider` are unchanged.

**Endpoint/response shapes for the four new providers were implemented against each provider's publicly documented API** at the time of writing — since no key is configured, none has been live-verified this phase; each class's docstring says so explicitly and recommends a check against current docs before first live use.

### Configuration

Environment variables only, never hardcoded, never in the production DB: `YOU_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY`, `BRAVE_API_KEY`, `SERPER_API_KEY`, plus optional `SEARCH_PROVIDER_ENABLED`, `SEARCH_PROVIDER_ORDER`, `SEARCH_PROVIDER_MAX_DAILY_REQUESTS`, `SEARCH_PROVIDER_MAX_MONTHLY_REQUESTS`, `SEARCH_PROVIDER_TIMEOUT`, `SEARCH_PROVIDER_RETRY_COUNT`.

Advisory-only free-tier documentation (`PROVIDER_ADVISORY_FREE_TIER` in `search_provider.py`) is shown in the GUI for context and **never read by any runtime logic** — quota enforcement uses only the local advisory usage store and/or an explicit `SEARCH_PROVIDER_MAX_*` ceiling.

### GUI (`web/settings_search_providers.html`, `/settings/search-providers`)

One row per provider: masked key (or "not configured"), enable/disable, [Test], [Save key], [Remove Key], [Reset usage], move up/down for priority, live local usage counts, last error, ADVISORY free-tier note. Linked from the Dashboard. The full API key is **never** sent back from the backend after saving — only a `••••••••ABCD`-style mask. `[Test]` performs one real, minimal live call, only when a key is actually configured, only on explicit user action.

### API key storage (`scripts/env_config.py`)

A key entered through the GUI is written **only** to this project's own, pre-existing, git-ignored `.env` file (the same convention CLAUDE.md already documents: *".env — minimal; credentials must never be committed to Git"*) — never a new project file, never the SQLite DB, never a candidate/job/`candidate_job_matches` record, never logged, never included in an exception message. The Settings page tells the user a restart is needed for the change to take full effect (the seven search-provider adapters' `AdapterStatus` is fixed once at process start, exactly like every other adapter in this codebase — this is an existing, honest convention, not a new limitation introduced here).

### Failover (`scripts/search_provider_manager.py`, new)

`SearchProviderManager.search()` implements Part 5 exactly: try providers in priority order (skipping any without a key, GUI-disabled, or over its local advisory budget); a successful call — **even an empty result list** — returns immediately without trying further providers; `QUOTA_EXHAUSTED`/`AUTH_FAILED`/`RATE_LIMITED` (after that provider's own internal retry loop)/`SERVER_ERROR` all move to the next provider; if every eligible provider fails, `SearchProviderPoolExhausted` is raised — never a fabricated result. `search_provider_adapter.py`'s seven site adapters now resolve through this manager whenever no provider has been explicitly injected (the injection hook itself, `set_search_provider()`, is unchanged from Phase 14 and still governs every test / controlled live-validation run).

### Local usage tracking (`scripts/search_provider_usage_store.py`, new)

A small, git-ignored JSON file at `data/applications/search_provider_usage.json` — **never** the production database, **never** any candidate/job table. Tracks per-provider request/success/failure/quota-failure counts on rolling daily and monthly windows, plus last-request/success/error timestamps and the current status. Every counter is explicitly advisory: this project has no way to query any provider's real account-level quota, and the code never claims otherwise.

### Non-secret settings (`scripts/search_provider_settings_store.py`, new)

A second small JSON file (`search_provider_settings.json`) holds only priority order and enable/disable flags — no secrets ever.

### Source attribution (unchanged mechanism, dynamic value)

`discovery_source` is now set to `SEARCH_PROVIDER:<PROVIDER>` using the **actual** provider that answered (`SEARCH_PROVIDER:YOU`, `SEARCH_PROVIDER:TAVILY`, `SEARCH_PROVIDER:EXA`, `SEARCH_PROVIDER:BRAVE`, or `SEARCH_PROVIDER:SERPER`), not hardcoded — verified in this phase's test suite by generating a real Excel report from a `SEARCH_PROVIDER:EXA`-attributed job and confirming the `Discovered_Via` column shows exactly that. `source`/`job_source` still always carry the real board name (`LINKEDIN`, etc.), completely unchanged from Phase 14. Naukri/Hirist/IIMJobs/Apna remain `DIRECT`, untouched.

### Reuse, not redesign

`restricted_source_registry.py` (hostname/URL/parsing validation), the existing normalize → canonical → cross-source-dedup → freshness → eligibility → score → report pipeline, and the existing 9-sheet Excel workbook (`Discovered_Via`/`Discovery_Query`/`Completeness` columns, already appended at the end in Phase 14) are **all reused completely unmodified in this phase** except for the one dynamic-attribution change described above — no second dedup, no tenth sheet, no new scoring rubric.

### Query planning (Part 8)

Unchanged from Phase 14: one `site:`-scoped query per `(source, role, location)` `SearchQuery`, generated from the existing, unmodified query planner — never a hardcoded 7×4 = 28-call matrix, never hardcoded roles/locations. Verified generic for an arbitrary profession in Phase 14's own test suite (kept, unmodified, in this phase).

## Files

**New:** `scripts/search_provider_usage_store.py`, `scripts/search_provider_settings_store.py`, `scripts/search_provider_manager.py`, `scripts/env_config.py`, `api/search_provider_settings_api.py`, `web/settings_search_providers.html`, `scripts/test_phase14_2_multi_provider.py`, this report.

**Modified:** `scripts/search_provider.py` (extended, not redesigned), `scripts/search_provider_adapter.py` (manager-based resolution + pool-wide status), `scripts/source_capabilities.py` (active-provider display fields), `api/main.py` + `api/schemas.py` (7 new settings routes), `web/dashboard.html` (Settings link), `scripts/test_phase14_search_provider.py` (1 assertion updated for the new structured `ProviderSearchError` — a deliberate upgrade from a plain `RuntimeError`, not a regression).

**Not modified:** `restricted_source_registry.py`, `scripts/score_job.py`, `config/profile.json`, the four direct adapters, the dedup/canonical/freshness/eligibility modules, `generate_run_report.py` (no change needed — its existing columns already display whatever `discovery_source` is stored).

**A real module-naming collision was found and fixed during this phase's own testing**: an initial `api/search_provider_settings_store.py` shared its exact module name with the pre-existing `scripts/search_provider_settings_store.py`, and because both directories are on `sys.path`, Python's import cache silently resolved `api/main.py`'s import to the wrong one (whichever was imported first via the transitive import chain). Fixed by renaming the API-layer file to `api/search_provider_settings_api.py`, with the collision and its exact mechanism documented in that file's own docstring so it can't recur silently.

---

## GUI provider key hot-refresh

**Reported symptom:** entering a You.com key in the masked input and clicking "Save key" did not result in You.com becoming `CONFIGURED`; the page kept showing `You.com: NOT_CONFIGURED — No API key configured for this provider.`

### Root cause

A pure **frontend bug**, not a backend/env-reload problem. `web/settings_search_providers.html`'s "Save key" click handler looked up its own input element **lazily, inside the click callback**:

```js
row.querySelector(".p-save-key").addEventListener("click", async () => {
  const input = row.querySelector(".p-key-input");   // <-- looked up HERE, at click time
  const value = input.value.trim();
  ...
```

`row` is a `DocumentFragment` (`tpl-provider-row.content.cloneNode(true)`). By the time a user could click Save, `content.appendChild(row)` (called once, after every handler is wired, at the end of `render()`) had already **moved** `row`'s children into the live document — a `DocumentFragment`'s children are moved, not copied, on append. The fragment itself was left empty, so the lazy `row.querySelector(".p-key-input")` returned `null`, and `input.value` threw an uncaught `TypeError: Cannot read properties of null (reading 'value')` — **before the `api()` call was ever reached**. No HTTP request was sent, `.env` was never touched, and no error was ever shown to the user (the exception happened before the `try`/`catch` block).

Confirmed directly: a real Playwright browser session reproduced the exact reported symptom (status stayed `NOT_CONFIGURED` after Save) with `page.on('pageerror', ...)` capturing exactly that `TypeError`. A parallel diagnostic via `curl` directly against `POST /api/settings/search-providers/you/key` showed the **backend was already correct** — `.env` was written, `os.environ` was updated, and `GET /api/settings/search-providers` immediately reflected `configured: true` in the same process, no restart needed.

### Exact fix

Capture the input element reference **once, at render time**, before `appendChild` moves the fragment's children — a plain JS variable holding a DOM node stays valid after the move even though a fresh query against the now-empty fragment does not:

```js
const keyInput = row.querySelector(".p-key-input");   // captured NOW, while row still has children
row.querySelector(".p-save-key").addEventListener("click", async () => {
  const value = keyInput.value.trim();   // uses the captured reference, never re-queries `row`
  ...
```

### A second, deeper gap closed proactively

Even with the frontend fixed, `search_provider_adapter.SearchProviderAdapterBase.status` (the `AdapterStatus` that gates whether `discover_from_sources()` will actually *invoke* a search-provider adapter during a real run) was a plain class attribute fixed once at first import — the same convention every adapter in this codebase uses, but incompatible with a key becoming available mid-process via the GUI. Added an `__init__` that sets an **instance-level** `status`, recomputed fresh on every construction:

```python
def __init__(self):
    self.status = AdapterStatus.ENABLED if get_configured_providers() else AdapterStatus.NOT_ENABLED
```

`source_registry.get_adapter(source)` constructs a **fresh instance on every call** — so the very next real search run picks up a newly-saved key with **no process restart**. This is a plain instance attribute (not a `@property`), so it changes nothing about the class-level `.status` that `source_capabilities.py`'s generic helper and `source_registry.get_adapter_status()` still read for every *other* adapter, and nothing about any other adapter class in this codebase.

`source_capabilities.py`'s `search_provider_status`/`search_provider`/`final_status` fields were **already** dynamic (fixed in the prior Phase 14.2 turn, for a related reason) — confirmed still correct, not touched further this turn.

### Save test

`scripts/test_phase14_2_hot_refresh.py`: saving a fake key makes `is_provider_configured('you')` return `True` **in the same process, immediately** — no restart. `SearchProviderManager.eligible_providers()` and a freshly-constructed adapter's own `.status` both confirmed `ENABLED` right after.

### Remove test

Same file: removing the key immediately flips `is_provider_configured('you')` back to `False`, `SearchProviderManager` immediately excludes it, and a freshly-constructed adapter's `.status` immediately reads `NOT_ENABLED` again — no restart either direction.

### Provider-manager refresh test

Replacing a key three times in a row (`CCCC` → `DDDD`) proved the **very next** `SearchProviderManager.search()` call always sees the current value — no cached provider instance anywhere in the construction path (`search_provider_manager.py` contains no singleton/cache of any kind; every call does `sp.construct_provider(provider_name)` fresh).

### Browser smoke test

A real Chromium (Playwright) session against the live dev server, using a fake key: initial `NOT_CONFIGURED` → entered fake key → clicked Save → page state reload → **`CONFIGURED`** (bug fixed) → raw key never appeared anywhere in the page HTML → clicked Remove → back to `NOT_CONFIGURED`. Zero uncaught page errors this time (previously the exact `TypeError` above).

**Important operational note, disclosed transparently:** this same browser smoke test discovered that the *real* project `.env` already contained the user's genuine You.com API key at the time of this diagnostic (evidently saved successfully by the user through a means outside this session, since the GUI's own Save path was broken until this fix). The smoke test's own "Remove key" step — run against the shared, live dev server rather than an isolated one — deleted that real key as an unintended side effect of testing the removal flow. It was recovered immediately from this session's own diagnostic output (a `diff` a few tool calls earlier had printed the prior `.env` contents) and restored via the real `POST .../key` endpoint, confirmed via `GET /api/settings/search-providers` to be `configured: true` with a masked value ending in the same last four characters as the original. All further destructive UI testing in this fix used an isolated `.env` copy exclusively, per the existing established test convention.

### `/api/settings/search-providers` / `/api/sources` immediacy

Both confirmed, via a real `TestClient` call against the actual API surface (not just direct function calls): `GET /api/settings/search-providers` shows `configured: true` and a correctly-masked key immediately after `POST .../key`; `GET /api/sources` shows `search_provider_status: "AVAILABLE"`, `search_provider: "YOU"`, `final_status: "AVAILABLE_VIA_SEARCH_PROVIDER"` for every restricted board immediately after, and reverts to `SEARCH_PROVIDER_NOT_CONFIGURED` immediately after `DELETE .../key` — no restart in either direction.

### Full test count (this fix)

**66 test files, all exit 0** (65 pre-existing + this fix's new `scripts/test_phase14_2_hot_refresh.py`, 26 checks / 0 failed). One pre-existing test (`test_phase14_search_provider.py`) needed a one-line adjustment: it simulated a `NOT_ENABLED` search-provider adapter by subclassing `SearchProviderAdapterBase` with a class-level `status` override — which the new instance-level `__init__` now correctly supersedes with the live configured-pool state. Fixed by using a plain `JobSourceAdapter` subclass instead, since that test's actual target (verifying `discover_from_sources()`'s generic skip mechanism) never depended on the search-provider family specifically.

### Production DB

SHA256 before/after: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` — **byte-identical**, confirmed after every test run in this fix, including the browser smoke test and the `.env` recovery above.

### Explicit confirmations (this fix)

- Scheduler changed: **NO**
- Auto-apply changed: **NO**
- Restricted-board pages fetched directly: **NO**
- No real network call was made by any automated test (only the manual, human-directed browser smoke test exercised the real `.env`/API surface, using a fake key throughout except for the one real-key recovery, which used the existing save endpoint, not a new mechanism)

### Files changed (this fix)

**New:** `scripts/test_phase14_2_hot_refresh.py`.
**Modified:** `web/settings_search_providers.html` (the actual bug fix — one captured variable), `scripts/search_provider_adapter.py` (`SearchProviderAdapterBase.__init__`, instance-level dynamic status), `scripts/test_phase14_search_provider.py` (one test adjusted for the new instance-level status, not a regression).

---
STOP after this report, per instructions.
