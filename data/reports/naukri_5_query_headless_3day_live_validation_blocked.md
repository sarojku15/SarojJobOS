# Naukri 5-Query Live Validation — BLOCKED (health check failed again)

## Outcome

**The health check failed again, this time WITH the `channel: 'chromium'` fix
applied. Per explicit instruction, the five planned queries were NOT
attempted, and no retry was performed.**

## 1. Browser Configuration

`scripts/naukri_fetch_bridge.js` (unmodified since the fix applied this task):

```js
const headless = process.env.JOBOS_BROWSER_HEADLESS !== '0';
const browser = await chromium.launch({ channel: 'chromium', headless });
```

`JOBOS_BROWSER_HEADLESS` was left unset for this run (confirmed:
`os.environ.get('JOBOS_BROWSER_HEADLESS')` → `None`), so `headless = true`,
`channel = 'chromium'`.

## 2. Browser Executable/Process Evidence

**Compromised for this specific live run — reported honestly rather than
glossed over.** The live-test driver script's own background `ps aux`
poller filtered for the substring `"chromium"`, but the driver script's own
filename (`live_5query_channel_chromium_validation.py`) **also contains
that substring**, and `ps aux` shows each process's full command line
(including the script path of the Python process running the poller
itself). Every one of the 130 "chromium" process observations this run
recorded turned out to be the poller matching its **own** Python process,
confirmed by: zero of the 130 observation lines contain
`ms-playwright`/`chrome-mac-arm64`/`Google Chrome for Testing` (the strings
a genuine browser process would show), and grepping the raw dump for the
driver script's own filename found a match. **This is a real instrumentation
bug in this run's driver script, not evidence about the browser itself.**

**What we do know, from separate, clean, offline evidence collected minutes
earlier in this same task** (`scripts/test_naukri_headless_config.py`, which
exercises the exact same `chromium.launch({ channel: 'chromium', headless })`
call this bridge uses): that call reliably launches
`chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app` — the full
Chromium binary, not `chromium_headless_shell` — with zero visible window.
There is no code-level reason this live health-check invocation would have
used a different binary; it calls the identical, unmodified bridge script.
But this run itself did not independently re-confirm that with clean
process evidence, and that gap is reported honestly rather than assumed away.

## 3. Health-Check Result

```
reachable: False
block_reason: BlockReason.UNKNOWN_BLOCK
detail: (empty)
```

- **URL requested:** `https://www.naukri.com/` (homepage) — exactly one live
  HTTP request was made in this entire run.
- **Fetch itself succeeded** (no exception, no timeout) — `detect_block_reason()`
  matched one of its five known access-denied phrases on the returned HTML.
  The exact phrase was not captured (this run's instrumentation logs
  `html_len` only, not the raw HTML, same limitation as the prior blocked run).
- **Source marked BLOCKED** by `source_registry.discover_from_sources()`
  before any query was attempted — exactly the same code path documented in
  `data/reports/naukri_headless_block_diagnosis.md`.

## 4. Five Queries — NOT Attempted

Recorded (for completeness) but **never executed**, per instruction:

| # | Source | Role | Location | max_job_age_days |
|---|---|---|---|---|
| 1 | NAUKRI | Infrastructure Engineer | Bangalore | 3 |
| 2 | NAUKRI | Infrastructure Engineer | Bengaluru | 3 |
| 3 | NAUKRI | Infrastructure Engineer | Chennai | 3 |
| 4 | NAUKRI | Infrastructure Engineer | Hyderabad | 3 |
| 5 | NAUKRI | Infrastructure Engineer | Pune | 3 |

`adapter.search()` was called **0** times (confirmed via instrumentation).
No search-page URL, with or without `?jobAge=3`, was ever requested.

## 5. jobAge=3 Evidence

**Still none.** This run adds no new evidence either way about whether
Naukri honors `?jobAge=3` — the parameter was never sent.

## 6. Search-Run / Worker Accounting

| Field | Value |
|---|---|
| status | FAILED |
| queries_total | 5 |
| queries_completed | 0 |
| queries_blocked | 1 (source-level, at health check) |
| jobs_discovered / eligible / scored | 0 / 0 / 0 |
| matches_created | 0 |
| errors_count | 0 |

`jobs` table: 0 rows. `candidate_job_matches`: 0 rows. No duplicates
(trivially, nothing to duplicate).

## 7. Conclusion

**The `channel: 'chromium'` fix did not resolve the block.** This is a
genuinely informative negative result: it weakens the specific
"headless-shell binary fingerprint" hypothesis from the prior diagnosis (at
least as the *sole* cause) — the exact binary that has succeeded in every
headed run, and that offline testing just confirmed launches correctly in
headless mode via this fix, was (very likely, modulo the instrumentation gap
in §2) still used here, and Naukri still returned an access-denied response
to the bare homepage request.

This does **not** mean the fix was wrong or should be reverted — it remains
a legitimate, evidence-based improvement (headless mode now uses the
historically-successful binary instead of a different one), and per the
task's own instruction, no further code change is being made based on this
one inconclusive result. What it does mean is that **at least one other
factor is still in play**, most plausibly one of:

1. **Naukri blocks based on the `--headless` runtime behavior itself**
   (rendering/timing/API differences a full Chromium binary still exhibits
   under `--headless`, distinct from which binary file is used) — not ruled
   out by this fix.
2. **Cumulative session/IP-level rate-limiting or reputation scoring** from
   this session's now-repeated live Naukri access across several recent
   tasks (a 20-job audit, an earlier successful 5-query run, a failed
   headless-shell run, and now this run) — plausible and cannot be
   distinguished from (1) without a cooldown period and/or access from a
   different network context, neither of which this task performs.
3. The homepage specifically being more heavily protected than a
   search-results URL (Option 3 from the diagnosis) — still completely
   untested.

## 8. Readiness for Next Step

**Not ready for another live attempt right now.** No retry was performed,
per instruction. Recommend a cooldown period before the next live attempt,
and, when next authorized, prioritizing a way to distinguish hypothesis (1)
from (2) from (3) — options include: waiting substantially longer between
attempts, checking whether a search-URL-first (bypassing the homepage
entirely) reaches a different outcome, or accepting that this specific
avenue (fully automated, unauthenticated, headless Naukri access) may need
Options B or C from the prior diagnosis (letting `search()` itself attempt
and self-classify, rather than gating on the homepage) evaluated more
seriously despite their higher risk profile — a decision for you to make,
not one taken here.
