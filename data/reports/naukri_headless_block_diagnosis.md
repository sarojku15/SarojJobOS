# Naukri Headless Block — Diagnosis

**Scope of this task:** pure investigation. No Naukri network request, no
browser launch, no code change was made while producing this report — every
finding below comes from source inspection, the installed Playwright browser
cache on disk, and artifacts already captured by prior live runs this
session.

## 1. Current Architecture

```
search_worker.run_once()
  -> process_queue_item()
    -> source_registry.discover_from_sources(queries)
        for each source:
          health = adapter.health_check()          <-- ONE call, before ANY query
          if not (health.reachable and health.block_reason == NONE):
              mark source BLOCKED, skip ALL queries for it, continue
          else:
              for each query: adapter.search(query)  <-- per-query, only reached if health passed
    -> naukri_adapter.NaukriAdapter
        health_check()  -> fetches https://www.naukri.com/  (homepage)
        search(query)   -> fetches https://www.naukri.com/{role}-jobs-in-{location}[?jobAge=N]
        both delegate the actual page fetch to naukri_fetcher.NaukriFetcher.fetch(url),
        which shells out to: node scripts/naukri_fetch_bridge.js <url>
```

Every fetch (health check or search or detail page) is an **independent Node
subprocess invocation** — a brand-new Playwright browser is launched, used
for exactly one `page.goto()`, and closed, for every single URL. There is no
persistent browser, no persistent context, and no session/cookie reuse
anywhere in this codebase (confirmed — see §6).

## 2. Exact Headless Launch Configuration

`scripts/naukri_fetch_bridge.js` (the only Playwright launch point in the
actual worker/adapter path):

```js
const headless = process.env.JOBOS_BROWSER_HEADLESS !== '0';
const browser = await chromium.launch({ headless });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(6000);
const html = await page.content();
```

With `JOBOS_BROWSER_HEADLESS` unset (the automated default), `headless` is
`true`.

## 3. Exact Health-Check Flow

`NaukriAdapter.health_check()`:

```python
def health_check(self) -> AdapterHealth:
    try:
        html = self._fetcher.fetch(_HOMEPAGE_URL)   # "https://www.naukri.com/"
    except AdapterTimeoutError:
        return AdapterHealth(source=self.name, reachable=False,
                              block_reason=BlockReason.TIMEOUT, ...)
    reason = detect_block_reason(html)
    return AdapterHealth(source=self.name, reachable=(reason == BlockReason.NONE),
                          block_reason=reason)
```

- **URL requested:** `https://www.naukri.com/` — the bare homepage, not any
  search or detail URL.
- **Same browser bridge as `search()`?** Yes — both call
  `self._fetcher.fetch(url)`, i.e. the identical
  `naukri_fetcher.NaukriFetcher.fetch()` → `node naukri_fetch_bridge.js <url>`
  path, with the identical headless configuration. There is no
  health-check-specific browser setup.
- **Same session/context?** No persistent session exists to share — every
  fetch (health check, search, detail) is its own fresh, isolated browser
  launch and context. "Same or different session" is not really a
  meaningful distinction here; there is no session at all.
