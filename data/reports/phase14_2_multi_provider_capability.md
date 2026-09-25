# Phase 14.2 — Fix Source Capability UI + Multi-Provider Search Pool

**Date:** 2026-09-21
**Objective:** the screenshot showed the seven restricted boards still displaying stale, pre-search-provider wording ("Automated discovery not authorized", "Not ready yet"). Fix the board-level capability model and wire the GUI to actually use it — these boards cannot be crawled directly, but they *can* participate in JobOS through the search-provider architecture, and the UI must say so.

## Root cause

`scripts/source_capabilities.py` already computed `final_status`/`search_provider_status`/`search_provider_active_provider` (built in the prior Phase 14.2 turn) — but `web/app.js`'s `renderSourceStatus()`/`shortReason()` were **never updated to read them**. They still rendered the raw, pre-Phase-14 `reason` investigation text through a hardcoded lookup table. The backend had the right data; the frontend was ignoring it. This turn fixes that wiring gap, and along the way tightens the capability model itself to match the exact vocabulary and priority logic requested.

## Provider configuration: none configured

| Provider | Configured? |
|---|---|
| You.com | NO |
| Tavily | NO |
| Exa | NO |
| Brave | NO |
| Serper | NO |

**Active providers:** none.

> **Search-provider architecture ready; no provider key configured.**

## Board-level capability (current, real state)

| Board | `direct_status` | `search_provider_status` | `search_provider` | `final_status` |
|---|---|---|---|---|
| Naukri | ENABLED | — | — | **ENABLED** |
| Hirist | ENABLED | — | — | **ENABLED** |
| IIMJobs | ENABLED | — | — | **ENABLED** |
| Apna | ENABLED | — | — | **ENABLED** |
| LinkedIn | NOT_AUTHORIZED | NOT_CONFIGURED | — | **SEARCH_PROVIDER_NOT_CONFIGURED** |
| Indeed | NOT_AUTHORIZED | NOT_CONFIGURED | — | **SEARCH_PROVIDER_NOT_CONFIGURED** *(+ a separate note: no company field ever available)* |
| Foundit | NOT_AUTHORIZED | NOT_CONFIGURED | — | **SEARCH_PROVIDER_NOT_CONFIGURED** |
| Instahyre | NOT_AUTHORIZED | NOT_CONFIGURED | — | **SEARCH_PROVIDER_NOT_CONFIGURED** |
| Cutshort | NOT_AUTHORIZED | NOT_CONFIGURED | — | **SEARCH_PROVIDER_NOT_CONFIGURED** |
| Wellfound | NOT_AUTHORIZED | NOT_CONFIGURED | — | **SEARCH_PROVIDER_NOT_CONFIGURED** |
| Shine | NOT_AUTHORIZED | NOT_CONFIGURED | — | **SEARCH_PROVIDER_NOT_CONFIGURED** |

Every restricted board also carries `manual_import_available: true` as a fallback regardless of provider configuration — that path was never affected by any of this.

**Restricted boards available through a search provider right now: none** (no key configured). **Verified, however**, via an isolated test server with a placeholder key, that all seven correctly flip to `AVAILABLE_VIA_SEARCH_PROVIDER` / `search_provider: "SERPER"` the instant a provider becomes eligible — see Verification below.

## Unavailable boards and exact reason

No board is reported as *impossible*. Every restricted board's `direct_status=NOT_AUTHORIZED` is backed by dated, specific Phase 12/13 evidence (robots.txt disallow, named-AI-crawler block, or an active anti-bot/Cloudflare response — unchanged, not re-investigated this turn) and every one still offers manual import.

## Failover behavior

Unchanged from the prior Phase 14.2 turn (`SearchProviderManager`, not touched this turn beyond confirming it still passes): priority-ordered pool, immediate success on any result (including empty), failover on `QUOTA_EXHAUSTED`/`AUTH_FAILED`/`RATE_LIMITED`/`SERVER_ERROR`, `SearchProviderPoolExhausted` (never fabricated) when the whole pool fails or none is configured.

## Test count

**65 test files, all exit 0.** New this turn: `scripts/test_phase14_2_capability_ui.py` — **99 checks, 0 failed** — covering board-level aggregation, the priority logic (unconfigured and configured, tested both via direct function calls and via a real FastAPI `TestClient`), no duplicate `*_SEARCH` rows in the GUI, `app.js` no longer contains the stale wording, checkbox-to-registry-key routing end-to-end, and the two new API path aliases (`/api/search-providers/...`).

## Production DB

