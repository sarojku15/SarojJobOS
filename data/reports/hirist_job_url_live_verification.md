# Phase 6 Step 11 — Hirist job_url Live Verification (Exactly One Request)

**Exactly one live HTTP request was made in this task. Hirist remains
`AdapterStatus.NOT_ENABLED`, `capabilities = frozenset()`. The parser
was NOT modified during this task — this is a verification of the
already-shipped Step 10 remediation, run against fresh live data.
`source_registry.py` was NOT modified. `naukri_*` files were NOT
touched. Production DB never opened.**

## How the single-request guarantee was enforced

Identical mechanism to Phase 6 Step 9, reused exactly (per instruction
#11):

1. The search URL was built **offline, zero network access**, via the
   existing, unmodified `hirist_adapter._build_search_url()` —
   confirmed to produce the same URL as Step 9:
   `https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru`.
2. The **same diagnostic-only Node/Playwright script** built in Step 9
   (session scratchpad, not a project file — contains exactly one
   `page.goto()` call, no loop, no `rel="next"` handling) was invoked
   **once**, writing the captured HTML to a temporary scratchpad file
   (not a permanent project artifact — per this task's "temporary
   diagnostic artifact" instruction, unlike Step 9's permanent
   capture).
3. `HiristAdapter.search()` was never invoked. `hirist_fetch_bridge.js`
   was not touched.
4. All subsequent processing — classification, JSON-LD parsing,
   normalization, job-ID generation — was performed **entirely
   offline**, against the already-captured HTML, using the **current,
   unmodified production** `hirist_parser.py` (with the Step 10
   remediation already in place), `discover_local.py`, and `job_id.py`.
   **No code was changed during this task.**

## A. HTTP Evidence

| | Value |
|---|---|
| Total HTTP requests made | **1** |
| Requested URL | `https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru` (identical to Step 9's URL) |
| Final URL | Identical — **no redirect** |
| HTTP status | **200** |
| Effective `navigator.userAgent` | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/153.0.0.0 Safari/537.36` |
| Browser mode | Headless (`JOBOS_BROWSER_HEADLESS` unset → default) |
| Channel | `chromium` |
| Response length | 311,917 characters |
| Started | 2026-09-20T15:36:17.555Z |
| Ended | 2026-09-20T15:36:21.607Z (≈4.1s) |
| Retries | **0** |
| Detail-page requests | **0** |
| Second queries | **0** |
| Authentication / CAPTCHA interaction | **0** |

## B. JSON-LD Structure (this fresh capture)

| | Value |
|---|---|
| `HiristPageState` (unmodified classifier) | `VALID_RESULTS` |
| Matched block/challenge phrase | None |
| Total `ListItem` entries | **20** |
| Entries with a top-level `url` field | **20 / 20** |

**Confirms Step 9's finding is not a one-off**: on this independent,
fresh live capture, every single real entry again has a top-level
`url` field — the shape the Step 10 remediation targets.

## C. Normalization — job_url Fidelity

| | Value |
|---|---|
| Jobs successfully parsed by the current parser | **10 / 20** (same 10-of-20 pattern as Step 9's page 1 — the other 10 are skipped by the unchanged, fails-closed multi-delimiter rule, see Section E) |
| Jobs with `job_url` populated | **10 / 10** (100% of successfully-parsed jobs) |
| `job_url` exactly equal to the source `ListItem.url`, for every entry | **YES — confirmed for all 10, zero mismatches** |
| Any evidence of a constructed or guessed URL | **No — none found** |
| Malformed/off-domain URLs observed in this real data | **None** (all 20 URLs are well-formed `https://www.hirist.tech/j/...` links) — the malformed/off-domain rejection path was not exercised by real data this run, but was already separately verified with synthetic cases in Step 10's `test_hirist_job_url_remediation.py` (tests D1/D2) |

Sample (first 5 of 10 successfully-parsed jobs):

| Company | Title | job_url |
|---|---|---|
| Verint | Senior DevOps Engineer | `https://www.hirist.tech/j/verint-senior-dev-ops-engineer-1672606` |
| Senior DevOps Engineer | AWS & Kubernetes | `https://www.hirist.tech/j/senior-dev-ops-engineer-aws-and-kubernetes-1672338` |
| Vedantu Innovations | Senior DevOps & SecOps Engineer | `https://www.hirist.tech/j/vedantu-innovations-senior-dev-ops-and-sec-ops-engineer-1672535` |
| NTT DATA | Senior DevOps Engineer | `https://www.hirist.tech/j/ntt-data-senior-dev-ops-engineer-1670149` |
| SatSure | Senior Cloud/DevOps Engineer | `https://www.hirist.tech/j/sat-sure-senior-cloud-dev-ops-engineer-1670842` |

**This is a decisive, positive, real-world confirmation of the Step 10
remediation.** Every job_url extracted matches its source `ListItem.url`
character-for-character; none were constructed, guessed, or
transformed.

## D. Job ID Determinism

The existing, **unmodified** `discover_local.generate_job_id()` /
`job_id.job_id_from_url()` pipeline was exercised directly (offline)
against 5 of the 10 real, live jobs from this capture:

| Company | Title | job_url | job_id | Deterministic (2nd call identical) |
|---|---|---|---|---|
| Verint | Senior DevOps Engineer | `.../verint-senior-dev-ops-engineer-1672606` | `HIRIST-5649154751c6aff0` | Yes |
| Senior DevOps Engineer | AWS & Kubernetes | `.../senior-dev-ops-engineer-aws-and-kubernetes-1672338` | `HIRIST-a192c63836dd5b44` | Yes |
| Vedantu Innovations | Senior DevOps & SecOps Engineer | `.../vedantu-innovations-senior-dev-ops-and-sec-ops-engineer-1672535` | `HIRIST-69d7cdb927f9e0a2` | Yes |
| NTT DATA | Senior DevOps Engineer | `.../ntt-data-senior-dev-ops-engineer-1670149` | `HIRIST-8385171ec90662ae` | Yes |
| SatSure | Senior Cloud/DevOps Engineer | `.../sat-sure-senior-cloud-dev-ops-engineer-1670842` | `HIRIST-b0ceedcb0ced8064` | Yes |

**Notably, the Verint job's `job_id` (`HIRIST-5649154751c6aff0`) is
byte-identical to the value independently computed in Step 10's
report** for the same URL — confirming the hash is genuinely
deterministic across separate runs/captures, not merely
self-consistent within one process. **No code was added to produce
these IDs** — this is the existing, unmodified pipeline, working as
already designed, now correctly fed by a populated `job_url`.

## E. Company/Title — Not Modified, Recorded Only

Per explicit instruction, company/title parsing was **not touched** in
this task. As already documented in Steps 9 and 10, this fresh capture
reproduces the same known limitation: **3 of the 10 parsed jobs** have
a `company` value that is actually a title fragment (the "bare title,
no company" convention):

| Company (as parsed) | Title (as parsed) |
|---|---|
| Senior DevOps Engineer | AWS & Kubernetes |
| Senior DevOps Engineer | Cloud Infrastructure |
| Senior DevOps Engineer | AWS & Kubernetes |

This is the **same limitation**, reproduced on **independent, fresh
live data** — confirming it is a stable characteristic of Hirist's
real data, not an artifact of the specific Step 9 capture. **No fix
was attempted here, per instruction.**

## F. No Extra Network

After the single request in the "LIVE CAPTURE" step, every subsequent
operation — `classify_search_page()`, `_find_item_list_json_ld()`,
`_parse_job_entry()` (×20), `normalize_job()`, `generate_job_id()` (×6)
— was run **offline**, directly against the already-captured HTML file
on disk. No fetcher, browser, or HTTP client was invoked again at any
point after the one live request completed.

## Safety Verification

| Check | Result |
|---|---|
| Exactly one HTTP request made | **Confirmed** — 1 |
| Zero pagination requests | **Confirmed** — the capture mechanism has no loop/`rel="next"` handling; `next_url` was detected by the classifier afterward but never fetched |
| Zero detail-page requests | **Confirmed** — 0 |
| Zero retries | **Confirmed** — single invocation, exit code 0 |
| Zero second queries | **Confirmed** — one invocation, total |
| No authentication / CAPTCHA interaction | **Confirmed** |
| Headless Chromium only, no stealth/fingerprint modification | **Confirmed** — identical config to Step 9 |
| Production DB unchanged | **Confirmed** — SHA-256 byte-identical before/after (below); never opened |
| `HiristAdapter.status` remains `NOT_ENABLED` | **Confirmed** |
| `HiristAdapter.capabilities` remains empty | **Confirmed** |
| `search_profile._default_sources()` remains `['NAUKRI']` | **Confirmed** |
| `scripts/hirist_parser.py` modified during this task | **No** |
| `scripts/source_registry.py` modified | **No** |
| Any `naukri_*` file modified | **No** |
| Company/title parsing changed | **No** |

## Production DB Safety

| | SHA-256 |
|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |

**Byte-identical.** Row counts also confirmed unchanged
(candidates=1, candidate_search_profile=1, jobs=9,
candidate_job_matches=0, search_runs=0, search_queue=0). Never opened.

## Final Verification

```
Full standalone regression suite (45 files):     PASS=45 FAIL=0
python3 -m py_compile across all scripts/*.py:    PY_COMPILE_ALL_OK
This report's .json:                              valid (json.load)
```

`HiristAdapter.status` = `NOT_ENABLED`, `capabilities` = `frozenset()`,
`search_profile._default_sources()` = `['NAUKRI']` — all re-confirmed
unchanged after this live verification.

## Files Changed

| File | Change |
|---|---|
| `data/reports/hirist_job_url_live_verification.md` / `.json` | New — this report |

**Not modified:** `scripts/hirist_parser.py`, `scripts/hirist_adapter.py`,
`scripts/hirist_fetcher.py`, `scripts/hirist_fetch_bridge.js`,
`scripts/naukri_parser.py`, `scripts/naukri_adapter.py`,
`scripts/naukri_fetcher.py`, `scripts/naukri_fetch_bridge.js`,
`scripts/source_registry.py`, `scripts/discover_local.py`,
`scripts/job_id.py`, `data/applications/jobos.db`, any existing Hirist
fixture, any LinkedIn file. The raw HTML captured for this task was
written to a **temporary** scratchpad file (not a permanent project
artifact, per this task's own instruction), and is not part of the
committed project.

## Summary

1. **The Step 10 `job_url` remediation is confirmed working correctly
   against fresh, real, live Hirist data** — 10/10 successfully-parsed
   jobs had `job_url` populated, and every single one matched its
   source `ListItem.url` exactly, with zero constructed/guessed URLs.
2. **The existing `generate_job_id()` pipeline is confirmed producing
   deterministic, URL-derived IDs** for these real jobs, entirely via
   pre-existing, unmodified code — including one ID that is
   byte-identical to a value independently computed in a prior task,
   confirming true determinism across runs.
3. **The company/title limitation reproduces identically on this
   independent capture** (3/10 jobs affected) — confirmed as a stable
   characteristic of the real data, not addressed here per instruction.
4. **Zero extra network activity** beyond the one authorized request.

**Stopping here, per instruction. No further live Hirist query was
made. Hirist remains `AdapterStatus.NOT_ENABLED` and was not enabled
globally. The parser was not modified in this task.**