- **Required before every search?** Yes, by construction:
  `source_registry.discover_from_sources()` calls `adapter.health_check()`
  exactly once per source, before attempting **any** query for that source
  (see §1's pseudocode, and the exact lines below).
- **What condition blocks the whole source?**
  `scripts/source_registry.py`, lines 141–145:
  ```python
  health = adapter.health_check()
  state = SourceRunState(source=source, health=health)
  if not (health.reachable and health.block_reason == BlockReason.NONE):
      state.blocked = True
      state.blocked_reason = health.block_reason
      # ... every query for this source is skipped entirely
  ```
- **Has health_check historically been reliable for Naukri?** Yes, in every
  prior live run this session — and in every one of those runs, the browser
  was launched **headed** (`headless: false`), before the `JOBOS_BROWSER_HEADLESS`
  default was introduced. This is the **first** run where `health_check()`
  failed, and it is also the **first** run where the browser launched
  headless. (See §6/§8 for the process-level evidence.)
- **Does a blocked health check necessarily mean the search URL is
  blocked?** **No — not necessarily, and the current code cannot tell.**
  `health_check()` only ever fetches the homepage; `search()`'s URL is
  different and is never attempted once the source is marked blocked. This
  codebase has zero evidence, one way or the other, about whether
  `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore?jobAge=3`
  would have succeeded in the same run.

## 4. Exact Search Flow

`NaukriAdapter.search(query)`:
1. Builds the URL via `_build_search_url()`: `https://www.naukri.com/{role-slug}-jobs-in-{location-slug}`, plus `?jobAge={N}` when `query.max_job_age_days` is set (added in the previous task).
2. `self._fetcher.fetch(url)` — same bridge, same headless config as health_check.
3. `classify_search_page(html)` on the result — `VALID_RESULTS` / `VALID_EMPTY_RESULT` / `BLOCKED` / `SOFT_BLOCK_OR_CHALLENGE` / `PARSE_FAILURE`.
4. Only reached if the source's health check passed.

## 5. Difference Between health_check() and search()

| | health_check() | search() |
|---|---|---|
| URL | `https://www.naukri.com/` (homepage) | `https://www.naukri.com/{role}-jobs-in-{location}[?jobAge=N]` |
| Fetch mechanism | identical (`NaukriFetcher.fetch` → same bridge, same headless config) | identical |
| Browser/session | fresh, isolated, non-persistent | fresh, isolated, non-persistent |
| Classification | `detect_block_reason()` only (NONE/UNKNOWN_BLOCK/TIMEOUT) | full `classify_search_page()` (5-state classifier) |
| Gating effect | **hard gate** — failure skips ALL queries for the source | per-query; a failure here only affects that one query (raises `AdapterBlockedError`/`AdapterTimeoutError`, caught per-query by `discover_from_sources()`) |

The only functional difference in the *fetch* itself is the URL. The gating
*behavior* around the result is what differs sharply.

## 6. Evidence from Previous Probes

Every probe script in this repository (`naukri_discovery_probe.js`,
`naukri_search_probe.js`, `naukri_job_structure_probe.js`,
`naukri_extractor.js`) — and the fetch bridge itself, before this session's
change — used the **identical, minimal** launch configuration:

```js
const browser = await chromium.launch({ headless: false });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
```

No probe ever set a custom user agent, locale, timezone, HTTP headers,
storage state, or cookies. **No probe has ever run headless.** None of them
demonstrate headless Naukri access, session reuse, or cookie/storage-state
reuse — because none of those mechanisms exist anywhere in this codebase.
Every successful Naukri access recorded in this project (every captured
fixture, every prior live-test result) was obtained **headed**.

`config/jobos.env.example` is empty; `.env` contains only `PROJECT_NAME` and
`TIMEZONE` — no session/cookie/auth configuration exists at the environment
level either.

## 7. Evidence from Fixtures/Logs

No dedicated Naukri request log exists (only `n8n/n8nEventLog.log`, unrelated
to this pipeline). The evidence available is:
- The captured fixtures (`homepage.html`, `search_results_sre_bengaluru.html`,
  `detail_valid.html`, `blocked_access_denied.html`) — all produced headed.
- This session's own prior live-test outputs (all headed, all successful
  through `health_check()` and `search()`).
- This task's own failed headless run's `WorkItemResult` (`blocked_sources:
  ['NAUKRI']`, `queries_completed: 0`) and 44 process-table observations
  confirming the browser that launched.

**Gap, noted honestly:** the actual blocked HTML from this run's failed
health check was not saved (only its length was logged by the live-test
driver script) — so the exact block phrase that matched cannot be quoted
verbatim. The only two possible outcomes given the code path that ran
(`detect_block_reason()` returned non-NONE after a successful fetch — no
exception was raised) are that the homepage response contained one of
`detect_block_reason()`'s five known phrases (most likely, since that is the
only mechanism `health_check()` currently has to report `reachable=False`
without an exception).

## 8. Why the Current Headless Run Was Blocked — Evidence-Based Diagnosis

