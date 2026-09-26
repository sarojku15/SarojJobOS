# Job Sources

The authoritative list is always `GET /api/sources` (or the dashboard's
"Sources" panel) — this document explains what the statuses mean and
records the state as last verified by a real, live, 11-source search run.
A source is **never** silently skipped or misrepresented: every source
you select gets a real audit row after a run, with one of these exact
statuses (`scripts/search_worker.py`):

| Status | Meaning |
|---|---|
| `SUCCESS` | The source was queried and returned results (which may still be 0 after filtering — see `ZERO`). |
| `ZERO` | The source was queried successfully but returned no matching jobs. |
| `FAILED` | The source was queried but the request itself failed (timeout, error response, parse failure). |
| `BLOCKED` | The source actively blocked the request (CAPTCHA, anti-bot challenge, IP block). JobOS never attempts to bypass this. |
| `NOT_CONFIGURED` | The source needs credentials (a provider API key) that aren't set — never attempted. |
| `NOT_ATTEMPTED` | The adapter isn't `ENABLED` at all (a registered skeleton with no live implementation). |

## Current sources (`GET /api/sources`)

### Direct, always-on (no configuration needed)

| Source | Type | Required creds | Notes |
|---|---|---|---|
| `NAUKRI` | Direct adapter (headless Chromium via Playwright) | None | Native freshness filter, detail-page parsing |
| `HIRIST` | Direct adapter | None | Respects `robots.txt` `Crawl-delay: 10` |
| `IIMJOBS` | Direct adapter | None | |
| `APNA` | Direct adapter | None | Also fetches each job's own detail page (bounded, rate-limited) for real JD text/posted date/experience via the page's schema.org `JobPosting` JSON-LD block |
| `MOCK` | Dev/test fixture | None | Never used for a real search |

### Provider-backed (need one API key)

Turn on together the moment **any one** of `YOU_API_KEY` / `TAVILY_API_KEY`
/ `EXA_API_KEY` / `BRAVE_API_KEY` / `SERPER_API_KEY` is set in `.env` —
discovered through that provider's own search API, never a direct
crawler for these boards. `scripts/search_provider_manager.py` fails
over across providers only on a real failure (auth/quota/rate-limit/
timeout/network/server error), never merely because a search returned
zero results.

| Source | Why not direct-crawled |
|---|---|
| `LINKEDIN` | `robots.txt` disallows unrecognized/AI agents |
| `INDEED` | `robots.txt` explicitly names AI crawlers and disallows `/jobs` |
| `FOUNDIT` | `robots.txt` disallows AI crawlers from `/jobs/`,`/search/`; live fetch also returned HTTP 403 |
| `INSTAHYRE` | Live fetch returned an active Cloudflare challenge (HTTP 403) |
| `CUTSHORT` | Active Cloudflare challenge-platform requests observed on page load |
| `WELLFOUND` | `robots.txt` disallows `/search`; page embeds Cloudflare Turnstile + hCaptcha |
| `SHINE` | `robots.txt` disallows job-search/job-ID paths for generic agents |

### Registered, not yet enabled (no live behavior, zero network calls)

| Source | Reason |
|---|---|
| `ASHBY`, `GREENHOUSE`, `LEVER` | Working implementation exists against each platform's public API, but zero company boards are configured in `config/career_pages.json` and none has been through this project's phased live-validation process yet |
| `CAREER_PAGE` | Generic career-page provider not yet implemented/validated |
| `TIMESJOBS` | `robots.txt` is permissive, but the site's TLS certificate fails standard verification |
| `WEB_SEARCH` | Provider interface + quality validation exist (`.claude/skills/job-discovery-engine/`), but this backend process cannot invoke a web-search tool at runtime — needs a configured backend, not a redesign |

**Glassdoor is explicitly not part of this project's source list** — it
was documented by mistake in an earlier pass and removed; it is not
registered anywhere.

## Last live-validated result (all 11 enabled sources, one real run)

One clean search, real network calls, fresh isolated database, 900s
budget, completed in 643s:

| Source | Status | Raw | Eligible | Notes |
|---|---|---|---|---|
| APNA | SUCCESS | 75 | 68 | detail-page fetch: 19/20 succeeded |
| NAUKRI | SUCCESS | 20 | 15 | |
| HIRIST | SUCCESS | 10 | 7 | |
| IIMJOBS | SUCCESS | 4 | 3 | |
| LINKEDIN | SUCCESS | 10 | 6 | via provider |
| INSTAHYRE | SUCCESS | 10 | 10 | via provider |
| WELLFOUND | SUCCESS | 10 | 10 | via provider |
| SHINE | SUCCESS | 10 | 2 | via provider |
| CUTSHORT | SUCCESS | 1 | 0 | via provider |
| FOUNDIT | SUCCESS | 10 | 0 | via provider |
| INDEED | SUCCESS | 9 | 0 | via provider |

11/11 sources executed and truthfully audited; 0 failures, 0 blocks,
0 silent skips. This is a point-in-time result (live external sites
change constantly) — always trust your own run's own audit table over
this table.

## Adding a new authorized source

1. Implement a `source_adapter.JobSourceAdapter` subclass (`status =
   NOT_ENABLED` until validated).
2. Register it in `scripts/source_registry.ADAPTERS` and
   `scripts/source_capabilities.py`.
3. Write offline tests against fixtures (never live calls in a test).
4. Run this project's phased live-validation process: offline
   implementation → one live query → controlled multi-query → only
   then set `status = ENABLED`.

No GUI/API/scoring/report code needs to change — `/api/sources` and the
search form both read the live registry.
