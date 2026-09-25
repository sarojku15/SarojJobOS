#!/usr/bin/env python3

"""
Regression test for the Phase 6 Step 6 block-classifier remediation
(data/reports/hirist_block_classifier_remediation.md), which fixed the
Phase 6 Step 5 forensic finding (data/reports/
hirist_block_classifier_forensics.md): hirist_parser._find_block_signal()
previously scanned the ENTIRE raw HTML document for phrases like
"captcha", causing a false positive whenever that word appeared in
dormant markup (CSS class names, <script src> attributes, JSON-LD
payload text) rather than genuine visible page content -- exactly the
already-documented, already-fixed naukri_parser.py precedent
(.bot-guard-captcha CSS class on a real captured Naukri page), now
also fixed for Hirist by reusing naukri_parser._extract_visible_text().

Covers the six scenarios required for Phase 6 Step 6:
  A. "captcha" only in CSS class/style/script/comment/attribute does
     NOT cause SOFT_BLOCK_OR_CHALLENGE when valid JSON-LD results are
     present
  B. "captcha" genuinely present in visible body text DOES cause
     SOFT_BLOCK_OR_CHALLENGE
  C. existing hard-block phrases still classify correctly
  D. existing valid Hirist JSON-LD pages (the real fixtures) still
     classify as VALID_RESULTS
  E. off-domain pagination protection remains unchanged
  F. no Naukri regressions (verified separately by the full suite run
     reported in data/reports/hirist_block_classifier_remediation.md --
     this file does not duplicate Naukri's own test suite)

Makes ZERO network requests -- pure, offline, in-memory/fixture-file
HTML only.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "data" / "fixtures" / "hirist"
sys.path.insert(0, str(ROOT / "scripts"))

from hirist_parser import classify_search_page, HiristPageState, _find_next_page_url


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _read_fixture(name):
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# Same synthetic page used in the Phase 6 Step 5 forensic diagnostic:
# a fully valid, parseable schema.org ItemList (one real job entry)
# plus a <style> block containing a .bot-guard-captcha-style CSS class
# name and a <script src="...recaptcha..."> attribute -- mirrors the
# real, already-confirmed Naukri precedent exactly, applied fresh to
# Hirist's own page shape. Before the Phase 6 Step 6 fix, this page was
# INCORRECTLY classified as SOFT_BLOCK_OR_CHALLENGE.
_PAGE_WITH_CAPTCHA_ONLY_IN_CSS = """
<!DOCTYPE html><html lang="en"><head>
<title>Search for - Senior Devops Engineer Jobs | hirist.tech</title>
<style>
.bot-guard-captcha { display: none; position: fixed; z-index: 1001; }
.bot-guard-captcha-portal .overlay { z-index: 10050; }
</style>
<script src="https://www.google.com/recaptcha/api.js" async defer></script>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "ItemList",
  "numberOfItems": 1,
  "itemListElement": [
    {"@type": "ListItem", "position": 1, "name": "Verint - Senior DevOps Engineer"}
  ]
}
</script>
</head><body>
<h1>Senior Devops Engineer Jobs</h1>
<p>Browse the latest openings below.</p>
</body></html>
"""

_PAGE_WITH_CAPTCHA_IN_VISIBLE_TEXT = """
<!DOCTYPE html><html><head><title>Security Check</title></head>
<body>
<h1>Please verify you are a human</h1>
<p>Complete the CAPTCHA to continue.</p>
</body></html>
"""

# "captcha" also present in an HTML comment -- comments are excluded
# from visible text by the same HTMLParser mechanism (handle_comment
# is never routed to handle_data), so this must also NOT trigger a
# false positive after the fix.
_PAGE_WITH_CAPTCHA_ONLY_IN_COMMENT = """
<!DOCTYPE html><html><head>
<title>Search for - Senior Devops Engineer Jobs | hirist.tech</title>
<!-- TODO: re-enable captcha widget once legal signs off -->
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList","numberOfItems":1,
"itemListElement":[{"@type":"ListItem","position":1,"name":"Acme Corp - Staff SRE"}]}
</script>
</head><body><h1>Senior Devops Engineer Jobs</h1></body></html>
"""


def test_A_captcha_only_in_css_no_longer_misclassified(failures):
    classification = classify_search_page(_PAGE_WITH_CAPTCHA_ONLY_IN_CSS, "https://x/test")

    if classification.state != HiristPageState.VALID_RESULTS:
        return _fail(
            failures,
            f"A: expected VALID_RESULTS for CSS/attribute-only 'captcha' after remediation, "
            f"got {classification.state} (detail={classification.detail!r})",
        )
    if len(classification.jobs) != 1 or classification.jobs[0]["company"] != "Verint":
        return _fail(failures, f"A: expected the one valid job to still be parsed, got {classification.jobs}")

    print("PASS: A -> 'captcha' present only in CSS class name / <script src> attribute no longer causes SOFT_BLOCK_OR_CHALLENGE; valid ItemList is correctly parsed")


def test_A2_captcha_only_in_comment_no_longer_misclassified(failures):
    classification = classify_search_page(_PAGE_WITH_CAPTCHA_ONLY_IN_COMMENT, "https://x/test-comment")

    if classification.state != HiristPageState.VALID_RESULTS:
        return _fail(
            failures,
            f"A2: expected VALID_RESULTS for comment-only 'captcha' after remediation, got {classification.state}",
        )

    print("PASS: A2 -> 'captcha' present only in an HTML comment does not cause SOFT_BLOCK_OR_CHALLENGE")


def test_B_captcha_in_visible_text_still_classified(failures):
    classification = classify_search_page(_PAGE_WITH_CAPTCHA_IN_VISIBLE_TEXT, "https://x/test2")

    if classification.state != HiristPageState.SOFT_BLOCK_OR_CHALLENGE:
        return _fail(failures, f"B: expected SOFT_BLOCK_OR_CHALLENGE for genuinely visible 'CAPTCHA' text, got {classification.state}")
    if classification.matched_block_phrase != "captcha":
        return _fail(failures, f"B: expected matched_block_phrase='captcha', got {classification.matched_block_phrase!r}")

    print("PASS: B -> a genuine visible-text CAPTCHA phrase is still correctly classified as SOFT_BLOCK_OR_CHALLENGE")


def test_C_hard_block_phrases_still_classify_correctly(failures):
    classification = classify_search_page(_read_fixture("blocked_access_denied.html"), "https://x/blocked")

    if classification.state != HiristPageState.BLOCKED:
        return _fail(failures, f"C: expected BLOCKED for the existing blocked_access_denied.html fixture, got {classification.state}")
    if classification.matched_block_phrase != "access denied":
        return _fail(failures, f"C: expected matched_block_phrase='access denied', got {classification.matched_block_phrase!r}")

    print("PASS: C -> existing hard-block fixture (blocked_access_denied.html) still classifies as BLOCKED with the correct matched phrase")


def test_D_existing_valid_fixtures_still_valid_results(failures):
    for fixture_name in ["valid_results_page1.html", "valid_results_page2.html"]:
        classification = classify_search_page(_read_fixture(fixture_name), "https://x/valid")
        if classification.state != HiristPageState.VALID_RESULTS:
            return _fail(failures, f"D: expected VALID_RESULTS for {fixture_name}, got {classification.state} (detail={classification.detail!r})")
        if not classification.jobs:
            return _fail(failures, f"D: expected at least one job parsed from {fixture_name}, got none")

    empty_classification = classify_search_page(_read_fixture("empty_results.html"), "https://x/empty")
    if empty_classification.state != HiristPageState.VALID_EMPTY_RESULT:
        return _fail(failures, f"D: expected VALID_EMPTY_RESULT for empty_results.html, got {empty_classification.state}")

    print("PASS: D -> existing valid Hirist JSON-LD fixtures (page1, page2, empty) still classify correctly, unaffected by the remediation")


def test_E_off_domain_pagination_protection_unchanged(failures):
    # Same check as test_hirist_adapter.py's test 8 -- re-verified here
    # explicitly because the remediation touches the same module.
    classification = classify_search_page(
        _read_fixture("next_link_off_domain.html"),
        "https://www.hirist.tech/search/senior-devops-engineer-jobs",
    )
    if classification.next_url is not None:
        return _fail(failures, f"E: an off-domain rel=next link was followed after remediation: {classification.next_url!r}")

    valid_next = _find_next_page_url(
        _read_fixture("valid_results_page1.html"),
        "https://www.hirist.tech/search/senior-devops-engineer-jobs?locations=Bengaluru",
    )
    if valid_next != "https://www.hirist.tech/search/senior-devops-engineer-jobs?page=2":
        return _fail(failures, f"E: same-domain rel=next extraction regressed: {valid_next!r}")

    print("PASS: E -> off-domain pagination protection remains unchanged; same-domain rel=next extraction still works")


def main():
    print("HIRIST BLOCK-CLASSIFIER REMEDIATION REGRESSION TEST (Phase 6 Step 6)")
    print("=========================================================================")

    failures = []

    test_A_captcha_only_in_css_no_longer_misclassified(failures)
    test_A2_captcha_only_in_comment_no_longer_misclassified(failures)
    test_B_captcha_in_visible_text_still_classified(failures)
    test_C_hard_block_phrases_still_classify_correctly(failures)
    test_D_existing_valid_fixtures_still_valid_results(failures)
    test_E_off_domain_pagination_protection_unchanged(failures)

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll block-classifier remediation regression tests passed.")


if __name__ == "__main__":
    main()