**Leading, evidence-supported hypothesis: the default headless launch used a
fundamentally different browser binary than every previously-successful run,
not just "the same Chromium with a hidden window."**

Direct evidence from this machine's Playwright browser cache
(`~/Library/Caches/ms-playwright/`):

```
chromium-1243                    <- full Chromium build (used by every headED launch)
chromium_headless_shell-1243     <- SEPARATE, dedicated headless-only build
```

The process observed during this task's failed run was:

```
.../chromium_headless_shell-1243/chrome-headless-shell-mac-arm64/chrome-headless-shell
    ... --headless --hide-scrollbars --mute-audio ... --no-sandbox --no-startup-window
```

This is Playwright's documented behavior (since ~Chromium 112/"new headless
mode"): calling `chromium.launch({ headless: true })` without an explicit
`channel` does **not** run the same full Chromium binary with a hidden
window — it substitutes a **separate, stripped-down "headless shell" binary**
with a distinguishable runtime fingerprint (this is a long-publicly-known
signal anti-bot/WAF systems can detect). Every prior successful Naukri
access in this project — every probe, every earlier live test — used
`headless: false`, which can *only* run the full `chromium-1243` binary
(a headless-shell binary has no window to show).

This aligns exactly with:
- Option 1 (Naukri blocks headless Chromium) and Option 2 (the specific
  launch configuration triggers it) **being the same underlying cause,
  narrowed down**: it is very unlikely that "headless" per se is
  categorically blocked — it is the specific **headless-shell binary's
  fingerprint** that is the most concrete, evidence-supported difference
  between every success and this one failure.
- The already-documented historical note in `naukri_fetch_bridge.js` (an
  earlier exploratory attempt was blocked in an earlier headless mode and
  succeeded once switched to headed) — consistent with Naukri having
  fingerprint-sensitive protection that predates this session.

**This is a strong hypothesis, not a proven fact.** It has not been, and per
this task's explicit scope was not, verified with a live request. The
homepage-vs-search-URL question (Option 3) also remains completely
untested and is not ruled out.

## 9. Is the Health Check a Valid Hard Gate?

**As currently implemented, no — it conflates two different questions:**
"is Naukri reachable at all" and "is *this specific browser configuration*
going to work." Given headed mode has a 100% historical success rate and
headless mode has a 100% failure rate (n=1) on the exact same homepage URL,
the health check is currently doing exactly what it is designed to do
(fail closed rather than waste 5 queries against a source it just found
blocked) — but its result is now **confounded with browser configuration**,
not just genuine source availability. It was a valid, safe design when the
only variable was "is Naukri up," and remains valid in that sense; it has
simply now surfaced a second, previously-untested variable (headless vs.
headed) that this codebase never had reason to distinguish before.

## 10. Options A–G Evaluated

