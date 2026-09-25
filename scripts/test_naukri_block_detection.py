#!/usr/bin/env python3

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from source_adapter import BlockReason
from naukri_parser import detect_block_reason

FIXTURES_DIR = ROOT / "data" / "fixtures" / "naukri"


def load_fixture(filename):
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


def main():
    failures = []

    print("NAUKRI BLOCK DETECTION TEST")
    print("============================")

    # --- homepage.html -> BlockReason.NONE ---
    html = load_fixture("homepage.html")
    result = detect_block_reason(html)

    if result != BlockReason.NONE:
        failures.append(
            f"homepage.html: expected BlockReason.NONE, got {result}"
        )
    else:
        print(f"PASS: homepage.html -> {result}")

    # --- search_results_sre_bengaluru.html -> BlockReason.NONE ---
    # This fixture is known to contain dormant CSS/markup for a
    # .bot-guard-captcha overlay and a hidden OTP-verify form field,
    # neither of which is an active block. A naive keyword-only
    # detector would incorrectly flag this page as blocked.
    html = load_fixture("search_results_sre_bengaluru.html")
    result = detect_block_reason(html)

    if result != BlockReason.NONE:
        failures.append(
            "search_results_sre_bengaluru.html: expected "
            f"BlockReason.NONE, got {result}"
        )
    else:
        print(f"PASS: search_results_sre_bengaluru.html -> {result}")

    # --- blocked_access_denied.html -> BlockReason from its companion
    #     .expected.json (the expected value is not hardcoded here) ---
    html = load_fixture("blocked_access_denied.html")

    expected_path = FIXTURES_DIR / "blocked_access_denied.expected.json"
    expected_data = json.loads(expected_path.read_text(encoding="utf-8"))
    expected_reason = BlockReason[expected_data["expected_block_reason"]]

    result = detect_block_reason(html)

    if result != expected_reason:
        failures.append(
            f"blocked_access_denied.html: expected {expected_reason} "
            f"(from {expected_path.name}), got {result}"
        )
    else:
        print(f"PASS: blocked_access_denied.html -> {result}")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(failure)
        sys.exit(1)

    print("\nAll Naukri block-detection tests passed.")


if __name__ == "__main__":
    main()
