#!/usr/bin/env python3

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from naukri_parser import parse_search_results

FIXTURES_DIR = ROOT / "data" / "fixtures" / "naukri"

EXPECTED_TCS_URL = (
    "https://www.naukri.com/job-listings-site-reliability-engineer-"
    "tata-consultancy-services-bengaluru-8-to-10-years-190626026419"
)

EXPECTED_TCS_TITLE = "Site Reliability Engineer"

# Deterministically confirmed by direct inspection of
# search_results_sre_bengaluru.html: 21 unique href values containing
# "job-listings" appear in real <a> anchor tags on this fixture.
EXPECTED_UNIQUE_COUNT = 21


def load_fixture(filename):
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


def main():
    failures = []

    print("NAUKRI SEARCH RESULTS PARSING TEST")
    print("===================================")

    html = load_fixture("search_results_sre_bengaluru.html")
    results = parse_search_results(html)

    print(f"Job listings parsed: {len(results)}")

    tcs_matches = [
        r for r in results
        if r.get("job_url") == EXPECTED_TCS_URL
    ]

    if not tcs_matches:
        failures.append(
            f"Expected job_url not found in parsed results: {EXPECTED_TCS_URL}"
        )
    else:
        entry = tcs_matches[0]

        if entry.get("title") != EXPECTED_TCS_TITLE:
            failures.append(
                "TCS listing found but title mismatch: expected "
                f"{EXPECTED_TCS_TITLE!r}, got {entry.get('title')!r}"
            )
        else:
            print(
                f"PASS: found confirmed TCS listing -> "
                f"{entry['title']} | {entry['job_url']}"
            )

    if len(results) != EXPECTED_UNIQUE_COUNT:
        failures.append(
            f"Expected {EXPECTED_UNIQUE_COUNT} unique job-listing entries, "
            f"got {len(results)}"
        )
    else:
        print(f"PASS: unique job-listing count == {EXPECTED_UNIQUE_COUNT}")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(failure)
        sys.exit(1)

    print("\nAll Naukri search-parsing tests passed.")


if __name__ == "__main__":
    main()