| Option | Preserves headless? | Requires visible browser? | Changes production behavior? | Blocking risk | Offline-testable? | Lets us verify jobAge=3 later? |
|---|---|---|---|---|---|---|
| **A. Keep as mandatory gate** | Yes | No | No (status quo) | Same as today (unverified for headless) | N/A | Only after a separate fix unblocks it |
| **B. health_check uses the same URL/session as search** | Yes | No | Yes — health_check would need to fetch a search-shaped URL instead of the homepage | Unknown — search URL might behave differently (untested), could reduce OR not change risk | Partially — the code change itself is offline; effectiveness requires a live test | Yes, if it stops blocking |
| **C. Make health_check advisory; let search() self-classify** | Yes | No | Yes — a real change to `source_registry.discover_from_sources()`, a shared function used by every source (not just Naukri) | Increases exposure: every query would be attempted even against a genuinely-down/blocked source (more live requests before we know it's futile) | Yes, the logic change | Yes — directly, since search() would run |
| **D. Reuse an existing session/cookie mechanism headlessly** | Yes | No | N/A — **no such mechanism exists anywhere in this codebase** to reuse | N/A | N/A | N/A |
| **E. Improve the headless launch configuration** (e.g. force the full Chromium binary via `channel: 'chromium'` instead of the default headless-shell) | **Yes** | No | Minimal — one launch-option change in `naukri_fetch_bridge.js` only | **Likely reduces risk** — restores the exact binary that has a 100% historical success rate, still with no visible window | Yes, the change itself; effectiveness needs one live check | Yes — this is the most direct path to actually testing jobAge=3 |
| **F. Persistent headless browser context** | Yes | No | Larger architectural change (a long-lived browser process instead of one-shot subprocess-per-fetch) | Unclear — persistence itself doesn't address the binary-fingerprint hypothesis in §8 | Partially | Yes, but only after also addressing §8 |
| **G. Other** | — | — | — | — | — | — |

**D is not viable** — this project's own Application Safety rules (CLAUDE.md)
prohibit creating accounts/logging in, and no session infrastructure exists
to reuse even if it were permitted. **C carries the most risk** (attempts
live requests against a source already suspected blocked, before knowing
whether the underlying cause is fixed) and touches a shared, multi-source
function — it should not be adopted "because it sounds plausible" without
first trying the narrower, better-evidenced fix. **F** is a real architecture
option for the future (real production use will likely want a longer-lived
browser anyway) but doesn't address the specific, evidenced cause found here
and is a bigger change than warranted right now.

## 11. Recommended Next Implementation Step

**Option E, specifically:** change `naukri_fetch_bridge.js`'s
`chromium.launch({ headless })` call to also pass `channel: 'chromium'`
(or the equivalent mechanism to force the full `chromium-1243` binary rather
than `chromium_headless_shell-1243`), while leaving `headless` computed
exactly as it is now (`JOBOS_BROWSER_HEADLESS`-driven, default `true`). This:
- Keeps automated execution 100% headless (no visible window) — the hard
  constraint is preserved.
- Reuses the exact binary/rendering engine that has a 100% success rate in
  this project so far, changing only "hidden window vs. visible window,"
  not the browser fingerprint itself.
- Is a single, small, reversible, well-scoped change with a clear,
  evidence-based rationale — not a guess.
- **Not implemented in this task** — this is a diagnosis-only deliverable,
  per your explicit instructions. Awaiting your approval before touching
  `naukri_fetch_bridge.js` again.

## 12. How to Safely Validate jobAge=3 Afterward

1. Apply the recommended §11 change (pending your approval).
2. Run a **health-check-only** offline-safe smoke check first if possible
   (or accept that the next live attempt's first request IS the health
   check, exactly as today — no new mechanism is needed for this).
3. Re-run the **same** 5-query, `max_job_age_days=3` live validation already
   built (`live_5query_3day_headless_validation.py`'s pattern) — no new
   query-planning or profile changes needed, since that part was already
   proven correct offline.
4. If the health check now succeeds, the existing per-query URL capture
   already built into that script will show the real requested URLs
   (confirming `?jobAge=3` is actually sent) and the freshness audit will
   show whether Naukri actually honors it (jobs_returned_older_than_3_days).
5. If the health check still fails even with the full-Chromium binary in
   headless mode, that would be strong evidence *against* the fingerprint
   hypothesis and *for* Option 1 (Naukri blocking headless categorically)
   or Option 3 (something else specific to the homepage) — to be
   investigated then, not assumed now.

## 13. Risks / Unknowns

- The §8 hypothesis is **not proven** — only one headless attempt has been
  made, and it was not instrumented to capture the actual blocked HTML for
  a definitive phrase-level confirmation.
- Whether the search URL specifically (as opposed to the homepage) is
  blocked under headless is completely untested.
- Whether `channel: 'chromium'` in headless mode is itself sufficient, or
  whether Naukri's protection also inspects other signals (TLS fingerprint,
  timing, IP reputation accumulated from this session's repeated automated
  access) is unknown and cannot be resolved without a live test.
- This session has made a meaningful number of live Naukri requests across
  recent tasks; if IP/session-level rate-limiting or reputation scoring is
  also a factor, even the recommended fix might not immediately succeed,
  and that would not disprove the fingerprint hypothesis on its own.
