#!/usr/bin/env python3

"""
Regression tests for the Phase 6 Step 10 job_url remediation
(data/reports/hirist_job_url_remediation.md): _parse_job_entry()'s
flat-name path now reads a top-level ListItem.url field, the exact
shape Phase 6 Step 9's real, untruncated live capture
(data/reports/hirist_step9_raw_capture.html) confirmed every one of
20 real entries actually has. The nested item.url path (schema.org's
standards-compliant shape, never observed in real data) is unchanged.
Company/title parsing (the flat "name" split) is NOT touched by this
remediation -- confirmed unchanged by test F below.

Covers the 7 scenarios required for Phase 6 Step 10 (A-G):
  A. real observed ListItem with a top-level url -> job_url matches
     exactly (uses the VERBATIM real entry from Step 9's capture)
  B. ListItem without url -> job_url remains empty
  C. existing nested item.url behavior continues working, including
     when a top-level url is ALSO present alongside a nested item
     (nested path must still take priority, unaffected)
  D. invalid (malformed) URL rejected; well-formed-but-off-domain URL
     accepted -- confirmed consistent with the PRE-EXISTING nested-path
     behavior (no domain restriction was ever applied to job_url; only
     pagination next_url has one) -- this remediation does not change
     that validation style, for either path
  E. existing valid Hirist fixtures (valid_results_page1/2.html)
     remain valid, unmodified
  F. company/title split output is byte-identical before/after this
     remediation -- explicitly re-proves the Step 9 "wrong split"
     example still reproduces exactly, i.e. remediation only added
     job_url, nothing else changed
  G. no Naukri regressions -- verified by the full standalone suite
     run reported in data/reports/hirist_job_url_remediation.md, not
     duplicated here (naukri_parser.py was never modified)

Makes ZERO network requests -- pure, offline, in-memory dicts and
existing static fixture files only.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "data" / "fixtures" / "hirist"
sys.path.insert(0, str(ROOT / "scripts"))

from hirist_parser import _parse_job_entry, classify_search_page, HiristPageState


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _read_fixture(name):
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_A_real_step9_entry_top_level_url_extracted(failures):
    # Verbatim entry #1 from the real Phase 6 Step 9 capture
    # (data/reports/hirist_step9_raw_capture.html /
    # hirist_raw_payload_capture.md, Section C entry 1) -- not
    # synthetic, not invented.
    entry = {
        "@type": "ListItem",
        "position": 1,
        "name": "Verint - Senior DevOps Engineer",
        "url": "https://www.hirist.tech/j/verint-senior-dev-ops-engineer-1672606",
    }
    job = _parse_job_entry(entry)

    if job is None:
        return _fail(failures, "A: expected the real Step 9 entry to parse successfully, got None")
    if job["job_url"] != "https://www.hirist.tech/j/verint-senior-dev-ops-engineer-1672606":
        return _fail(failures, f"A: expected job_url to exactly equal the real captured URL, got {job['job_url']!r}")
    if job["company"] != "Verint" or job["title"] != "Senior DevOps Engineer":
        return _fail(failures, f"A: company/title regressed: {job['company']!r} / {job['title']!r}")

    print("PASS: A -> real Step 9 ListItem with a top-level url is now correctly extracted into job_url")


def test_B_entry_without_url_stays_empty(failures):
    entry = {"@type": "ListItem", "position": 1, "name": "Acme Corp - Staff SRE"}
    job = _parse_job_entry(entry)

    if job is None:
        return _fail(failures, "B: expected this entry to parse, got None")
    if job["job_url"] != "":
        return _fail(failures, f"B: expected job_url == '' when no url field is present, got {job['job_url']!r}")

    print("PASS: B -> a ListItem with no url field leaves job_url empty (never guessed/constructed)")


def test_C_nested_item_url_still_works_and_takes_priority(failures):
    # Baseline: nested path alone, matching the existing fixture's
    # own nested entry exactly.
    nested_only = {
        "@type": "ListItem",
        "position": 2,
        "item": {
            "@type": "JobPosting",
            "title": "Lead Platform Engineer",
            "hiringOrganization": {"@type": "Organization", "name": "Example Systems Pvt Ltd"},
            "url": "https://www.hirist.tech/job-detail/lead-platform-engineer-12345",
        },
    }
    job = _parse_job_entry(nested_only)
    if job is None or job["job_url"] != "https://www.hirist.tech/job-detail/lead-platform-engineer-12345":
        return _fail(failures, f"C: nested item.url extraction regressed: {job}")

    # Adversarial: BOTH a nested item AND a top-level url on the SAME
    # ListItem, with two deliberately DIFFERENT URLs -- the nested
    # path must still win entirely (the flat-path code, including the
    # new top-level url read, must never even run), proving the two
    # extraction paths remain fully independent after this change.
    both_present = {
        "@type": "ListItem",
        "position": 3,
        "url": "https://www.hirist.tech/j/top-level-should-be-ignored-999",
        "item": {
            "@type": "JobPosting",
            "title": "Staff Engineer",
            "hiringOrganization": {"@type": "Organization", "name": "Acme Corp"},
            "url": "https://www.hirist.tech/job-detail/staff-engineer-nested-111",
        },
    }
    job2 = _parse_job_entry(both_present)
    if job2 is None:
        return _fail(failures, "C: expected the mixed entry to parse, got None")
    if job2["job_url"] != "https://www.hirist.tech/job-detail/staff-engineer-nested-111":
        return _fail(
            failures,
            f"C: nested item.url must take priority over a sibling top-level url, got {job2['job_url']!r}",
        )

    print("PASS: C -> nested item.url extraction is unaffected, and correctly takes priority when both a nested item and a top-level url are present")


def test_D_invalid_and_off_domain_url_handling(failures):
    # D1: malformed URL (no scheme/netloc) -- must be rejected by the
    # existing _sane_url_or_none() validator, same as it always was.
    malformed = {"@type": "ListItem", "position": 1, "name": "Acme Corp - Staff SRE", "url": "not-a-valid-url"}
    job = _parse_job_entry(malformed)
    if job is None or job["job_url"] != "":
        return _fail(failures, f"D1: expected a malformed url to be rejected (job_url == ''), got {job}")

    # D2: well-formed but off-domain URL. The PRE-EXISTING nested-item
    # path has never applied a domain restriction to job_url (only
    # pagination next_url has one, via _ALLOWED_NEXT_URL_HOST) -- this
    # remediation reuses that exact same _sane_url_or_none() validator
    # for the flat path, so behavior must be IDENTICAL for both paths:
    # a well-formed off-domain URL is accepted, not silently treated
    # as a new leniency introduced here.
    off_domain_flat = {
        "@type": "ListItem",
        "position": 1,
        "name": "Acme Corp - Staff SRE",
        "url": "https://evil-example.com/job/123",
    }
    off_domain_nested = {
        "@type": "ListItem",
        "position": 2,
        "item": {
            "title": "Staff Engineer",
            "hiringOrganization": {"name": "Acme Corp"},
            "url": "https://evil-example.com/job/456",
        },
    }
    job_flat = _parse_job_entry(off_domain_flat)
    job_nested = _parse_job_entry(off_domain_nested)
    if job_flat is None or job_flat["job_url"] != "https://evil-example.com/job/123":
        return _fail(
            failures,
            f"D2: expected the flat path to accept a well-formed off-domain URL (matching pre-existing "
            f"nested-path behavior, no new domain restriction), got {job_flat}",
        )
    if job_nested is None or job_nested["job_url"] != "https://evil-example.com/job/456":
        return _fail(failures, f"D2: nested path's own (pre-existing, unchanged) off-domain acceptance regressed: {job_nested}")

    print("PASS: D -> malformed URLs are rejected (job_url stays empty); well-formed off-domain URLs are accepted, identically on both paths, matching pre-existing (unchanged) validation style -- no domain allowlist has ever applied to job_url, only to pagination next_url")


def test_E_existing_valid_fixtures_remain_valid(failures):
    for fixture_name, expected_job_count in [("valid_results_page1.html", 2), ("valid_results_page2.html", 1)]:
        classification = classify_search_page(_read_fixture(fixture_name), "https://x/valid")
        if classification.state != HiristPageState.VALID_RESULTS:
            return _fail(failures, f"E: expected VALID_RESULTS for {fixture_name}, got {classification.state}")
        if len(classification.jobs) != expected_job_count:
            return _fail(
                failures,
                f"E: expected {expected_job_count} job(s) from {fixture_name}, got {len(classification.jobs)}",
            )

    # The existing fixture's flat entry ("Verint - Senior DevOps
    # Engineer") has NO url field -- job_url must still be empty,
    # proving this remediation does not retroactively invent a URL
    # for fixtures that never had one.
    classification = classify_search_page(_read_fixture("valid_results_page1.html"), "https://x/valid")
    flat_job = next(j for j in classification.jobs if j["company"] == "Verint")
    if flat_job["job_url"] != "":
        return _fail(failures, f"E: expected the existing fixture's flat entry (no url field) to keep job_url == '', got {flat_job['job_url']!r}")

    print("PASS: E -> existing valid Hirist fixtures (page1, page2) remain valid and unaffected, including the flat entry that has no url field")


def test_F_company_title_split_unchanged(failures):
    # Re-proves the exact Step 9 "wrong split" example still
    # reproduces byte-identically -- confirming this remediation
    # touched ONLY job_url extraction, not the name-splitting logic.
    cases = [
        ("Verint - Senior DevOps Engineer", "Verint", "Senior DevOps Engineer"),
        ("Senior DevOps Engineer - AWS & Kubernetes", "Senior DevOps Engineer", "AWS & Kubernetes"),
        ("Vedantu Innovations - Senior DevOps & SecOps Engineer", "Vedantu Innovations", "Senior DevOps & SecOps Engineer"),
    ]
    for name, expected_company, expected_title in cases:
        job = _parse_job_entry({"@type": "ListItem", "position": 1, "name": name})
        if job is None or job["company"] != expected_company or job["title"] != expected_title:
            return _fail(
                failures,
                f"F: company/title split regressed for {name!r}: got {job}",
            )

    # Multi-delimiter entries (3+ segments) must still be skipped
    # entirely -- fails closed, unchanged.
    multi = _parse_job_entry({
        "@type": "ListItem", "position": 1,
        "name": "EXL - Senior DevOps Engineer - Cloud Architecture & Infrastructure",
        "url": "https://www.hirist.tech/j/exl-senior-dev-ops-engineer-cloud-architecture-and-infrastructure-1668805",
    })
    if multi is not None:
        return _fail(failures, f"F: expected a 3-segment name to still be skipped (fails closed), got {multi}")

    print("PASS: F -> company/title split behavior (including the fails-closed multi-delimiter skip) is byte-identical to pre-remediation behavior; only job_url extraction changed")


def main():
    print("HIRIST JOB_URL REMEDIATION REGRESSION TESTS (Phase 6 Step 10)")
    print("=========================================================================")

    failures = []

    test_A_real_step9_entry_top_level_url_extracted(failures)
    test_B_entry_without_url_stays_empty(failures)
    test_C_nested_item_url_still_works_and_takes_priority(failures)
    test_D_invalid_and_off_domain_url_handling(failures)
    test_E_existing_valid_fixtures_remain_valid(failures)
    test_F_company_title_split_unchanged(failures)

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  {failure}")
        sys.exit(1)

    print("\nAll Step 10 job_url remediation regression tests passed.")
    print("(Naukri regression -- test G -- verified separately by the full standalone suite, not duplicated here.)")


if __name__ == "__main__":
    main()