SHA256 before/after: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` — **byte-identical.**

## Explicit confirmations

- Scheduler changed: **NO**
- Auto-apply changed: **NO**

---

## What was fixed

### 1. Board-level priority logic (`_compute_final_status()`, exactly Part 4)

```
IF direct source is enabled:                          final_status = ENABLED
ELIF a configured, eligible search provider exists:    final_status = AVAILABLE_VIA_SEARCH_PROVIDER
ELIF a search-provider path exists but no key:          final_status = SEARCH_PROVIDER_NOT_CONFIGURED
ELIF manual import is available:                        final_status = MANUAL_IMPORT_AVAILABLE
ELSE:                                                    final_status = NOT_AVAILABLE
```

This priority logic applies only to the **eleven job boards** (Part 2's explicit list) — the three ATS providers, `WEB_SEARCH`, `CAREER_PAGE`, and `TIMESJOBS` keep their own, pre-existing, unrelated vocabulary (`ATS_FALLBACK`/`NOT_CONFIGURED`), untouched.

### 2. A real inconsistency caught and fixed by this turn's own test suite

The initial implementation computed `search_provider_status` from the search-provider adapter's own `AdapterStatus` — which, like every adapter's status in this codebase, is fixed once at that module's first import and never re-evaluated. `search_provider_active_provider` (the field showing *which* provider would answer) was already correctly dynamic (`SearchProviderManager.eligible_providers()`, which re-checks the environment on every call). The two could disagree the moment a key was added mid-process — exactly the scenario the GUI's own "Save key" action creates. Fixed by deriving `search_provider_status`/`final_status` from the same live check as `search_provider_active_provider`, so all of a board's fields are always mutually consistent.

### 3. `web/app.js` — the actual bug from the screenshot

`renderSourceStatus()` was rewritten around a new `sourceStatusLine()` helper that branches on `final_status`:

- `AVAILABLE_VIA_SEARCH_PROVIDER` → `"✓ LINKEDIN — Search Provider: Serper"`
- `SEARCH_PROVIDER_NOT_CONFIGURED` → `"LINKEDIN — Search provider not configured / Configure a search API to enable discovery."`
- `ENABLED` → plain `"✓ NAUKRI"`

The old `shortReason()` function (the hardcoded `NOT_AUTHORIZED → "Automated access not authorized"` lookup table that produced the screenshot's stale text) was removed entirely, not left dangling. A new "Search Provider Status" line (`N providers configured · Automatic failover: ON/OFF`, linking to Settings) was added, fed from `GET /api/settings/search-providers`.

### 4. `web/search_new.html` — Part 12's "the backend decides"

The New Search checkbox list is now built from the board-level `sources.enabled` list rather than raw registry keys. A user sees `"LINKEDIN (via Search Provider: Serper)"` as the label but the checkbox's submitted **value** is the correct internal key (`LINKEDIN_SEARCH`) — computed automatically (`AVAILABLE_VIA_SEARCH_PROVIDER` → `{board}_SEARCH`, otherwise the board's own name). The user never manually picks "LinkedIn via Tavily"; they just see LinkedIn is available and check the box.

### 5. `GET /api/sources` — Part 16's exact shape

Confirmed live: a board's capability record now looks exactly like the spec —

```json
{
  "source": "LINKEDIN",
  "direct_status": "NOT_AUTHORIZED",
  "search_provider_status": "AVAILABLE",
  "search_provider": "SERPER",
  "final_status": "AVAILABLE_VIA_SEARCH_PROVIDER",
  "manual_import_available": true
}
```

`list_enabled_sources()`/`list_unavailable_sources()` (which feed the GUI's Available/Unavailable buckets) now key off `final_status` rather than the raw direct-adapter `enabled` flag — a board available via search provider moves into "Available" even though it has no direct adapter. `search_store.enabled_sources()` (the actual query-execution authorization gate) is untouched.

### 6. New API path aliases (Part 6, exact paths)

`GET /api/search-providers`, `POST /api/search-providers/{provider}/test`, `/enable`, `/disable` — thin aliases delegating to the exact same logic the existing `/api/settings/search-providers/...` routes already use (built in the prior Phase 14.2 turn). No duplicate implementation.

## Verification (real, not simulated)

- **Live API, unconfigured** (the actual shared dev server): every restricted board's `final_status` reads `SEARCH_PROVIDER_NOT_CONFIGURED`, `direct_status` reads `NOT_AUTHORIZED` — confirmed via `curl`.
- **Live API, configured**: a short-lived, isolated uvicorn instance with a placeholder `SERPER_API_KEY` confirmed LinkedIn's record exactly matches Part 16's own example.
- **Real browser (Playwright), unconfigured**: the Dashboard's Sources card genuinely renders `"CUTSHORT — Search provider not configured / Configure a search API to enable discovery. / Import a job manually"` — the exact replacement for the screenshot's stale text.
- **Real browser (Playwright), configured**: the same page, against the isolated server, renders `"✓ LINKEDIN — Search Provider: Serper"` for all seven restricted boards plus the new Search Provider Status line (`"1 provider configured · Automatic failover: ON"`).
- **Checkbox routing, real browser**: New Search shows `"LINKEDIN (via Search Provider: Serper)"` while the checkbox's actual value is `LINKEDIN_SEARCH`; a real `POST /api/candidates/{id}/searches` with `sources: ["LINKEDIN_SEARCH", "NAUKRI"]` was accepted (200). The test search row created during this manual check was archived immediately afterward — no lingering test data.

## Files

**New:** `scripts/test_phase14_2_capability_ui.py`, this report.

**Modified:** `scripts/source_capabilities.py`, `web/app.js`, `web/dashboard.html`, `web/search_new.html`, `api/main.py`.

**Not modified:** the multi-provider pool/failover implementation itself (`search_provider.py`, `search_provider_manager.py`, `search_provider_usage_store.py`, `search_provider_settings_store.py`, `env_config.py`), `restricted_source_registry.py`, `search_provider_adapter.py`, `scripts/score_job.py`, `config/profile.json`, the four direct adapters, the dedup/canonical/freshness/eligibility modules, `generate_run_report.py`.

---
STOP after this report, per instructions.
