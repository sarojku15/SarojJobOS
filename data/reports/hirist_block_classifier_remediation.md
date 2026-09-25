# Phase 6 Step 6 — Hirist Block-Classifier Remediation (Offline Only)

**Zero network requests. Hirist remains `AdapterStatus.NOT_ENABLED`,
`capabilities = frozenset()`. No live fetch code was touched. No live
validation was performed.**

## Previous Raw-HTML False-Positive Mechanism (Recap)

Diagnosed in Phase 6 Step 5
(`data/reports/hirist_block_classifier_forensics.md`):
`hirist_parser._find_block_signal(html)` checked phrases like
`"captcha"` against the **entire raw HTML document** — every
`<script>` tag's contents and attributes, every `<style>` rule, every
HTML comment, every JSON-LD payload — not just what a human visitor
would actually see rendered. This is a **confirmed repeat** of an
already-documented, already-fixed anti-pattern in this same project:
`naukri_parser.py`'s own docstrings record a real prior incident where
the literal word `"captcha"` was found inside a dormant
`.bot-guard-captcha` CSS class on a genuinely valid, successful Naukri
search-results page — independently re-verified in Step 5 by grepping
that real, local fixture file.

## Exact Remediation

`scripts/hirist_parser.py`, `_find_block_signal()` — the **smallest
possible change**:

**Before:**
```python
def _find_block_signal(html: str):
    lowered = html.lower()
    for phrase in _HARD_BLOCK_PHRASES:
        if phrase in lowered:
            return ("BLOCKED", phrase)
    for phrase in _CHALLENGE_PHRASES:
        if phrase in lowered:
            return ("SOFT_BLOCK_OR_CHALLENGE", phrase)
    return (None, None)
```

**After:**
```python
def _find_block_signal(html: str):
    title, visible_text = _extract_visible_text(html)
    combined = f"{title} {visible_text}".lower()

    for phrase in _HARD_BLOCK_PHRASES:
        if phrase in combined:
            return ("BLOCKED", phrase)
    for phrase in _CHALLENGE_PHRASES:
        if phrase in combined:
            return ("SOFT_BLOCK_OR_CHALLENGE", phrase)
    return (None, None)
```

with one new import at the top of the file:

```python
# Reused, unmodified, from naukri_parser.py -- see module docstring.
# naukri_parser.py is not edited by this import.
from naukri_parser import _extract_visible_text
```

**`naukri_parser.py` itself was not modified in any way** — this reuses
the existing, already-tested `_extract_visible_text()` /
`_VisibleTextExtractor` utility exactly as-is via a plain Python
import. That utility is genuinely source-agnostic (it knows nothing
about Naukri's own page structure; it only knows how to skip
`<script>`/`<style>` element content and never treat tag names or
attributes as text), which is precisely why "reuse the established
Naukri approach" was both possible and appropriate here without
touching Naukri at all.

**No other function in `hirist_parser.py` was changed.**
`classify_search_page()` still passes the full, unmodified raw `html`
string to `_find_item_list_json_ld()` for JSON-LD extraction — only
`_find_block_signal()`'s internal check was narrowed to visible text.
Pagination-link discovery (`_find_next_page_url()`), job-entry parsing
(`_parse_job_entry()`), and every other function are byte-for-byte
unchanged.

## Why This Is Safer

- **Structurally eliminates an entire class of false positive**,
  rather than trying to out-guess every noisy keyword that might
  appear in third-party tracking scripts, bot-protection CSS, or JSON
  config data — the same reasoning `naukri_parser.py`'s own docstring
  already gives for its identical design.
- **Does not weaken genuine detection**: a phrase genuinely shown to a
  visitor (in the page `<title>` or any visible body text) is still
  found exactly as before — `_extract_visible_text()` preserves both
  title and body text, only excluding non-visible markup.
- **Reuses an already-proven mechanism** rather than inventing a new
  one: `_VisibleTextExtractor` has been running correctly in
  production for Naukri since it was first built, with its own
  documented true-positive and true-negative fixture coverage.
- **Minimal blast radius**: one function's internal implementation
  changed; one new (read-only) import; zero other files modified.

## Test Cases Added / Updated

`scripts/test_hirist_block_classifier_forensics.py` was **rewritten**
(from Phase 6 Step 5's characterization test, which documented the bug,
to a proper post-fix regression suite) — 6 checks, all passing against
the now-remediated code:

