# Naukri Headed Single-Query Diagnostic — Isolating Headless (A) from Volume/Timing (E)

**Result in one line: the headed request succeeded completely (HTTP 200,
`VALID_RESULTS`, 20 real jobs) using the exact same query, URL, Chromium
channel, and advisory architecture as the immediately preceding headless
attempt, which returned HTTP 403/`BLOCKED`/0 jobs. This is strong evidence
supporting Hypothesis A, and evidence weakening (not eliminating)
Hypothesis E, since this headed request occurred later in the session's
cumulative request timeline than the failed headless attempts.**

## Experiment Configuration

| Setting | Value |
|---|---|
| Query | role="Infrastructure Engineer", location="Bangalore", `max_job_age_days` omitted |
| Browser mode | **headed** (`JOBOS_BROWSER_HEADLESS=0`, a deliberate, one-time diagnostic exception) |
| Channel | `channel: 'chromium'` (unchanged) |
| Advisory health check | `NaukriAdapter.health_check_is_advisory = True` (unchanged) |
| DB | fresh isolated temp DB, production never opened for writing |
| Retries | none — exactly one search attempted |

## Pre-Flight Confirmations (all verified before execution)

| Check | Result |
|---|---|
| Automated default (`JOBOS_BROWSER_HEADLESS !== '0'` → headless) unchanged in code | **Confirmed** — `naukri_fetch_bridge.js` line 61, unmodified |
| This experiment explicitly sets `JOBOS_BROWSER_HEADLESS=0` | **Confirmed** — `os.environ["JOBOS_BROWSER_HEADLESS"] = "0"` in the driver script |
| `channel='chromium'` | **Confirmed**, unmodified |
| `max_job_age_days` absent from the query | **Confirmed** — `submission.query_plan[0]["max_job_age_days"] is None` |
| `NaukriAdapter.health_check_is_advisory` | **True**, unmodified |
| Production DB SHA before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` ✓ |
| Production row counts before | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 ✓ |

## A. Homepage Health Check

| Field | Value | Label |
|---|---|---|
| reachable | **True** | FACT |
| block_reason | **NONE** | FACT |
| HTTP status | **200** | FACT (new diagnostic) |
| detail | `""` | FACT |

## B. Search

| Field | Value | Label |
|---|---|---|
| search() attempted? | **Yes, exactly 1 time** (instrumentation-confirmed count) | FACT |
| Requested URL | `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore` | FACT |
| Final URL | identical — no redirect | FACT |
| HTTP status | **200** | FACT |
| Matched block phrase | none — page was not blocked | FACT |
| SearchPageState | **`VALID_RESULTS`** | FACT |
| block_reason | NONE | FACT |
| job_link_count | 20 | FACT |
| Worker status | **COMPLETED** | FACT |

## C. Browser Evidence

| Field | Value | Label |
|---|---|---|
| headless | **false** (deliberate, this experiment only) | FACT |
| channel | `chromium` (full binary) | FACT |
| Executable observed | `chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app` | FACT |
| Process observations | 3845 total (many, since this run made 22 live requests, each launching/closing its own browser instance) — **100% full Chromium, 0 `chromium_headless_shell`** | FACT |
| Observations with `--headless` flag | **0** — correctly confirms headED execution | FACT |
| Visible UI | Yes, confirmed by the complete absence of the `--headless` flag on every one of 3845 observations | FACT |

## D. Results

| Field | Value |
|---|---|
| Raw jobs | 20 |
| Normalized jobs | 20 |
| Unique jobs | 20 |
| Eligible / scored / matched | 5 / 5 / 5 |
| Freshness distribution | HOT=1, FRESH=4, AGING=2, OLD=13 |

(Full per-job freshness data is in the accompanying JSON. This confirms the
whole downstream pipeline — normalization, dedup, eligibility, scoring,
freshness, ranking — continues to work correctly end to end, though
validating that pipeline was not this experiment's primary objective.)

## E. Timing / Request-Volume Evidence

| Field | Value | Label |
|---|---|---|
| Requests made before `search()` (the health check) | 1 | FACT |
| Total requests this run | **22** (1 health + 1 search + 20 detail pages) | FACT |
| Existing delay values | unchanged — 3-second rate limit between detail-page fetches, 6-second settle wait per page (same as every prior run) | FACT |
| Cumulative session position | This run occurred **after** every prior live test this session, including both recent headless 403s — i.e. it added the **most** cumulative request volume of any run in this comparison set, and still succeeded | FACT |

## Comparison With the Immediately Preceding Headless Run

| | Headless (immediately preceding) | Headed (this experiment) |
|---|---|---|
| Query | Infrastructure Engineer / Bangalore, no jobAge | identical |
| URL | `https://www.naukri.com/infrastructure-engineer-jobs-in-bangalore` | identical |
| Channel | `chromium` | identical |
| Health check | Failed, `UNKNOWN_BLOCK`, HTTP 403 | **Succeeded**, HTTP 200 |
| Search | `BLOCKED`, HTTP 403, matched phrase `'access denied'`, 0 jobs | **`VALID_RESULTS`, HTTP 200, 20 jobs** |
| Worker status | BLOCKED | **COMPLETED** |
| Cumulative session request volume at time of run | lower (2 requests that run) | **higher** (22 requests that run, occurring later) |

