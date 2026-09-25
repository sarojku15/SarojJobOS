# Phase 6 Step 5 — Hirist Block-Classifier Forensics

**Offline-only investigation. Zero network requests were made — no
Playwright, no `hirist_fetcher.py`, no `hirist_fetch_bridge.js`, no
curl/wget, no browser, no HTTP client of any kind was invoked anywhere
in this task.**

**Conclusion (full reasoning below): the evidence available offline
strongly supports classifier false positive (Category B), though it
falls short of absolute proof because the actual raw HTML from the
Step 4 live response was never persisted to any file and no longer
exists to inspect directly. The classifier's design defect itself —
matching phrases against the full raw HTML rather than extracted
visible text — is confirmed with certainty from code inspection alone,
and it is a verified repeat of an anti-pattern this exact project
already diagnosed, documented, and fixed once before, for the same
trigger word, on a different source (Naukri).**

---

## 1. Exact Classifier Function

`scripts/hirist_parser.py`:

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

called from:

```python
def classify_search_page(html, current_url):
    ...
    block_kind, block_phrase = _find_block_signal(html)
```

`html` here is the **complete raw HTML document source** as returned
by `HiristFetcher.fetch()`, which in turn comes from
`hirist_fetch_bridge.js`'s `const html = await page.content();`
(confirmed directly by reading that exact line of the bridge script) —
i.e. everything: every `<script>` tag's full contents, every `<meta>`
tag, every HTML comment, every inline/external CSS rule, every JSON-LD
block, every tag attribute. **Not** rendered/visible text.

## 2. Exact Matched Phrase/Pattern

`"captcha"` — one entry in:

```python
_CHALLENGE_PHRASES = (
    "captcha",
    "checkpoint",
    "security check",
    "unusual activity",
    "verify you are a human",
    "are you a robot",
)
```

Directly recorded in the Step 4 validation's own captured output
(`data/reports/hirist_one_query_live_validation.json`:
`"matched_phrase": "captcha"`, `"detail": "challenge phrase matched:
'captcha'"`).

## 3. Exact Occurrence/Context

**NOT DETERMINABLE from currently available artifacts.** The Step 4
validation driver captured and persisted only *derived* data
(`html_length`, classifier `state`, `matched_block_phrase`, parsed job
list) — it never wrote the actual fetched HTML body to any file. The
temporary driver script and its output files were deleted at the end
of Step 4, per this project's standard scratchpad-cleanup practice for
one-off validation drivers. **This is an honest, acknowledged evidence
gap**, not a claim that the context doesn't exist — it means this
specific question (visible text vs. attribute vs. JavaScript vs.
JSON-LD vs. metadata vs. comment vs. script vs. unrelated content, and
the exact occurrence count) **cannot be answered from this task's
available offline artifacts.** See Section 10 for how this limits
confidence, and Section 11 for how to prevent this gap in any future
live attempt.

What **can** be determined with certainty (Section 6) is that the
matching mechanism itself scans the entire raw document — which by
construction makes attribute/script/comment/JSON-LD matches just as
possible as a visible-text match.

## 4. Response Length

| | Value |
|---|---|
| Step 4 (blocked) | **312,116 characters** |
| Step 1 (successful, same URL shape) | **312,194 characters** |
| Difference | **78 characters** (0.025% of the page) |

Both directly recorded facts from each step's own report.

## 5. Evidence from the Successful Phase 6 Step 1 Page

Step 1's own **exploratory probe** (`scripts/hirist_discovery_probe.js`
— a *different* script from the production `hirist_parser.py`, written
and run only once, before the production adapter existed) computed a
`captchaOrChallenge` marker using this exact code:

```javascript
const bodyText = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
...
captchaOrChallenge: MARKERS.captchaOrChallenge(bodyText),
```

where `MARKERS.captchaOrChallenge` checks for `'captcha'` (among other
phrases) — **the identical trigger word** that later fired in Step 4.
`page.locator('body').innerText()` returns only **rendered, visible
text**, explicitly excluding script/style contents, hidden elements,
and raw markup.

**This check returned `false`**
(`data/reports/hirist_phase6_inspection.json`: `"captcha":false`,
`"captcha_or_challenge_text_present": false`) on a page of
near-identical length (312,194 chars) to the one Step 4 later flagged.