| # | Scenario | Result |
|---|---|---|
| A | `"captcha"` only in a CSS class name + `<script src="...recaptcha...">` attribute, with a valid `ItemList` present | **No longer** misclassified — correctly `VALID_RESULTS`, the one valid job still parsed |
| A2 | `"captcha"` only inside an HTML comment | Correctly `VALID_RESULTS` (comments were already excluded by `_VisibleTextExtractor`'s underlying `HTMLParser` base behavior — confirmed, not assumed) |
| B | `"captcha"` genuinely present in visible `<p>` body text | Still correctly `SOFT_BLOCK_OR_CHALLENGE` — detection is **not weakened** |
| C | Existing hard-block fixture (`blocked_access_denied.html`) | Still correctly `BLOCKED`, correct matched phrase |
| D | Existing valid Hirist fixtures (`valid_results_page1.html`, `valid_results_page2.html`, `empty_results.html`) | All still classify exactly as before (`VALID_RESULTS` / `VALID_EMPTY_RESULT`), fully unaffected |
| E | Off-domain `rel="next"` pagination protection (`next_link_off_domain.html`) | Unchanged — still refuses to follow an off-domain link; same-domain extraction still works |

**Requirement F (no Naukri regressions)** was verified by the full
standalone suite run below, rather than duplicated in this file —
`naukri_parser.py` was never modified, so no Naukri-specific test could
plausibly be affected, and none were.

**Every existing test in `scripts/test_hirist_adapter.py`** (all 14
Phase 6 Step 3 scenarios) was also re-run against the fixed classifier
and continues to pass unmodified — none of them depended on the old
(buggy) raw-HTML-scanning behavior.

## Confirmations

1. **Zero live requests**: no Playwright, no `hirist_fetcher.py`, no
   `hirist_fetch_bridge.js`, no curl/wget/browser/HTTP client was
   invoked anywhere in this task. Every test uses synthetic, in-memory
   HTML strings or the existing static fixture files.
2. **Hirist remains `NOT_ENABLED`**: `HiristAdapter.status ==
   AdapterStatus.NOT_ENABLED`, re-verified after this change.
3. **Capabilities remain empty**: `HiristAdapter.capabilities ==
   frozenset()`, re-verified after this change.
4. **Production DB untouched**: never opened; SHA-256 confirmed
   byte-identical before and after (see below).
5. **Naukri behavior unchanged**: `naukri_parser.py`,
   `naukri_adapter.py`, `naukri_fetcher.py`, and
   `naukri_fetch_bridge.js` were not modified in any way — the fix
   only *imports* an existing, unmodified Naukri utility function.
6. **`source_registry.py`, `search_profile.py`, `search_worker.py`,
   scoring, eligibility, freshness, deduplication**: not modified.
7. **`hirist_adapter.py`, live fetch code
   (`hirist_fetcher.py`/`hirist_fetch_bridge.js`)**: not modified — this
   remediation is confined entirely to `hirist_parser.py`'s
   classification logic.
8. **Block/challenge taxonomy unchanged**: still exactly
   `VALID_RESULTS` / `VALID_EMPTY_RESULT` / `BLOCKED` /
   `SOFT_BLOCK_OR_CHALLENGE` / `PARSE_FAILURE`; `_HARD_BLOCK_PHRASES`
   and `_CHALLENGE_PHRASES` word lists themselves are unchanged — only
   *what text they're checked against* changed.

## Verification Results

```
scripts/test_hirist_block_classifier_forensics.py (rewritten, 6 checks): 6/6 PASS
scripts/test_hirist_adapter.py (all 14 Phase 6 Step 3 scenarios, re-run against the fix): 14/14 PASS
Full standalone regression suite (43 files):                              PASS=43 FAIL=0
python3 -m py_compile across all scripts/*.py:                            PY_COMPILE_ALL_OK
This report's .json:                                                       valid (json.load)
```

| | Value |
|---|---|
| `HiristAdapter.status` | `AdapterStatus.NOT_ENABLED` (confirmed, unchanged) |
| `HiristAdapter.capabilities` | `frozenset()` (confirmed, unchanged) |
| `search_profile._default_sources()` | `['NAUKRI']` (confirmed, unchanged) |
| Production DB SHA-256 before | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production DB SHA-256 after | `174fc2e752dbd168ef3d99c42efed2c0df64a9b825b2b1a54c02c25cbcd87dc8` |
| Production DB | Byte-identical; never opened |

## Files Changed

| File | Change |
|---|---|
| `scripts/hirist_parser.py` | Modified: `_find_block_signal()` now checks extracted visible text instead of raw HTML; one new import (`from naukri_parser import _extract_visible_text`); docstring updated to document the reuse and the remediation |
| `scripts/test_hirist_block_classifier_forensics.py` | Rewritten: from a Step 5 characterization test (documenting the bug) to a proper Step 6 post-fix regression suite (6 checks, A–E as specified plus a comment-based A2 variant) |
| `data/reports/hirist_block_classifier_remediation.md` / `.json` | New — this report |

**Not modified:** `scripts/naukri_parser.py`, `scripts/naukri_adapter.py`,
`scripts/naukri_fetcher.py`, `scripts/naukri_fetch_bridge.js`,
`scripts/hirist_adapter.py`, `scripts/hirist_fetcher.py`,
`scripts/hirist_fetch_bridge.js`, `scripts/source_registry.py`,
`scripts/search_profile.py`, `scripts/search_worker.py`,
`scripts/score_job.py`, `scripts/job_eligibility.py`,
`scripts/experience_eligibility.py`, `scripts/location_taxonomy.py`,
`scripts/freshness.py`, `scripts/discover_local.py`,
`scripts/cross_source_dedup.py`, `scripts/canonical_job.py`,
`data/applications/jobos.db`, any LinkedIn file.

**Stopping here, per instruction. No live Hirist query was performed.
This remediation is offline-only, awaiting your review before any
further live step.**
