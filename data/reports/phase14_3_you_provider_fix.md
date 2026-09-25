# Phase 14.3 — Fix You.com Provider HTTP 403 / Current API Integration

**Date:** 2026-09-21
**Objective:** the You.com API key is correctly saved (confirmed by Phase 14.2's hot-refresh fix), but every request returned `HTTP 403`. This was never a key/config problem — `YouProvider` was calling the wrong, old API contract. Fix the actual integration against You.com's current documented Search API.

## 1. Old endpoint found

```
GET https://api.ydc-index.io/search?query=...&num_web_results=...
```

Query-string parameters, no JSON body, no `Content-Type` header. An old/undocumented shape.

## 2. New endpoint

```
https://ydc-index.io/v1/search
```

## 3. HTTP method

**POST** (was GET).

## 4. Auth header

`X-API-Key: <key>` — unchanged — plus `Content-Type: application/json`, which the old GET implementation never sent at all (it had no request body).

## 5. Env variable behavior

`YDC_API_KEY` (You.com's own canonical name, per their current docs) is now **preferred**; the project's original `YOU_API_KEY` is kept as an **automatic fallback** for backward compatibility. `search_provider.get_provider_api_key('you')` checks `YDC_API_KEY` first, then `YOU_API_KEY`. The existing Settings GUI's Save action still **writes** `YOU_API_KEY` — no migration of the save path, exactly as instructed. Neither variable's value is ever logged, printed, stored in the database, or included in any exception message (verified by dedicated tests).

## 6. 403 response before fix

`HTTP 403` with **no diagnostic detail at all** — the old code never read the response body on error, so the UI could only ever show the bare status code.

## 7. 403 response after fix

**N/A this run** — the corrected `POST /v1/search` request did **not** return a 403 at all; see #8. The response-body-capture mechanism for a 403 was still built (Part 6) and is verified against mocked 403 responses in the new test suite — it captures the provider's own message (e.g. a simulated `"Missing required scopes"`) into `ProviderSearchError.detail`, and the Settings page already surfaces `usage["last_error"]`, so a real future 403 would now show something like:

```
You.com: AUTH_FAILED — HTTP 403 -- Missing required scopes
```

instead of only `HTTP 403`.

## 8. Provider test result

| Field | Value |
|---|---|
| Provider | you |
| Query | `site:linkedin.com/jobs/view Site Reliability Engineer Bangalore` |
| Requested count | 5 |
| HTTP status | 200 |
| Result count | 5 |
| First URLs | `.../site-reliability-engineer-ii-db-at-sixt-research-development-india-3628987742`, `.../associate-site-reliability-engineer-at-blackline-3144459838`, `.../site-reliability-engineer-at-prosol-it-2803407748`, `.../site-reliability-engineer-f2f-interview-bangalore-at-zettamine-labs-pvt-ltd-4452341912`, `.../senior-site-reliability-engineer-at-nvidia-2996877120` |

Verified two ways: (1) a direct one-off diagnostic call to `YouProvider().search(...)`, and (2) the real **Settings → Test** action itself (`POST /api/settings/search-providers/you/test`) against the live dev server, which returned `{"status": "AVAILABLE", "detail": "Connection OK (1 result(s) returned)."}`.

This was **only** a provider-authentication/discovery test against You.com's own API. No LinkedIn page was fetched directly. No restricted-board network access occurred. The full seven-source live search was explicitly **not** run, per instruction.

## 9. Was Web Search scope missing?

**No.** The already-configured real You.com key was valid and had Web Search API scope all along. The 403 was caused entirely by calling the wrong (old, undocumented) endpoint with the wrong HTTP method — not by any permission/scope problem. No new key, no scope change, no user action was needed.

## 10. Failover status

**Unchanged.** `SearchProviderManager` was not touched — `YouProvider` only needed to correctly implement its own API contract, per instruction ("do not create a second failover implementation"). The existing priority order (You → Tavily → Exa → Brave → Serper), `AUTH_FAILED`/`QUOTA_EXHAUSTED`/`RATE_LIMITED`/`SERVER_ERROR` → failover-to-next-provider behavior, and "empty results are not a failure" rule all re-verified passing via the full, unmodified Phase 14.2 test suite.

## 11. Test count

**67 test files, all exit 0.** New: `scripts/test_phase14_3_you_provider.py` — **47 checks, 0 failed** — covering the correct endpoint/method/headers/request body, response parsing from `results.web` (including empty and malformed shapes, handled safely), every error code (401/403/429/402/5xx/timeout/network), the 403 missing-scope diagnostic capture, the `YDC_API_KEY`→`YOU_API_KEY` fallback and preference order, no-key handling, and — explicitly — no key leakage into any exception or log, all via mocked HTTP (zero real network calls, zero real credits consumed).

Two pre-existing tests needed a one-line fix — not a regression in this fix itself, but a side effect of the `.env`-loader addition below (the real `.env`'s now-genuinely-present key started leaking into tests that intentionally want a controlled/empty environment). Fixed by neutralizing the loader for one specific import via a monkeypatch on `env_config.read_env_file()` — the real `.env` file itself was never touched.

## 12. Production DB SHA

Before: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8`
After: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8`
**Byte-identical.**

## Explicit confirmations

- Scheduler changed: **NO**
- Auto-apply changed: **NO**
- Restricted-board pages fetched directly: **NO**
- Full seven-source live search run: **NO** (explicitly not performed, per instruction)

---

## What was fixed

### `YouProvider` (`scripts/search_provider.py`)

Rewritten from the old `GET https://api.ydc-index.io/search?query=...&num_web_results=...` to the current documented `POST https://ydc-index.io/v1/search` with a JSON body `{"query": ..., "count": ...}`. Response parsing now reads `results.web` (a list of `{url, title, description, ...}`), defensively handling a missing/null/non-list `results` or `results.web` by returning `[]` rather than crashing — and an empty-but-successful result list is returned as-is, never treated as a failure. No field is invented: the common shape (`title`/`url`/`snippet`/`date`) is populated only from fields the response actually provided.

### Freshness mapping (Part 4)

The Serper-shaped `qdr:d`/`qdr:w`/`qdr:m` tokens `search_provider_adapter.py`'s shared recency filter already produces are translated into You.com's own `day`/`week`/`month` freshness vocabulary inside `YouProvider` itself — no change to the shared recency-computation logic. An unrecognized/absent token omits the `freshness` parameter entirely rather than guessing, and You.com's `week` filter is never treated as proof of JobOS's `<=3`-day rule; real freshness is still always re-derived downstream by the existing, unmodified `freshness.classify_freshness()`.

### `YDC_API_KEY` / `YOU_API_KEY` (Part 2)

`search_provider.PROVIDER_ENV_KEY_ALIASES` lists `YDC_API_KEY` before `YOU_API_KEY` for the `"you"` provider; `get_provider_api_key()`/`get_active_env_key_name()` check both, preferred-first. `api/search_provider_settings_api.py`'s Settings listing now displays whichever variable is actually in use, so the masked-key label stays accurate even if a user sets `YDC_API_KEY` by hand outside the GUI.

### Error diagnostics (Parts 5/6)

`search_provider.py`'s shared `_http_request_with_retry()` helper (used by every provider, not just You.com) now reads and safely extracts a short diagnostic message from an HTTP error's own response body — parsed as JSON first (looking for `message`/`error`/`detail`/`reason`), falling back to raw text, truncated to 300 characters. This is the server's own response, never anything from our request, so it structurally cannot contain our API key; verified anyway by a dedicated no-leakage test. `401`/`403` → `AUTH_FAILED`, `429` → `RATE_LIMITED`, `402` → `QUOTA_EXHAUSTED`, `5xx` → `SERVER_ERROR`, timeout → `TIMEOUT`, network failure → `NETWORK_ERROR` — the existing `ProviderErrorType`/`ProviderSearchError` vocabulary, unchanged.

### A small, related fix found while verifying Part 8 (`.env` loader)

Editing `.py` files to deliver this fix triggers uvicorn `--reload`'s normal restart. Nothing in this project ever loaded `.env` into a fresh process's `os.environ`, so the real, already-saved You.com key appeared to "disappear" after any such restart — `.env` itself was never wrong, nothing was reading it back at startup. `api/main.py` now loads `.env` at import time via `os.environ.setdefault()` (standard dotenv semantics, never overrides a real shell-set variable), before any module that reads a `*_API_KEY` is imported. This was necessary to actually demonstrate Part 8's real provider test through the GUI's own Test action (not just a bypass script), and is a small, low-risk, clearly-scoped addition.

## Files

**New:** `scripts/test_phase14_3_you_provider.py`, this report.

**Modified:** `scripts/search_provider.py`, `api/search_provider_settings_api.py`, `api/main.py`, `scripts/test_phase14_2_capability_ui.py`/`test_phase14_2_hot_refresh.py` (test-isolation fix, not a behavior change).

**Not modified:** `scripts/search_provider_manager.py` (failover architecture, per explicit instruction), `scripts/search_provider_adapter.py`, `restricted_source_registry.py`, `scripts/score_job.py`, `config/profile.json`, the four direct adapters, the dedup/canonical/freshness/eligibility modules, `generate_run_report.py`.

---
STOP after this fix and provider test, per instructions. The full seven-source live search was not run.