**This is a directly relevant, already-captured, offline-available data
point**: on a page of almost exactly the same size, the same trigger
word was absent from *visible* text, on the one occasion it was
actually checked that way.

**Caveat, stated plainly**: Step 1 and Step 4 are two separate live
requests at two different times. Page content — especially third-party
ad/tracking/analytics script payloads, which commonly include dynamic
IDs, feature flags, or session-specific data — can legitimately differ
between requests even when overall length is nearly identical. This
does **not** prove the word "captcha" was absent from Step 4's raw HTML
in the same way; it is strong, directly relevant circumstantial
evidence, not conclusive proof.

## 6. Comparison of the Two Pages

| | Step 1 | Step 4 |
|---|---|---|
| HTTP status | 200 | 200 |
| Redirect | No | No |
| HTML length | 312,194 | 312,116 |
| Check performed | `captchaOrChallenge` on **visible text only** (exploratory probe) | `_find_block_signal` on **entire raw HTML** (production parser) |
| Result | `false` (no match) | `true` (matched `"captcha"`) |

The two checks are **not the same test** — they check different
surfaces (visible text vs. raw HTML) using **different code** (a
one-off exploratory probe vs. the production parser). The differing
outcome is therefore consistent with either (a) the page genuinely
differed between requests, or (b) the same underlying content was
present both times, but only the raw-HTML check (Step 4's) was capable
of finding it because it scans markup the visible-text check
(Step 1's) deliberately excludes. **Available evidence cannot
distinguish between these two explanations with certainty** — but
Section 7 provides strong, independent, already-documented reason to
consider (b) far more likely than a coincidence.

## 7. Does "captcha" Occur in Normal Page Content? — Direct, Verified Precedent in This Same Project

This question does **not** need to be answered by speculation. This
project has **already encountered, documented, and fixed** this exact
issue once before, for a **different source** (Naukri), with the
**same trigger word**. From `scripts/naukri_parser.py`'s own,
pre-existing docstrings (written months before this Hirist
investigation, for entirely unrelated reasons):

> `detect_block_reason()`: *"Classify a captured page as blocked or
> not, based only on human-visible text (title + body) -- never on raw
> HTML source, tag attributes, CSS, or script content."*
>
> *"Guessing at keyword rules for conditions never actually observed is
> exactly how an earlier naive, raw-HTML keyword check produced false
> positives on this same search-results fixture."*

And from `_VisibleTextExtractor`'s own docstring:

> *"Naive tag-stripping... leaves CSS rule text and class-name strings
> behind as if they were visible page content. On
> search_results_sre_bengaluru.html, that naive approach 'sees' the
> words 'captcha' and 'verify' inside a dormant `.bot-guard-captcha` CSS
> rule and an `otpMobileVerifyContainer` class name -- neither of which
> a human visitor ever sees rendered on the page."*

**This was independently re-verified in this task** (zero network
access — a local file already present in this repository,
`data/fixtures/naukri/search_results_sre_bengaluru.html`, was searched
with a plain-text `grep`):

```
$ grep -io "captcha[a-z-]*\|[a-z-]*captcha[a-z-]*" search_results_sre_bengaluru.html | sort -u
bot-guard-captcha
bot-guard-captcha-portal
grecaptcha-badge
recaptcha
```

**Confirmed, directly, from a real file:** the literal word
`"captcha"` genuinely appears **four separate ways** in this real,
already-captured, successful Naukri search-results page's raw
HTML/CSS — as CSS class names (`bot-guard-captcha`,
`bot-guard-captcha-portal`) and Google reCAPTCHA badge/library
references (`grecaptcha-badge`, `recaptcha`) — almost certainly from a
defensive, site-wide bot-protection bundle that ships in every page's
CSS/JS regardless of whether it is ever actively triggered for a given
visitor.

**A second, independent, already-documented example exists for a third
source (LinkedIn):** Phase 5 Step 1's inspection
(`data/reports/linkedin_phase5_inspection.md`) directly observed
`data-recaptcha-v3-integration-lix-value="control"` in LinkedIn's own
page `<meta>` config — the literal string `"recaptcha"` present in raw
markup on a page that was, at the time, confirmed reachable with no
actual block or challenge shown.

**Pattern across three independent sources (Naukri, LinkedIn, and now
suspected for Hirist):** major job-board/commercial sites commonly ship
reCAPTCHA-family bot-protection tooling (CSS classes, JS libraries,
config flags) site-wide, present in every page's raw markup whether or
not it is ever actively shown to a given visitor. A phrase-match against
raw HTML for a word like `"captcha"` is, based on this project's own
prior, real-world evidence, **known to be prone to exactly this false
positive** on this class of site.

**Decisive, executable proof added in this task** (offline, zero
network — `scripts/test_hirist_block_classifier_forensics.py`): a
synthetic page containing a fully valid, parseable `schema.org`
`ItemList` (one real job entry) plus a `<style>` block with a
`.bot-guard-captcha`-style CSS class name — mirroring the real Naukri
precedent exactly, but constructed fresh for Hirist's own page shape —
**is, right now, with the current unmodified `hirist_parser.py`,
incorrectly classified as `SOFT_BLOCK_OR_CHALLENGE`** instead of
`VALID_RESULTS`. A second, positive-control fixture (the word genuinely
present in visible body text) correctly classifies as
`SOFT_BLOCK_OR_CHALLENGE`, confirming the defect is specifically about
*where* the word appears, not that the word itself is inherently
unreliable. **This proves the general mechanism with certainty** — it
does not, and cannot, prove that this specific mechanism is what
happened in the actual Step 4 live response (Section 3's evidence gap
remains: the real HTML was never saved), but it eliminates any doubt
that the classifier's raw-HTML-scanning design is capable of exactly
this kind of false positive on a genuinely valid Hirist-shaped page.

## 8. Does It Represent an Actual Challenge?

**Not determinable with certainty from available evidence** (Section
3's gap). However:

- The classifier's own reported `detail` (`"challenge phrase matched:
  'captcha'"`) records only that the substring was found *somewhere*
  in 312,116 characters of raw HTML — it carries no information about
  whether that occurrence was inside a visible interstitial, a CSS
  rule, a script tag, or a JSON config value.
- No corroborating signal was captured: no CAPTCHA widget/iframe
  detection, no "access denied"-style hard-block phrase also matched
  (`_HARD_BLOCK_PHRASES` did not fire), no HTTP status change (still
  200), no redirect to a challenge URL.
- Section 7's precedent shows this exact word, in this exact class of
  site, has already been confirmed to appear in non-challenge,
  purely-defensive markup on at least one directly comparable job
  board's real page.

## 9. Classification Conclusion

**Evidence supports: B (classifier false positive) — strongly, but not
with absolute certainty.**

This is explicitly **not** chosen "merely from HTTP 200" (which the
task correctly warns against) — the reasoning chain is:

1. The classifier's design is **confirmed, with certainty, from code
   alone**, to scan raw HTML rather than visible text (Sections 1, 6).
2. This exact design pattern has **already been proven, in this same
   project, on a real captured page, to produce a false positive on
   the same trigger word** (Section 7) — not a hypothetical risk, a
   documented, previously-fixed incident.
3. The one *available* apples-to-apples signal (Step 1's visible-text-only
   check, on a page of near-identical length) found the word **absent**
   from visible content (Section 5).
4. No corroborating hard-block or structural-challenge signal
   accompanied the match (Section 8).

**What stops this from being labeled a fully CONFIRMED false positive
rather than a strongly-supported one:** the actual raw HTML from the
Step 4 response no longer exists to point to the literal matched
location, and Step 1/Step 4 are different live moments, so a genuine
transient difference cannot be completely ruled out.

**Category A (genuine Hirist challenge) is not supported by any
positive evidence** in this task's available artifacts — it remains
possible only in the sense that it cannot be actively disproven without
the missing raw HTML, not because anything points toward it.

## 10. Confidence / Evidence Limitations

| Evidence | Strength |
|---|---|
| Classifier scans raw HTML, not visible text | **Certain** (code inspection) |
| This exact pattern produced a real false positive before, same word, this project | **Certain** (verified against a real, local file) |
| Page-length near-match to a confirmed-clean page | **Suggestive**, not proof (different live moment) |
| Visible-text-only check on a similar page found nothing | **Suggestive**, directly relevant, not proof of Step 4 specifically |
| Exact match location/context in the Step 4 response | **Not determinable** — raw HTML was never saved |
| Whether Hirist's real page contains a similar dormant bot-protection bundle | **Not directly confirmed** — inferred by analogy to Naukri/LinkedIn, not observed on Hirist's own markup |

## 11. Exact Remediation Required, If Any

**A specific, well-evidenced bug is identified. Per instruction,
`hirist_parser.py` is NOT modified in this task** — diagnosis only,
implementation deferred to a separate, future step. The recommended
fix, precisely:

1. **Reuse `naukri_parser.py`'s already-existing, already-tested
   `_VisibleTextExtractor` approach** (an `HTMLParser` subclass that
   extracts only `<title>` + body text nodes, explicitly excluding
   `<script>`/`<style>` content and all tag/attribute markup) — apply
   the same extraction to `hirist_parser.py`, and change
   `_find_block_signal()` to check the extracted visible text instead
   of the raw HTML string. This is not a new design; it is applying an
   already-built, already-proven fix from elsewhere in this same
   codebase to a module that did not yet have it.
2. Add an offline regression fixture reproducing this exact scenario:
   a synthetic page containing the word `"captcha"` **only** inside a
   `<style>`/CSS class name or a `<script>` block, with **no**
   occurrence in visible body text — asserting the fixed classifier
   returns `VALID_RESULTS` (or whatever the page's actual content
   otherwise warrants), not `SOFT_BLOCK_OR_CHALLENGE`.
3. Keep a **second** fixture where `"captcha"` genuinely appears in
   visible body text (e.g. "Please complete the CAPTCHA below") —
   confirming the fix does not silently stop detecting a real challenge
   phrase, only a markup-only one. (`data/fixtures/hirist/
   challenge_captcha.html`, already created in Phase 6 Step 3, already
   places the phrase in visible `<p>` text — it should continue to
   classify as `SOFT_BLOCK_OR_CHALLENGE` after the fix, and re-running
   it post-fix is a cheap, valuable regression check.)
4. Apply the exact same fix to `_HARD_BLOCK_PHRASES` checking, for
   consistency, even though no evidence yet suggests it has produced a
   false positive — the underlying design flaw (raw-HTML scanning) is
   identical for both phrase lists.

## 12. Should Another Live Validation Be Considered After Remediation?

**Yes, conditionally — not recommended now, and not authorized by this
report.** If the Section 11 remediation is implemented and passes new
offline regression tests (per this project's standing phase
discipline: fix offline, test offline, only then re-attempt live), a
**single**, freshly-authorized live query — identical in scope to Step
4 (same role/location, one query, no retries) — would be the
appropriate next step to determine whether the *fixed* classifier
reaches a different, more informative conclusion (ideally reaching the
JSON-LD parsing step this time, which Step 4 never got to test at all).

**This report does not authorize, schedule, or recommend that live
attempt happen now.** It should follow, not precede, the Section 11
fix — and remains subject to your own, separate, explicit decision.

---

## Files Touched in This Task

**New (diagnostic test only):** `scripts/test_hirist_block_classifier_forensics.py`
— an offline, zero-network test proving the general mechanism (Section
7) with concrete, executable evidence, using only in-memory synthetic
HTML strings. **No existing file was modified** — `hirist_parser.py`
itself, and every other production file, is byte-for-byte unchanged by
this task.

## Zero Network Requests — Confirmed

No `curl`, `wget`, Playwright, `hirist_fetcher.py`, or
`hirist_fetch_bridge.js` invocation occurred anywhere in this task.
Every piece of evidence above came from: (a) reading existing,
already-committed source files (`hirist_parser.py`,
`hirist_fetch_bridge.js`, `naukri_parser.py`,
`hirist_discovery_probe.js`), (b) reading existing report files
(`hirist_phase6_inspection.*`, `hirist_one_query_live_validation.*`,
`linkedin_phase5_inspection.*`), and (c) a plain-text `grep` against one
already-present local fixture file
(`data/fixtures/naukri/search_results_sre_bengaluru.html`).

## Production Safety

- `data/applications/jobos.db`: not opened.
- `search_worker.py`, `source_registry.py`, `search_profile.py`,
  scoring, eligibility, freshness, deduplication, Naukri, LinkedIn: not
  touched.
- `hirist_parser.py`: **not modified** — diagnosis only, per
  instruction.
- Hirist: not enabled.

**Stopping here, per instruction. No live request was made. No
multi-query validation was run. No classifier code was changed.**
