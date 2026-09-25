# Phase 6 Step 8 — Hirist JSON-LD Normalization + job_url Forensics (Offline Only)

**Zero network requests. Hirist remains `AdapterStatus.NOT_ENABLED`,
`capabilities = frozenset()`. No live fetch code was touched. No live
validation was performed. `hirist_parser.py` is unmodified — this is a
diagnosis-and-documentation task only, per instruction.**

## Evidence Base — Stated Explicitly Upfront

**The raw HTML and raw JSON-LD text from every live Hirist request
(Phase 6 Step 1, Step 4, and Step 7) was never persisted to any file in
this project.** This was confirmed by grepping every `data/reports/hirist_*`
file for JSON-LD markers (`"@type"`, `"@context"`, `itemListElement`,
`"Verint"`) before writing this report. Only two categories of evidence
exist:

1. **One real, truncated fragment** — the only genuine raw JSON-LD text
   ever captured, from Step 1 (`data/reports/hirist_phase6_inspection.md`,
   repeated in the Step 2 offline design doc):
   ```
   {"@type": "ListItem", "position": 1, "name": "Verint - Senior ..."}
   ```
   Truncated by the original probe's capture-length bound, mid-string,
   immediately after `"Senior "`. No key after `"name"` was ever
   observed for this or any other real entry — not `"url"`, not `"@id"`,
   not `"sameAs"`, not a nested `"item"` object. Its absence here is
   **not evidence of absence in the real page** — it is evidence of
   **truncation**, nothing more.

