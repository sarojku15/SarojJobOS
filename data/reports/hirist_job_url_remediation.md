# Phase 6 Step 10 — Hirist Deterministic job_url Remediation (Offline Only)

**Zero network requests. Hirist remains `AdapterStatus.NOT_ENABLED`,
`capabilities = frozenset()`. `source_registry.py`, `naukri_*` files,
and the production DB were not touched. Company/title parsing was not
changed.**

## Original Parser Gap

`hirist_parser._parse_job_entry()` has always had two extraction paths:
a **nested** `item` object path (schema.org's standards-compliant
`ItemList → JobPosting` shape), and a **flat** `name`-string path (the
only shape Phase 6's early, truncated evidence had confirmed). Only the
**nested** path ever read a `url` field
(`nested_item.get("url")`) — the flat path never checked for a URL of
any kind, on the theory (at the time, unconfirmed) that real Hirist
data might not carry one at all. Since Phase 6 Step 7's live run showed
**100% of real jobs took the flat path** (0/24 used the nested path),
this meant every real job's `job_url` came back empty — not because
the data lacked a URL, but because the code never looked for one on
the path real data actually uses.

## Exact Evidence From Step 9

Phase 6 Step 9 made one, single, explicitly-authorized live request and
persisted the complete raw HTML
(`data/reports/hirist_step9_raw_capture.html`). Offline analysis of
that capture (`data/reports/hirist_raw_payload_capture.md`, Section D)
established, as **directly observed FACT, not inference**:

- **All 20 real `ListItem` entries** on that page have a **top-level
  `url` field**, sibling to `name` and `position` — e.g.:
  ```json
  {"@type": "ListItem", "position": 1, "name": "Verint - Senior DevOps Engineer",
   "url": "https://www.hirist.tech/j/verint-senior-dev-ops-engineer-1672606"}
  ```
- **0/20 entries** had a nested `"item"` object.
- **0/20 entries** had `@id` or `sameAs`.
- No entry had `jobLocation`, `experienceRequirements`, `baseSalary`,
  `description`, or any application/contact field.

This is the entire, exact evidentiary basis for the change below —
nothing beyond what Step 9 directly observed was assumed.

## Exact Code Change

File: `scripts/hirist_parser.py`, function `_parse_job_entry()`, flat
`name` branch only.

**Before:**
```python
    company_part, title_part = name.split(_NAME_DELIMITER, 1)
    company_part = company_part.strip()
    title_part = title_part.strip()
    if not company_part or not title_part:
        return None

    return _empty_raw_job(company_part, title_part)
```

**After:**
```python
    company_part, title_part = name.split(_NAME_DELIMITER, 1)
    company_part = company_part.strip()
    title_part = title_part.strip()
    if not company_part or not title_part:
        return None

    job = _empty_raw_job(company_part, title_part)

    # Phase 6 Step 9/10: top-level ListItem.url, sibling to "name" --
    # the shape every real captured entry actually uses. Same
    # validator as the nested-item path above; never constructs or
    # guesses a URL -- left empty if absent or malformed.
    url = _sane_url_or_none(list_item.get("url"))
    if url:
        job["job_url"] = url

    return job
```

**Nothing else changed.** The company/title split logic (`occurrences
!= 1` fails-closed rule, the delimiter constant, the skip-on-ambiguity
behavior) is **byte-for-byte identical** to before — confirmed by test
F below. The nested-item path is untouched — still reads
`nested_item.get("url")` exactly as before, and still takes priority
whenever a nested `item` object is present (confirmed by test C below,
including the adversarial case of both a nested item and a top-level
`url` on the same entry).

### Validation style — reused, not invented

`_sane_url_or_none()` (unmodified, pre-existing) is reused exactly as
the nested path already used it: accepts any well-formed absolute
`http`/`https` URL, rejects anything else, **does not restrict by
domain**. This project's only existing domain-allowlist
(`_ALLOWED_NEXT_URL_HOST = "www.hirist.tech"`) has only ever applied to
pagination `rel="next"` link-following — never to `job_url`, on either
the nested or flat path, before or after this change. **This
remediation does not introduce a new leniency or a new restriction** —
it is a straight, minimal extension of the flat path to match the
nested path's own long-standing, unchanged validation style. This is
confirmed explicitly by test D below (a well-formed off-domain URL is
accepted identically on both paths).

## Stable-ID Decision

**Instruction:** "Before implementing an ID extraction, inspect the
existing schema/model and determine whether a stable job ID field
already exists and whether the captured URL evidence is sufficient to
populate it safely."

**Finding: a stable ID field already exists in the pipeline schema, and
is already correctly populated as an automatic consequence of this
job_url fix — no new code was needed or added.**

`scripts/discover_local.py`'s `normalize_job()` already calls
`generate_job_id(job)` (from `scripts/job_id.py`) on every normalized
job, with this existing, unmodified priority order:
1. A source-provided `job_id` (none provided by any adapter today).
2. **A stable, deterministic hash of `source` + normalized `job_url`**
   (`job_id_from_url()`).
3. A fallback hash of `source` + `company` + `title` + `location`
   (`job_id_from_fields()`), used only when no usable `job_url` exists.

This means: **the moment `job_url` is correctly populated (this
remediation), `generate_job_id()` automatically produces a
collision-resistant, URL-derived `job_id` for that job — with zero
changes to `hirist_parser.py`, `discover_local.py`, or `job_id.py`.**
Verified directly (offline, in-memory):

```
raw entry: {"name": "Verint - Senior DevOps Engineer",
            "url": "https://www.hirist.tech/j/verint-senior-dev-ops-engineer-1672606"}
normalize_job(...)["job_url"] -> "https://www.hirist.tech/j/verint-senior-dev-ops-engineer-1672606"
normalize_job(...)["job_id"]  -> "HIRIST-5649154751c6aff0"   (deterministic -- reproduced identically on a second call)

raw entry with no url:
normalize_job(...)["job_id"]  -> "HIRIST-d19708782931609b"   (existing field-based fallback, unchanged)
```

**Was a separate, raw, source-native numeric ID extraction (e.g.
pulling `1672606` out of the URL path) implemented? No — deliberately
left unchanged, per the instruction's own escape hatch ("if there is
any ambiguity, leave stable ID unchanged and document why").**
Reasoning:

1. `_empty_raw_job()`'s raw-job-dict schema has **no field** for a
   source-native numeric ID today (only `job_url`, not e.g.
   `source_job_id`). Adding one would be a schema change beyond this
   task's explicit, minimal scope ("modify it minimally").
2. It would be **purely redundant** with the URL-based `job_id`
   mechanism that already exists and now already works correctly —
   `job_id_from_url()` already derives a stable, unique-per-URL hash
   from the exact same `job_url` string; a second, parallel ID
   wouldn't add reliability, only a second thing to keep in sync.
3. While Step 9's evidence strongly *suggests* the URL's trailing
   digits are Hirist's own internal job ID, this was explicitly framed
   as an unconfirmed inference in Step 9's report, not a confirmed
   fact ("do not assume more than the captured evidence establishes" —
   this task's own instruction) — no second live capture with a
   different job has been made to confirm the digits are unique and
   stable across re-crawls, rather than e.g. a listing-position
   artifact.

**Conclusion: the existing schema already supports a stable ID; the
captured URL evidence is sufficient to populate it safely and already
does so automatically via the existing `job_id_from_url()` pipeline;
no new stable-ID extraction code was added to `hirist_parser.py`.**

## Tests

`scripts/test_hirist_job_url_remediation.py` (new file, 6 checks,
covering required cases A–F; G is the full suite run below):

| Case | What it proves |
|---|---|
| A | The **verbatim, real** Step 9 entry (`Verint - Senior DevOps Engineer`, real captured URL) now produces `job_url` equal to the exact real URL |
| B | A `ListItem` with no `url` field leaves `job_url` empty — never guessed or constructed |
| C | Nested `item.url` extraction is unaffected; when **both** a nested item and a top-level `url` are present on the same entry (adversarial case, deliberately using two different URLs), the nested path still wins entirely — the two extraction paths remain fully independent |
| D | A malformed URL (`"not-a-valid-url"`) is rejected on the flat path, exactly as it always was on the nested path; a well-formed **off-domain** URL is **accepted** identically on both paths — confirming this remediation introduced no new domain restriction and no new leniency, matching the pre-existing, unchanged validation style |
| E | The existing, unmodified `valid_results_page1.html`/`valid_results_page2.html` fixtures still classify as `VALID_RESULTS` with the same job counts; the fixture's flat entry (which has no `url` field) still yields `job_url == ""` — this remediation does not retroactively invent a URL for data that never had one |
| F | Company/title split output is **byte-identical** to pre-remediation behavior for single-delimiter entries, and 3+-segment entries are still skipped entirely (fails closed, unchanged) — re-using the exact Step 9 "wrong split" example (`"Senior DevOps Engineer - AWS & Kubernetes"`) to prove name-parsing itself was not touched |

**No heuristic company/title fix was added or attempted**, per explicit
instruction — bare-title, `Company - Title`, and `Company - Title -
Tags` conventions are all still handled exactly as before test F
confirms.

`scripts/test_hirist_normalization_url_forensics.py` (Step 8's
characterization tests) — **test A was updated**, since it previously
asserted the *old* gap (`job_url` stays empty for a top-level `url`
field) as a documented limitation; that gap is now fixed, so the
assertion was updated to reflect the new, correct behavior, with an
explicit note in both the module docstring and the test itself
explaining why. Tests B, C, and D (all genuinely still unfixed
limitations — `application_url`, `baseSalary`/`experienceRequirements`,
and the delimiter ambiguity) are **unchanged and still pass**, since
none of those were touched by this remediation.

**No new Naukri-specific test was added** (test G) — verified instead
by the full standalone suite run below; `naukri_parser.py` was never
modified.

## Unresolved Company/Title Limitation (explicitly not addressed)

Per explicit instruction, this remediation makes **no change** to
company/title parsing. Step 9's evidence (confirmed again, unchanged,
by test F) already established that `name` mixes at least three
conventions with no deterministic disambiguation rule — this remains
exactly as documented in `data/reports/hirist_normalization_url_forensics.md`
and `hirist_raw_payload_capture.md`. **Not addressed here.**

## Verification

```
New test (scripts/test_hirist_job_url_remediation.py, 6 checks):                6/6 PASS
Updated test (scripts/test_hirist_normalization_url_forensics.py, 4 checks):    4/4 PASS
Full standalone regression suite (45 files, including both above):             PASS=45 FAIL=0
python3 -m py_compile across all scripts/*.py:                                 PY_COMPILE_ALL_OK
This report's .json:                                                           valid (json.load)
```

| | Value |
|---|---|
| `HiristAdapter.status` | `AdapterStatus.NOT_ENABLED` (confirmed, unchanged) |
| `HiristAdapter.capabilities` | `frozenset()` (confirmed, unchanged) |
| `search_profile._default_sources()` | `['NAUKRI']` (confirmed, unchanged) |
| Live network requests made this task | **0** |
| Production DB SHA-256 before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production DB SHA-256 after | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production DB row counts | Unchanged (candidates=1, candidate_search_profile=1, jobs=9, candidate_job_matches=0, search_runs=0, search_queue=0) |
| Production DB | Byte-identical; never opened |

## Files Changed

| File | Change |
|---|---|
| `scripts/hirist_parser.py` | Modified: `_parse_job_entry()`'s flat `name` branch now reads a top-level `url` field (via the existing, unmodified `_sane_url_or_none()`); docstrings updated (module-level evidence section + function docstring) to document the Step 9 evidence and the Step 10 change. Company/title logic byte-identical. Nested-item path byte-identical. |
| `scripts/test_hirist_job_url_remediation.py` | New — 6 regression tests (A–F) |
| `scripts/test_hirist_normalization_url_forensics.py` | Modified: test A updated to assert the new, correct behavior (was asserting the now-fixed gap); module docstring updated to note which of the 4 Step 8 findings were/weren't addressed by Step 10 |
| `data/reports/hirist_job_url_remediation.md` / `.json` | New — this report |

**Not modified:** `scripts/hirist_adapter.py`, `scripts/hirist_fetcher.py`,
`scripts/hirist_fetch_bridge.js`, `scripts/naukri_parser.py`,
`scripts/naukri_adapter.py`, `scripts/naukri_fetcher.py`,
`scripts/naukri_fetch_bridge.js`, `scripts/source_registry.py`,
`scripts/search_profile.py`, `scripts/search_worker.py`,
`scripts/score_job.py`, `scripts/discover_local.py`,
`scripts/job_id.py`, `scripts/job_eligibility.py`,
`data/applications/jobos.db`, any existing Hirist fixture, any
LinkedIn file.

## Summary

1. **`job_url` is now correctly extracted for real Hirist data**,
   using exactly the field Step 9's live capture confirmed every real
   entry has — a top-level `url`, sibling to `name`.
2. **This automatically fixes `job_id` too**, at zero additional code
   cost: the existing, unmodified `discover_local.generate_job_id()`
   already derives a deterministic, collision-resistant ID from
   `job_url` whenever one is present — verified directly.
3. **No separate raw numeric stable-ID field was added** — the
   existing schema has none, the URL-based mechanism already
   supersedes the need for one, and the evidence for treating the
   URL's numeric suffix as a confirmed, independently-stable ID is not
   yet strong enough (framed as inference, not fact, in Step 9).
4. **Company/title parsing is completely unchanged** — confirmed by a
   dedicated regression test, per explicit instruction not to address
   it in this step.

**Stopping here, per instruction. No further live Hirist query was
made. Hirist remains `AdapterStatus.NOT_ENABLED` and was not enabled
globally.**
