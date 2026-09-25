#!/usr/bin/env python3

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import BlockReason
from naukri_parser import classify_search_page, SearchPageState

FIXTURES_DIR = ROOT / "data" / "fixtures" / "naukri"


def load_fixture(filename):
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


def main():
    failures = []

    print("NAUKRI SEARCH PAGE CLASSIFICATION TEST")
    print("=======================================")

    # --- 1. Real valid search-results fixture -> VALID_RESULTS ---
    html = load_fixture("search_results_sre_bengaluru.html")
    result = classify_search_page(html)

    if result.state != SearchPageState.VALID_RESULTS:
        failures.append(
            f"search_results_sre_bengaluru.html: expected VALID_RESULTS, got {result.state}"
        )
    elif result.job_link_count != 21:
        failures.append(
            f"search_results_sre_bengaluru.html: expected job_link_count=21, got {result.job_link_count}"
        )
    else:
        print(f"PASS: search_results_sre_bengaluru.html -> {result.state} ({result.job_link_count} links)")

    # --- 2. Existing blocked fixture -> BLOCKED ---
    html = load_fixture("blocked_access_denied.html")
    result = classify_search_page(html)

    if result.state != SearchPageState.BLOCKED:
        failures.append(
            f"blocked_access_denied.html: expected BLOCKED, got {result.state}"
        )
    elif result.block_reason != BlockReason.UNKNOWN_BLOCK:
        failures.append(
            f"blocked_access_denied.html: expected block_reason=UNKNOWN_BLOCK, got {result.block_reason}"
        )
    else:
        print(f"PASS: blocked_access_denied.html -> {result.state} ({result.block_reason})")

    # --- 3. Synthetic legitimate empty-result fixture -> VALID_EMPTY_RESULT ---
    html = load_fixture("search_results_empty_valid.html")
    result = classify_search_page(html)

    if result.state != SearchPageState.VALID_EMPTY_RESULT:
        failures.append(
            f"search_results_empty_valid.html: expected VALID_EMPTY_RESULT, got {result.state}"
        )
    elif result.job_link_count != 0:
        failures.append(
            f"search_results_empty_valid.html: expected job_link_count=0, got {result.job_link_count}"
        )
    elif not result.has_shell_markers:
        failures.append(
            "search_results_empty_valid.html: expected has_shell_markers=True"
        )
    else:
        print(f"PASS: search_results_empty_valid.html -> {result.state}")

    # --- 4. Synthetic soft-block/challenge fixture -> SOFT_BLOCK_OR_CHALLENGE ---
    html = load_fixture("search_results_soft_block.html")
    result = classify_search_page(html)

    if result.state != SearchPageState.SOFT_BLOCK_OR_CHALLENGE:
        failures.append(
            f"search_results_soft_block.html: expected SOFT_BLOCK_OR_CHALLENGE, got {result.state}"
        )
    else:
        print(f"PASS: search_results_soft_block.html -> {result.state}")

    # --- 5. Synthetic unrecognized-structure fixture -> PARSE_FAILURE ---
    html = load_fixture("unrecognized_structure.html")
    result = classify_search_page(html)

    if result.state != SearchPageState.PARSE_FAILURE:
        failures.append(
            f"unrecognized_structure.html: expected PARSE_FAILURE, got {result.state}"
        )
    else:
        print(f"PASS: unrecognized_structure.html -> {result.state}")

    # --- 6. SAFETY RULE: zero job links + no shell markers must NEVER
    #        become VALID_EMPTY_RESULT, regardless of which specific
    #        non-empty bucket (SOFT_BLOCK_OR_CHALLENGE or PARSE_FAILURE)
    #        it lands in. Checked against both synthetic fixtures above
    #        plus a fresh minimal inline case with no markers at all. ---
    print("Case 6: unknown-structure safety rule")
    safety_failed = False

    for filename in ("search_results_soft_block.html", "unrecognized_structure.html"):
        html = load_fixture(filename)
        result = classify_search_page(html)
        if result.state == SearchPageState.VALID_EMPTY_RESULT:
            failures.append(
                f"SAFETY VIOLATION: {filename} was classified VALID_EMPTY_RESULT "
                "despite having no search-results shell markers"
            )
            safety_failed = True

    inline_html = "<html><head><title>x</title></head><body><div>nothing here</div></body></html>"
    result = classify_search_page(inline_html)
    if result.state == SearchPageState.VALID_EMPTY_RESULT:
        failures.append(
            "SAFETY VIOLATION: bare inline HTML with no shell markers was "
            "classified VALID_EMPTY_RESULT"
        )
        safety_failed = True
    elif result.state != SearchPageState.PARSE_FAILURE:
        failures.append(
            f"bare inline HTML: expected PARSE_FAILURE, got {result.state}"
        )
        safety_failed = True

    if not safety_failed:
        print("  PASS: no unrecognized-structure page was classified VALID_EMPTY_RESULT")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll Naukri search-page classification tests passed.")


if __name__ == "__main__":
    main()