2. **Step 7's derived, aggregate statistics** (`data/reports/
   hirist_classifier_remediation_live_validation.md`/`.json`) — per-page
   raw/normalized/skipped counts, `numberOfItems`, `next_url` values, and
   the **final normalized output** for all 24 real jobs (company, title,
   `job_url: null`, `location: null`, `posted_date: null` for every one).
   These are *outputs of the current parser*, not raw captures — they
   tell us what the parser produced, not what the underlying JSON-LD
   actually contained beyond what the parser looked for.

No fuller or additional raw fragment exists anywhere in the project.
Everything below is grounded in these two sources, plus direct reading
of the current, unmodified `scripts/hirist_parser.py` and the 11
synthetic fixtures in `data/fixtures/hirist/` (all self-authored in
Phase 6 Step 3, none are real captures — each fixture file says so in
its own header comment).

**Legend:** **FACT** = directly observed (live data or code as written).
**INTERPRETATION** = a reasonable but unconfirmed explanation.
**NOT-DETERMINABLE** = cannot be answered from any evidence currently held.

---

## A–J: What the Real Hirist JSON-LD Supports, Field by Field

| # | Field | Status | Basis |
|---|---|---|---|
| A | Job title | **Populated for all 24 real jobs** (FACT — Step 7) via the flat `name.split(" - ")[1]` path. Reliability: **at least 1/26 confirmed wrong** (see Delimiter Deep-Dive). | Step 7 output + code read |
| B | Company name | **Populated for all 24 real jobs** (FACT) via the flat `name.split(" - ")[0]` path. Same reliability caveat as title — a wrong split corrupts both fields simultaneously. | Step 7 output + code read |
| C | Job URL | **Empty for all 24 real jobs** (FACT — Step 7: `job_url: null` × 24). Two distinct, both-plausible explanations exist; cannot currently distinguish them (see job_url Deep-Dive). | Step 7 output + code read |
| D | Location | **Empty for all 24 real jobs** (FACT — Step 7: `location: null` × 24). Code only ever sets `location` from a nested `item.jobLocation`; the flat path (the only path real data exercises) never sets it. | Step 7 output + code read |
| E | Experience | **Never observed in real data at all — not even as a null field explicitly reported.** Code-level: `experience_required` is **never assigned** by any path, nested or flat — only defaulted to `""` in `_empty_raw_job()`. Confirmed by `test_C` in the new characterization test (below): even a synthetic nested item with `experienceRequirements` set does not populate it. This is a pure code gap, independent of what real Hirist data may or may not contain. | Code read + new test C |
| F | Salary | **Never observed, never extractable by current code.** The module docstring (lines 50–52) lists `baseSalary` as part of the schema.org vocabulary "applied defensively" — but no code in `_parse_job_entry()` actually reads `baseSalary` or writes any salary-shaped field. This is a **docstring/code mismatch**: the docstring describes an aspirational vocabulary, not the vocabulary actually implemented. Confirmed by new test C. | Code read + new test C |
| G | Job description / summary | **Never observed in real data** (all 24 real jobs took the flat path, which never sets `jd_text`). Code supports it only via `nested_item.get("description")` — a path confirmed **unexercised** by real data (Step 7). | Step 7 + code read |
| H | Pagination | **Confirmed working on real data** (FACT — Step 7): `rel="next"` correctly detected and followed across all 3 real pages (`?page=2`, `?page=3`, `?page=4` link found on page 3 but not followed, since `_MAX_PAGES=3` was reached). This is the one area with no open question. | Step 7 |
| I | Stable job identifier | **None exists in current parser output**, for any of the 24 real jobs. No `url`, `@id`, or any other identifier is ever populated on the flat path. The adapter's own cross-page deduplication (Step 4/7) falls back to an in-memory `(company, title)` tuple — an **approximation**, not a true stable ID, and one that is only as reliable as the (confirmed imperfect) delimiter split itself. | Step 7 + code read |
| J | Application URL (separate from job URL) | **Never populated by any code path**, flat or nested — confirmed by new test B: `application_url` is only ever set to `""` in `_empty_raw_job()` and is never assigned anywhere else in `_parse_job_entry()`. This is a pure code gap; no live data (real or synthetic) has ever been observed to contain a schema.org field this code would map to an application URL (e.g. `applicationContact`) in the first place. | Code read + new test B |

---

## Delimiter Issue Deep-Dive ("flat Company - Title")

**1. Is the wrong split reproducible, or a one-off?**
**Reproducible — confirmed.** New characterization test D feeds the
parser the exact string implied by Step 7's finding
(`"Senior DevOps Engineer - AWS & Kubernetes"`) and confirms it
deterministically produces `company="Senior DevOps Engineer"`,
`title="AWS & Kubernetes"` — i.e., this is not a fluke of live timing
or a transient parsing bug; it is the delimiter logic working exactly
as designed, just on an input that doesn't follow the assumed
convention.

**2. Why does it happen?**
**INTERPRETATION** (cannot be confirmed without the raw string, which
was never persisted): the `_parse_job_entry()` flat-name branch assumes
every `name` field follows exactly the convention `"Company - Title"`.
Step 7's bad entry is consistent with a **different, equally plausible
real convention** — `"Title - Skills/Tags"` (e.g. a JD titled "Senior
DevOps Engineer" tagged with required skills "AWS & Kubernetes") — but
this is inference from the output shape, not a confirmed fact, because
the actual raw `name` string for that entry was never captured.

**3. Does a deterministic alternative to delimiter-splitting exist?**
**No reliable one, given current evidence.** Considered and rejected:
- *Use the nested `item` object instead*: **NOT-DETERMINABLE as a fix**
  — Step 7 confirms real data never populates the nested shape at all
  (0/24 jobs took that path), so this isn't an alternative, it's a
  no-op.
- *Detect the convention by position count or keyword heuristics*
  (e.g. "if the second segment looks like a skills list, swap it"):
  explicitly **rejected** — this is exactly the kind of heuristic
  guessing the task instructions forbid ("do not invent heuristics
  merely to make examples look better"). It would also require
  labeled examples of both conventions to validate against, which
  don't exist here (only one bad example is known).
- *Reject entries where the split looks wrong*: would require
  confidently distinguishing "wrong split" from "correct split with an
  unusual title," which is not possible from company/title text alone
  without fabricating a rule.
**Conclusion: no deterministic, evidence-backed alternative exists with
current evidence. This is a genuine, unresolved data-quality limitation
of the flat-name path — documented, not fixed.**

**4. Scope of the problem** — from Step 7's data: 1 confirmed-wrong
split out of 26 raw normalized entries (24 after dedup) = at minimum
**~4% of parsed entries** confirmed wrong; the true rate among **all**
`itemListElement` entries (including the 34 skipped as
zero-or-multiple-delimiter) is not determinable, since skipped entries
were never individually inspected or logged beyond the aggregate count.

---

## job_url Issue Deep-Dive

**1. Was the raw Step 7 response ever persisted?** **No — confirmed
above.** Only derived, aggregate output exists.

**2. What does the current code actually check?**
**FACT** (`scripts/hirist_parser.py:241–315`, `_parse_job_entry()`):
- The **nested-item branch** (lines 262–297) reads `nested_item.get("url")`
  — i.e., a `url` key **inside** a nested `"item"` sub-object.
- The **flat-name branch** (lines 299–315, the only branch real data
  exercises) **never reads any URL-shaped field at all** — not
  `list_item.get("url")`, not `list_item.get("@id")`, not
  `list_item.get("sameAs")`. It only computes `company`/`title` from
  the `name` string and returns immediately.

This is confirmed directly by new characterization test A: a
synthetic flat-name `ListItem` with `url`, `@id`, and `sameAs` **all**
set at the top level still produces `job_url == ""`.

**3. Two distinct, both-plausible explanations for the real empty
result — cannot currently distinguish them:**

| Hypothesis | Plausibility | Why it can't be confirmed or ruled out |
|---|---|---|
| **(i) Hirist's real search-results JSON-LD genuinely does not include a per-item URL at all** — the `ItemList`/`ListItem` entries may only ever carry `@type`, `position`, and `name` (a minimal list-summary shape), with the actual job URL only available on the **detail page** (never fetched — out of scope for this project so far) | Consistent with the one real fragment we have, which shows exactly these three keys before truncation | The fragment is truncated, so we cannot see whether more keys followed `"name"` in the same object |
| **(ii) A URL-shaped field does exist in the real data, but at a key/location the current code doesn't check** (e.g. a top-level `"url"` or `"@id"` on the `ListItem` itself, sibling to `"name"`, rather than nested inside an `"item"` object) | Consistent with schema.org's own `ItemList` conventions, where `ListItem.url` (sibling to `name`) is a legitimate, commonly-used alternative to nesting a full `item` object | The raw JSON-LD was never persisted past what the parser extracted, and the parser was never instrumented to log "keys seen but not consumed" during the Step 7 run |

**Conclusion: NOT-DETERMINABLE from current evidence which hypothesis
is correct — both remain live possibilities.** Distinguishing them
would require either (a) a fresh, explicitly-authorized live capture
with the *raw* JSON-LD text persisted to a file this time (not just
derived output), or (b) instrumenting the parser to log all top-level
keys seen on `ListItem` objects during a live run. **Neither was
performed in this task, per the "offline only, no further live query"
instruction.**

**4. What is needed to fix it, if hypothesis (ii) is correct?** A
one-line addition to the flat-name branch: attempt
`_sane_url_or_none(list_item.get("url") or list_item.get("@id"))`
before falling back to leaving `job_url` empty. **Not implemented in
this task** — implementing it now would be guessing at a fix for an
unconfirmed hypothesis, which the task instructions explicitly forbid
("do not fix the parser merely to make the test output prettier").

**5. What is needed to fix it, if hypothesis (i) is correct?** No
parser-level fix is possible — the search-results page simply doesn't
carry the data. The only path forward would be implementing a
**separate, new, explicitly-authorized detail-page fetch** per job (a
significant scope increase, itself requiring its own inspection,
robots.txt review, and rate-limit design — not something to imply is
"just a small fix").

---

## Schema.org Construct Review — What's Actually Confirmed vs. Assumed

| Construct | Confirmed present in real Hirist data? | Basis |
|---|---|---|
| `ItemList` (top-level) | **Yes — FACT** | Step 1 + Step 7 (`@type: "ItemList"` detected on all 3 real pages) |
| `itemListElement` (array) | **Yes — FACT** | Step 7 (`raw_item_list_element_count` = 20/page, `numberOfItems` = 20/page, consistent) |
| `ListItem` per entry, with `position` | **Yes — FACT** | Step 1's captured fragment shows `"@type": "ListItem", "position": 1`; Step 7's per-page counts are consistent with this shape |
| Flat `name` string on `ListItem` | **Yes — FACT, confirmed as the only path real data exercises** | Step 1 fragment; Step 7 (100% of 24 successfully-parsed jobs took this path) |
| Nested `item` object (`ListItem.item` → `Thing`/`JobPosting`) | **NOT confirmed present in real data — and Step 7 positively suggests it is absent for search-results entries** (0/24 real jobs took this path) | Step 7 (`job_url_finding.job_url_populated_for_any_of_24_jobs: false`, explicit conclusion already drawn in that report: "Hirist's real search-results JSON-LD does not appear to use the nested-object shape at all") |
| Top-level `url`/`@id`/`sameAs` on `ListItem` (sibling to `name`) | **NOT-DETERMINABLE — never checked for, never ruled out** | See job_url Deep-Dive above — this is the central open question of this report |
| `hiringOrganization`, `datePosted`, `description`, `employmentType`, `jobLocation`, `baseSalary`, `experienceRequirements` (all schema.org `JobPosting` fields) | **NOT confirmed present in real data at all** — these are only ever read from the unconfirmed, apparently-unused nested-`item` path | Step 7 (nested path never exercised); code read |

**Per the task's explicit instruction ("do not assume fields exist
merely because Schema.org supports them"): every "NOT confirmed"
row above is treated as unconfirmed, not assumed present, in this
report and was not used as a basis for any code change.**

---

## New Characterization Tests Added

Evidence-justified (not merely for prettier output) — each test pins a
specific code-level gap identified above to a concrete, runnable
example, so future changes to this module can see at a glance whether
a described limitation still holds:

`scripts/test_hirist_normalization_url_forensics.py` (new file, 4 tests):

| Test | What it proves |
|---|---|
| A | The flat-name path ignores top-level `url`/`@id`/`sameAs` fields entirely — confirms the code-level mechanism behind the job_url Deep-Dive's hypothesis (ii) |
| B | `application_url` is never populated by either extraction path — confirms category J |
| C | `baseSalary`/`experienceRequirements` are never read by any code path, despite `baseSalary` being named in the module's own docstring — confirms categories E and F, and the docstring/code mismatch |
| D | The `" - "` delimiter split reproduces Step 7's exact wrong-split result on a synthetic input — confirms the Delimiter Deep-Dive's reproducibility finding |

**All 4 tests pass against the current, unmodified `hirist_parser.py`.**
They are characterization tests — they document existing behavior
(including its gaps), not desired behavior. `hirist_parser.py` itself
was **not modified** in this task.

---

## Final Verification

```
New test (scripts/test_hirist_normalization_url_forensics.py, 4 checks): 4/4 PASS
Full standalone regression suite (44 files, including the new test):     PASS=44 FAIL=0
python3 -m py_compile across all scripts/*.py:                           PY_COMPILE_ALL_OK
This report's .json:                                                     valid (json.load)
```

| | Value |
|---|---|
| `HiristAdapter.status` | `AdapterStatus.NOT_ENABLED` (confirmed, unchanged) |
| `HiristAdapter.capabilities` | `frozenset()` (confirmed, unchanged) |
| `search_profile._default_sources()` | `['NAUKRI']` (confirmed, unchanged) |
| Live network requests made this task | **0** |
| Production DB SHA-256 before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production DB SHA-256 after | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production DB | Byte-identical; row counts unchanged (candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0); never opened for writing |

## Files Changed

| File | Change |
|---|---|
| `scripts/test_hirist_normalization_url_forensics.py` | New — 4 characterization tests, evidence-justified per above |
| `data/reports/hirist_normalization_url_forensics.md` / `.json` | New — this report |

**Not modified:** `scripts/hirist_parser.py`, `scripts/hirist_adapter.py`,
`scripts/hirist_fetcher.py`, `scripts/hirist_fetch_bridge.js`,
`scripts/naukri_parser.py`, `scripts/naukri_adapter.py`,
`scripts/naukri_fetcher.py`, `scripts/naukri_fetch_bridge.js`,
`scripts/source_registry.py`, `scripts/search_profile.py`,
`scripts/search_worker.py`, `scripts/score_job.py`,
`scripts/job_eligibility.py`, `scripts/experience_eligibility.py`,
`scripts/location_taxonomy.py`, `scripts/freshness.py`,
`scripts/discover_local.py`, `scripts/cross_source_dedup.py`,
`scripts/canonical_job.py`, `data/applications/jobos.db`, any LinkedIn
file, all 11 existing Hirist fixtures.

## Summary

Two real, unresolved data-quality limitations were confirmed, deep-dived,
and **left unfixed, as instructed**:

1. **The flat `"Company - Title"` delimiter split is not 100% reliable**
   (reproduced deterministically in a new test) — no deterministic
   alternative exists with current evidence; inventing one now would
   mean guessing at a convention from a single bad example, which the
   task instructions explicitly forbid.
2. **`job_url` (and `application_url`, salary, experience) cannot
   currently be extracted reliably** — two plausible, mutually
   exclusive explanations exist (the data isn't there at all, vs. it's
   there but at an unchecked key), and **distinguishing them requires
   a new live capture with raw payload persistence, which was not
   performed in this offline-only task.**

**Stopping here, per instruction. No further live Hirist query was
made. Hirist remains `AdapterStatus.NOT_ENABLED` and was not enabled
globally.**
