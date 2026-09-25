# Phase 13 — Broad Job Discovery (LinkedIn, Indeed, Foundit, Instahyre, Cutshort, Wellfound, Shine)

**Date:** 2026-09-21
**Objective:** maximize usable job discovery from all seven sources using legitimate, technically reliable acquisition paths — not to force every direct scraper to work at any cost.

## Executive summary

None of the seven sources can be crawled directly (each was investigated in Phase 12 and found to have a real robots.txt disallow, a named-AI-crawler block, or active Cloudflare/Turnstile/hCaptcha infrastructure — that finding stands unchanged). This phase went beyond that direct-access investigation and built the three permitted fallback paths the task asked for: **search-provider discovery**, **employer/ATS fallback**, and **manual import** — then proved the first two are honest and functional (or honestly non-functional, for Indeed's company-name gap and Shine's zero search-provider results) and shipped the third as a working GUI feature.

**Result: six of seven sources move from "no discovery path at all" to LIMITED (real, working search-provider evidence + manual import), and the seventh (Shine) to MANUAL_IMPORT** (its search-provider path yielded nothing this session, but manual import still works).

## Final status per source

| Source | Direct search | Search provider | ATS/employer fallback | Manual import | Final status |
|---|---|---|---|---|---|
| LinkedIn | NOT AUTHORIZED | EVIDENCE CAPTURED (9/9 URLs normalizable) | Supported, no match in this sample | YES | **LIMITED** |
| Indeed | NOT AUTHORIZED | EVIDENCE CAPTURED, URL-only (9 URLs, 0 normalizable — no company name in Indeed's own search-result titles) | Supported, no match | YES | **LIMITED** |
| Foundit | NOT AUTHORIZED | EVIDENCE CAPTURED (5/6 normalizable) | Supported, no match | YES | **LIMITED** |
| Instahyre | NOT AUTHORIZED | EVIDENCE CAPTURED (9/9 normalizable) | Supported, no match | YES | **LIMITED** |
| Cutshort | NOT AUTHORIZED | EVIDENCE CAPTURED (5/5 normalizable) | Supported, no match | YES | **LIMITED** |
| Wellfound | NOT AUTHORIZED | EVIDENCE CAPTURED (3/3 normalizable) | Supported, no match | YES | **LIMITED** |
| Shine | NOT AUTHORIZED | ATTEMPTED, 0 results (2 query phrasings tried) | Supported, untested | YES | **MANUAL_IMPORT** |

(Naukri/Hirist/iimjobs/Apna are unchanged: **ENABLED**, direct adapters, live-validated in earlier phases.)

## What "search provider" actually means here — the critical honesty boundary

The **agent operating this repository** (this Claude Code session) has its own `WebSearch` tool. It used it — for real, on 2026-09-21 — to run the exact `site:<domain> "<role>" <location>` queries this project's own discovery strategy recommends, against all seven sources. Those calls returned real, dated, public job URLs (captured verbatim in `scripts/web_search_evidence_capture.py`).

**The deployed JobOS backend process cannot do this itself.** `web_search_discovery_adapter.WEB_SEARCH_BACKEND` is still `None` — no search-API credential is configured anywhere in this project. This was true before this phase and remains true after it. What changed is:

1. `WebSearchDiscoveryAdapter` now exposes the `search_site()` / `search_jobs()` / `search_recent_jobs()` interface Part 9 asked for — all of them raise the same `AdapterNotEnabledError` as `search()` until a real backend is configured. Nothing here pretends a backend exists.
2. The real evidence gathered this session was run through the **existing, unmodified pipeline** (`normalize_job` → `deduplicate` → `assess_job_eligibility` → `score_job`) to prove the acquisition path is not just "URLs exist" but "JobOS can actually consume them." 31 raw jobs → 31 normalized, 0 duplicates, 29 eligible under Saroj's profile. Scores landed in REJECT range — an honest consequence of search-provider snippets carrying no job-description text, not a scoring defect.
3. A genuinely useful, load-bearing finding specific to Indeed: its own search-result titles **never include a company name** (verified against all 9 real results). A URL and role/location alone are not enough to build a valid `CommonJob` (company is a required field) — so Indeed's search-provider path is real but insufficient on its own; manual import is required to supply the missing company name.

## Employer/ATS fallback (Part 12)

`scripts/employer_ats_resolver.py` — pure, offline, no-network URL-pattern detector for Greenhouse/Lever/Ashby application links, tested against real and synthetic URLs. None of the 31 real jobs captured this session happened to point at one of these three platforms (typical — most postings don't), so this path has no live example yet, but the detector is real and tested, and routes correctly into the **existing, unmodified** Greenhouse/Lever/Ashby adapters (still `NOT_ENABLED`, zero boards configured, unchanged from Phase 10 — this phase did not touch their validation status).

## Manual import (Part 13)

**Deliberate design choice: this is a human-typed/pasted form, not a server-side URL fetch.** Several of these sites' robots.txt language ("the use of robots or *other automated means*... is strictly prohibited") plausibly covers even a single one-shot server fetch triggered by a human click, not just bulk crawling. Rather than adjudicate that line, this follows the project's own existing precedent (`scripts/naukri_manual_capture.js`'s human-in-browser pattern): the candidate, already legitimately viewing the page, types/pastes what they see. JobOS then only normalizes, deduplicates, scores, and stores it — through the exact same pipeline a live adapter uses.

- New page: `/import` (`web/import_job.html`) — reachable from the Dashboard's Sources panel and from New Search's source list, wherever a LIMITED/MANUAL_IMPORT source is shown.
- New endpoint: `POST /api/candidates/{id}/jobs/import` — validates required fields (422 on failure), requires a **CONFIRMED** profile (409 on DRAFT, same gate a real search enforces — never weakened, never auto-confirmed), then calls `search_worker.import_manual_job()`, which reuses `normalize_job` → `assess_job_eligibility` → `score_job` → `_upsert_job_scored`/`upsert_candidate_job_match` verbatim.
- A manually-imported job's `source`/`job_source` is the **real platform name** (e.g. `LINKEDIN`), not a synthetic label — so `job_id.py`'s URL-derived ID means this same job, if later discovered by a real, live-validated LinkedIn adapter, resolves to the identical `job_id` and dedupes naturally. `discovery_source=MANUAL_IMPORT` separately preserves *how* it was found (Part 11's model).
- Never fabricates: missing fields (`jd_text`, `experience_required`, skills) are left blank, never guessed.

## CommonJob discovery_source / job_source (Part 11)

`discover_local.normalize_job()` gained two additive fields, both defaulting to `source` when absent — **zero behavior change for every existing caller** (verified in the test suite): `job_source` (where the job's data actually came from) and `discovery_source` (how it was found — `WEB_SEARCH`, `MANUAL_IMPORT`, or the source's own name for a direct adapter). Confirmed with real data: every one of the 31 captured-evidence jobs carries `discovery_source=WEB_SEARCH` with the correct per-source `job_source`.

## Source capability vocabulary (Parts 1 / 14)

`scripts/source_capabilities.py` now computes a `final_status` per source using exactly the requested vocabulary (`ENABLED / DIRECT_PUBLIC / SEARCH_PROVIDER / ATS_FALLBACK / MANUAL_IMPORT / LIMITED / NOT_AUTHORIZED / BLOCKED / NOT_CONFIGURED`), plus `search_provider_evidence` (the real, dated capture record or `None` — never fabricated) and `manual_import_available`. This flows automatically through the **existing** `/api/sources` endpoint (`search_store.source_capability_summary()` was not modified) into the Dashboard's Sources panel and New Search's source list, both updated to surface a "import a job manually" link for any LIMITED/MANUAL_IMPORT source.

**Deliberate scope decision:** the New Search page's source *checkboxes* were **not** extended to include LinkedIn/Indeed/etc., because `search_store.validate_sources()` only accepts `AdapterStatus.ENABLED` sources, and none of these seven are. Adding them as checkboxes that silently discover nothing on a real run would violate this project's "never pretend" rule. Instead, New Search now explicitly names which sources it will not include and links to manual import.

## Tests

New file `scripts/test_phase13_broad_discovery.py` — **59 checks, 0 failures** — covering:
- discovery_source/job_source defaults + explicit passthrough
- employer_ats_resolver detection (Greenhouse/Lever/Ashby + non-match)
- WebSearchDiscoveryAdapter's new methods: raise cleanly with no backend, delegate correctly with one configured (temporary fake backend, always cleared in `finally`)
- source_capabilities final_status vocabulary for every source, including the worked LinkedIn/Naukri/Shine/Greenhouse examples from the task itself
- the real captured evidence (31 jobs) replayed through the unmodified pipeline end-to-end
- manual_import validation/builder (rejects missing title, bad URL, unknown source; builds correctly)
- manual import end-to-end via the real API + a temp DB: DRAFT profile → 409, CONFIRMED → 200 with correct fields, missing field → 422, unknown candidate → 404, response shape contains no application-submission fields

Full existing suite: **all 62 test files exit 0** (61 pre-existing + this new one), confirming zero regressions.

## Production safety (Part 17)

- `data/applications/jobos.db` SHA256 before/after: `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` (**byte-identical**)
- Row counts before/after: `candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0` (unchanged)
- Zero production searches run; all validation used temp databases (`init_dev_db` into `tempfile.mkdtemp()`)
- Zero launchd/scheduler changes
- Zero auto-apply changes (repo-wide token scan clean; manual-import response contains only scoring/eligibility fields)
- The FastAPI backend process made **zero** network requests to any of the seven sources. The only network activity this phase was 8 `WebSearch` calls made by the agent session itself (listed with exact queries and result counts in the accompanying `.json`), which query a third-party search aggregator, not the seven restricted sites directly.
- No CAPTCHA/Turnstile/hCaptcha/login/rate-limit was bypassed. No fake account was created. No credential was shared or spoofed.
- Naukri/Hirist/iimjobs/Apna's `ENABLED` status and existing adapters are untouched.

## Files changed

**New:** `scripts/employer_ats_resolver.py`, `scripts/manual_import.py`, `scripts/web_search_evidence_capture.py`, `scripts/test_phase13_broad_discovery.py`, `web/import_job.html`, this report.

**Modified (all additive):** `scripts/discover_local.py` (discovery_source/job_source fields), `scripts/source_capabilities.py` (final_status vocabulary), `scripts/web_search_discovery_adapter.py` (search_site/search_jobs/search_recent_jobs interface), `scripts/search_worker.py` (`import_manual_job()`), `api/schemas.py` (`ManualJobImportIn`), `api/main.py` (`POST /api/candidates/{id}/jobs/import`, `GET /import`), `web/dashboard.html`, `web/search_new.html`, `web/app.js` (manual-import links surfaced next to unavailable sources).

**Not modified:** `scripts/score_job.py`, `scripts/tracker.py`, `config/profile.json`, `source_registry.ADAPTERS` (no new adapter registered/enabled), Greenhouse/Lever/Ashby adapter status, Naukri/Hirist/iimjobs/Apna.

---
STOP after this report, per instructions. No further phase started.