## Interpretation

**The headed search succeeded.** Per the governing interpretation rule:

- **FACT:** headed execution succeeded while the immediately preceding
  headless execution, using the identical query/URL/channel, returned 403.
- This **provides evidence supporting Hypothesis A** (headless execution
  contributes to the block).
- This does **not prove headless mode is the sole cause**, because request
  timing/volume (Hypothesis E) has not been eliminated as a contributing
  factor in general — however, this specific run is itself evidence
  **against E being the dominant explanation**: it occurred *later* in the
  session's cumulative request timeline (i.e. with *more* prior volume
  behind it, not less) than the failed headless attempts, and still
  succeeded cleanly across 22 requests with no blocking at any point. If
  cumulative volume alone were driving the block, this run — occurring
  after even more volume — would have been at least as likely to fail, not
  markedly less likely.
- **Definitive causality is not claimed.** A single experiment does not
  rule out every alternative explanation (e.g., a time-of-day-based
  mechanism coincidentally aligned with this run, independent of both
  headless mode and raw request count) — but the balance of evidence has
  shifted meaningfully toward A relative to E as a result of this specific,
  controlled comparison.

## Hypothesis Update (A–E, plus F–H carried forward for completeness)

| # | Hypothesis | Prior verdict | Updated verdict | Reasoning |
|---|---|---|---|---|
| A | Headless mode causes/contributes to the block | SUPPORTED (confounded with E) | **SUPPORTED (strengthened)** | Identical query/URL/channel succeeded headed, failed headless, in a direct controlled comparison |
| B | Headless-shell binary mismatch | CONTRADICTED | CONTRADICTED (unchanged) | Not exercised by this test; already resolved |
| C | Headers/context differ materially | NOT DETERMINABLE | **NOT DETERMINABLE (unchanged)** | Full HTTP headers still not captured in this experiment either — only status code and final URL |
| D | Session/cookie state differs | CONTRADICTED | CONTRADICTED (unchanged) | No such mechanism exists in either mode |
| E | Request sequence/volume differs materially | SUPPORTED (confounded with A) | **CONTRADICTED (weakened significantly)** | This run added the *most* cumulative volume of any run compared (22 requests, latest in the session) and succeeded entirely — inconsistent with volume alone being the dominant driver, though a time-based (not count-based) mechanism is not separately ruled out |
| F | `jobAge` causes the block | CONTRADICTED | CONTRADICTED (unchanged) | Not re-tested; already resolved |
| G | Naukri blocks this automation path independent of `jobAge` | SUPPORTED | SUPPORTED (unchanged, refined) | Still true for **headless** specifically; this run shows the same path succeeds **headed** |
| H | Evidence insufficient to identify the cause | NOT DETERMINABLE | **NOT DETERMINABLE (narrowed further)** | The confound between A and E has been substantially resolved in favor of A; some residual uncertainty (e.g. a coincidental time-based factor) remains, but the evidence no longer supports treating A and E as equally likely |

## Does the Evidence Now Support Multi-Query Validation?

**Nuanced answer — not a plain yes.**

- **In headless mode (the automated default): no.** The evidence continues
  to associate headless execution with blocking; nothing in this experiment
  changes that for headless specifically — it was not re-tested here.
- **In headed mode: the evidence is now considerably stronger** that a
  multi-query run would succeed, since this single query succeeded cleanly
  across 22 real requests (1 search + 20 detail pages) with zero blocking
  at any point, immediately after a run of failed headless attempts. A
  multi-query headed test is a **plausible, evidence-supported next step**
  — but it would require accepting a visible browser window for multiple
  queries, which is a decision for you to make, not one taken here.
- **The underlying goal (reliable headless background operation) remains
  unresolved.** Proceeding to a multi-query test that only works headed
  does not itself solve the original problem this project is trying to
  reach (silent, headless automation).

## Recommended Next Step

This report does not execute anything further. Two reasonable paths exist,
for you to choose between:

1. **Accept headed mode as a temporary operating mode** and proceed to a
   controlled multi-query validation headed, to make forward progress on
   candidate matching while the headless-specific blocking issue remains
   open as a separate, lower-urgency investigation.
2. **Continue investigating headless specifically** (e.g., a longer
   cooldown period before the next headless attempt, or accepting that
   headless automation against Naukri may not be achievable without
   further, more invasive changes this project's stated safety rules
   would need to explicitly authorize) before running any further
   multi-query test.

No further live request was made as part of producing this report.

## Production DB Safety

| | SHA-256 | Row counts |
|---|---|---|
| Before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0 |
| After | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` | identical |

**Byte-identical.** No production DB mutation occurred. The automated
default configuration (headless) was **not** changed anywhere in the
codebase — the headed mode used here was an explicit, process-local
environment variable override for this one diagnostic script only.
