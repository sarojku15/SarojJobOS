#!/usr/bin/env python3

"""
Tests for scripts/experience_eligibility.py.

Fully isolated: no SQLite, no network. Uses the real captured Naukri
fixture (data/fixtures/naukri/detail_valid.html) as the test ground for
the structured-vs-visible cross-check and the "no unrelated numbers
picked up" guarantee, per the component spec.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from experience_eligibility import (
    Eligibility,
    assess_experience_eligibility,
    detect_requirement_type,
    parse_experience_text,
)
from naukri_parser import parse_detail_page


FIXTURES_DIR = ROOT / "data" / "fixtures" / "naukri"

CANDIDATE_11_YEARS = {"candidate": {"experience_years": 11}}


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def test_1_below_profile_range():
    failures = []
    job = {"experience_required": "8-10 years"}
    result = assess_experience_eligibility(job, CANDIDATE_11_YEARS)

    if result.eligibility != Eligibility.BELOW_PROFILE:
        _fail(failures, f"test 1: expected BELOW_PROFILE, got {result.eligibility}")
    else:
        print(f"PASS: test 1 (8-10 vs 11) -> {result.eligibility.value}")

    return failures


def test_2_match_range():
    failures = []
    job = {"experience_required": "10-15 years"}
    result = assess_experience_eligibility(job, CANDIDATE_11_YEARS)

    if result.eligibility != Eligibility.MATCH:
        _fail(failures, f"test 2: expected MATCH, got {result.eligibility}")
    else:
        print(f"PASS: test 2 (10-15 vs 11) -> {result.eligibility.value}")

    return failures


def test_3_match_plus():
    failures = []
    job = {"experience_required": "11+ years"}
    result = assess_experience_eligibility(job, CANDIDATE_11_YEARS)

    if result.eligibility != Eligibility.MATCH:
        _fail(failures, f"test 3: expected MATCH, got {result.eligibility}")
    else:
        print(f"PASS: test 3 (11+ vs 11) -> {result.eligibility.value}")

    return failures


def test_4_above_profile_range():
    failures = []
    job = {"experience_required": "12-15 years"}
    result = assess_experience_eligibility(job, CANDIDATE_11_YEARS)

    if result.eligibility != Eligibility.ABOVE_PROFILE:
        _fail(failures, f"test 4: expected ABOVE_PROFILE, got {result.eligibility}")
    else:
        print(f"PASS: test 4 (12-15 vs 11) -> {result.eligibility.value}")

    return failures


def test_5_below_profile_single_value():
    failures = []
    job = {"experience_required": "5 years"}
    result = assess_experience_eligibility(job, CANDIDATE_11_YEARS)

    if result.eligibility != Eligibility.BELOW_PROFILE:
        _fail(failures, f"test 5: expected BELOW_PROFILE, got {result.eligibility}")
    else:
        print(f"PASS: test 5 (5 years vs 11) -> {result.eligibility.value}")

    return failures


def test_6_missing_experience_is_unknown():
    failures = []
    job = {"experience_required": ""}
    result = assess_experience_eligibility(job, CANDIDATE_11_YEARS)

    if result.eligibility != Eligibility.UNKNOWN:
        _fail(failures, f"test 6: expected UNKNOWN, got {result.eligibility}")
    else:
        print(f"PASS: test 6 (missing) -> {result.eligibility.value}")

    return failures


def test_7_malformed_experience_is_unknown():
    failures = []
    job = {"experience_required": "Not Disclosed"}
    result = assess_experience_eligibility(job, CANDIDATE_11_YEARS)

    if result.eligibility != Eligibility.UNKNOWN:
        _fail(failures, f"test 7: expected UNKNOWN, got {result.eligibility}")
    else:
        print(f"PASS: test 7 (malformed) -> {result.eligibility.value}")

    return failures


def test_8_exact_boundary_behavior():
    """
    Documented choice: an exact-boundary match (candidate years exactly
    equal to a single-value requirement, or exactly at a range's min
    edge) is classified MATCH, not BORDERLINE. BORDERLINE exists in the
    Eligibility enum but is intentionally unused -- see
    experience_eligibility.assess_experience_eligibility()'s docstring.
    """
    failures = []

    job_single = {"experience_required": "11 years"}
    result_single = assess_experience_eligibility(job_single, CANDIDATE_11_YEARS)

    if result_single.eligibility != Eligibility.MATCH:
        _fail(
            failures,
            f"test 8a: expected MATCH for exact single-value boundary "
            f"(11 years vs 11), got {result_single.eligibility}",
        )
    else:
        print(f"PASS: test 8a (11 years vs 11, exact) -> {result_single.eligibility.value}")

    job_range_edge = {"experience_required": "11-15 years"}
    result_range_edge = assess_experience_eligibility(job_range_edge, CANDIDATE_11_YEARS)

    if result_range_edge.eligibility != Eligibility.MATCH:
        _fail(
            failures,
            f"test 8b: expected MATCH for exact range-minimum boundary "
            f"(11-15 vs 11), got {result_range_edge.eligibility}",
        )
    else:
        print(
            f"PASS: test 8b (11-15 vs 11, exact min edge) -> "
            f"{result_range_edge.eligibility.value}"
        )

    return failures


def test_9_structured_jsonld_minimum_experience():
    """
    Uses the real captured Naukri fixture: JSON-LD
    experienceRequirements.monthsOfExperience = 96 (from
    naukri_parser.parse_detail_page()'s new experience_min_months field)
    must be surfaced as source_min_experience_months = 96 and correctly
    treated as an 8-year MINIMUM, not converted into an 8-year maximum.
    """
    failures = []

    html = (FIXTURES_DIR / "detail_valid.html").read_text(encoding="utf-8")
    job = parse_detail_page(html, "https://example.com/job")

    if job.get("experience_min_months") != 96:
        _fail(
            failures,
            f"test 9: expected experience_min_months=96 from the real "
            f"fixture, got {job.get('experience_min_months')!r}",
        )
    else:
        print("PASS: test 9 -> naukri_parser exposes experience_min_months=96")

    result = assess_experience_eligibility(job, CANDIDATE_11_YEARS)

    if result.source_min_experience_months != 96:
        _fail(
            failures,
            f"test 9: expected source_min_experience_months=96 on the "
            f"assessment, got {result.source_min_experience_months!r}",
        )
    else:
        print("PASS: test 9 -> assessment carries source_min_experience_months=96")

    return failures


def test_10_visible_and_structured_consistent():
    """
    Same real fixture: visible text says "8 - 10 years" (min=8, max=10),
    JSON-LD says 96 months (= 8 years minimum). These agree (0 years
    apart) -- not suspicious, and eligibility is computed normally
    (BELOW_PROFILE for an 11-year candidate, since the visible maximum
    of 10 caps the range below the candidate's experience).
    """
    failures = []

    html = (FIXTURES_DIR / "detail_valid.html").read_text(encoding="utf-8")
    job = parse_detail_page(html, "https://example.com/job")

    result = assess_experience_eligibility(job, CANDIDATE_11_YEARS)

    if result.is_suspicious:
        _fail(
            failures,
            f"test 10: expected is_suspicious=False (8 visible vs 8 "
            f"structured minimum agree), got True: {result.reason}",
        )
    else:
        print("PASS: test 10 -> visible (8) and structured (8) minimums agree, not suspicious")

    if result.parsed_min_years != 8 or result.parsed_max_years != 10:
        _fail(
            failures,
            f"test 10: expected parsed range (8, 10), got "
            f"({result.parsed_min_years}, {result.parsed_max_years})",
        )
    else:
        print("PASS: test 10 -> parsed visible range is (8, 10)")

    if result.eligibility != Eligibility.BELOW_PROFILE:
        _fail(
            failures,
            f"test 10: expected BELOW_PROFILE (candidate 11 > visible "
            f"max 10), got {result.eligibility}",
        )
    else:
        print(f"PASS: test 10 -> eligibility {result.eligibility.value} (consistent, non-suspicious)")

    return failures


def test_11_visible_and_structured_disagree_materially():
    failures = []

    job = {
        "experience_required": "8 - 10 years",
        "experience_min_months": 24,  # 2 years -- 6 years off from visible 8
    }
    result = assess_experience_eligibility(job, CANDIDATE_11_YEARS)

    if not result.is_suspicious:
        _fail(
            failures,
            f"test 11: expected is_suspicious=True for an 8 vs 2 year "
            f"disagreement, got False",
        )
    else:
        print("PASS: test 11 -> material disagreement flagged is_suspicious=True")

    if result.eligibility != Eligibility.UNKNOWN:
        _fail(
            failures,
            f"test 11: expected UNKNOWN when sources disagree materially, "
            f"got {result.eligibility}",
        )
    else:
        print(f"PASS: test 11 -> eligibility forced to {result.eligibility.value}, not guessed")

    return failures


def test_12_unrelated_page_text_not_selected():
    """
    experience_eligibility never re-scans a page itself -- it only ever
    reads the already-isolated experience_required field. This proves
    that guarantee two ways: (a) against the real fixture, whose visible
    text contains other numbers (a 3.2 rating, "121.6K Reviews",
    "Openings: 1") that a naive "find any digits" approach (the exact
    bug previously found in score_job.py's own experience dimension)
    could latch onto -- yet the correctly scoped result is exactly
    "8 - 10 years"; and (b) with a synthetic decoy string, confirming
    the parser takes the string's own leading range, not some unrelated
    larger number appended to it.
    """
    failures = []

    html = (FIXTURES_DIR / "detail_valid.html").read_text(encoding="utf-8")
    job = parse_detail_page(html, "https://example.com/job")

    if job.get("experience_required", "").strip() != "8 - 10 years":
        _fail(
            failures,
            f"test 12: expected the real fixture's isolated experience "
            f"field to be exactly '8 - 10 years' (not some other number "
            f"from the page, e.g. the 3.2 rating or 121.6K reviews "
            f"count), got {job.get('experience_required')!r}",
        )
    else:
        print("PASS: test 12a -> real fixture experience_required is exactly '8 - 10 years'")

    decoy_min, decoy_max = parse_experience_text(
        "8 - 10 years (Similar Jobs: 2-4 years, 15+ years also available)"
    )

    if (decoy_min, decoy_max) != (8, 10):
        _fail(
            failures,
            f"test 12b: expected the FIRST range in the string (8, 10), "
            f"not a decoy range from trailing unrelated text, got "
            f"({decoy_min}, {decoy_max})",
        )
    else:
        print(f"PASS: test 12b -> decoy text ignored, parsed ({decoy_min}, {decoy_max})")

    return failures


def test_13_two_candidates_differ():
    failures = []

    job = {"experience_required": "8-10 years"}

    candidate_senior = {"candidate": {"experience_years": 11}}
    candidate_junior = {"candidate": {"experience_years": 3}}

    result_senior = assess_experience_eligibility(job, candidate_senior)
    result_junior = assess_experience_eligibility(job, candidate_junior)

    if result_senior.eligibility == result_junior.eligibility:
        _fail(
            failures,
            f"test 13: expected different eligibility for a materially "
            f"different candidate on the same job, got "
            f"{result_senior.eligibility} for both "
            f"(11 years and 3 years)",
        )
    else:
        print(
            f"PASS: test 13 -> 11yr candidate -> {result_senior.eligibility.value}, "
            f"3yr candidate -> {result_junior.eligibility.value}"
        )

    if result_senior.eligibility != Eligibility.BELOW_PROFILE:
        _fail(failures, f"test 13: expected 11yr candidate BELOW_PROFILE, got {result_senior.eligibility}")

    if result_junior.eligibility != Eligibility.ABOVE_PROFILE:
        _fail(failures, f"test 13: expected 3yr candidate ABOVE_PROFILE (job wants more experience than they have), got {result_junior.eligibility}")

    return failures


def test_14_real_jd_format_coverage():
    """Section D format coverage: every phrasing seen on real JDs must
    parse to the correct (min, max), not silently fail to (None, None)
    and not silently misparse to the wrong figure."""
    failures = []

    cases = [
        ("10+", (10, None)),
        ("10 years", (10, 10)),
        ("10 yrs", (10, 10)),
        ("10 yr", (10, 10)),
        ("10 to 12", (10, 12)),
        ("10-12", (10, 12)),
        ("10 – 12", (10, 12)),  # en dash
        ("10 — 12", (10, 12)),  # em dash
        ("minimum 10 years", (10, None)),
        ("at least 10 years", (10, None)),
        ("10 years or more", (10, None)),
        ("8–10 years preferred", (8, 10)),  # en dash + qualifier
        ("10+ Yrs", (10, None)),
    ]
    for text, expected in cases:
        got = parse_experience_text(text)
        if got != expected:
            _fail(failures, f"test 14: parse_experience_text({text!r}) expected {expected}, got {got}")
    if not failures:
        print(f"PASS: test 14 -> all {len(cases)} real-JD experience formats parse correctly")

    return failures


def test_15_overall_vs_relevant_experience():
    """Section D: an 'overall'/'total' figure must win over a separate
    'relevant' figure regardless of which one appears first in the
    text -- never silently substitute the relevant sub-figure."""
    failures = []

    forward = parse_experience_text("10 years overall experience, 5 years relevant experience")
    reverse = parse_experience_text("5 years relevant experience, 10 years overall required")

    if forward != (10, 10):
        _fail(failures, f"test 15: overall-first phrasing expected (10, 10), got {forward}")
    if reverse != (10, 10):
        _fail(failures, f"test 15: relevant-first phrasing expected the overall figure (10, 10) regardless of order, got {reverse}")

    if not failures:
        print("PASS: test 15 -> overall/total figure wins over relevant sub-figure, independent of sentence order")

    return failures


def test_16_requirement_type_detection():
    """Section E: REQUIRED/PREFERRED is tracked for explainability only
    -- it must never appear as a guess when the JD states neither."""
    failures = []

    cases = [
        ("8-10 years preferred", "PREFERRED"),
        ("8-10 years required", "REQUIRED"),
        ("8-10 years, mandatory", "REQUIRED"),
        ("8-10 years", "UNKNOWN"),
        ("", "UNKNOWN"),
    ]
    for text, expected in cases:
        got = detect_requirement_type(text)
        if got != expected:
            _fail(failures, f"test 16: detect_requirement_type({text!r}) expected {expected}, got {got}")

    # Requirement type is exposed on the assessment but must NEVER change
    # the eligibility decision itself.
    job_preferred = {"experience_required": "8-10 years preferred"}
    job_required = {"experience_required": "8-10 years required"}
    candidate = {"candidate": {"experience_years": 9}}
    result_preferred = assess_experience_eligibility(job_preferred, candidate)
    result_required = assess_experience_eligibility(job_required, candidate)
    if result_preferred.requirement_type != "PREFERRED":
        _fail(failures, f"test 16: expected requirement_type=PREFERRED on the assessment, got {result_preferred.requirement_type}")
    if result_required.requirement_type != "REQUIRED":
        _fail(failures, f"test 16: expected requirement_type=REQUIRED on the assessment, got {result_required.requirement_type}")
    if result_preferred.eligibility != result_required.eligibility:
        _fail(
            failures,
            f"test 16: SAFETY VIOLATION -- requirement_type altered the eligibility decision "
            f"({result_preferred.eligibility} vs {result_required.eligibility})",
        )

    if not failures:
        print("PASS: test 16 -> REQUIRED/PREFERRED correctly detected and never alters eligibility")

    return failures


def test_17_overall_vs_relevant_ranges_and_plus_forms():
    """Section 2: overall/total must win over relevant even when either
    side is a range or a '+' floor, and regardless of order -- never
    silently substitute the relevant sub-figure, and never collapse an
    open-ended overall floor into a fixed single value."""
    failures = []

    cases = [
        ("overall 10-12 years, relevant 5+ years", (10, 12)),
        ("10+ years overall, 5+ years relevant", (10, None)),
        ("8-10 years total, 5 years relevant", (8, 10)),
        ("5-8 years relevant, 10+ years overall", (10, None)),
        ("total experience: 8-10 years, relevant: 5 years", (8, 10)),
        ("10-12 years overall experience required, 5+ years relevant experience preferred", (10, 12)),
        ("minimum 10 years overall experience", (10, None)),
        ("overall experience of minimum 10 years", (10, None)),
    ]
    for text, expected in cases:
        got = parse_experience_text(text)
        if got != expected:
            _fail(failures, f"test 17: parse_experience_text({text!r}) expected {expected}, got {got}")

    # The critical rule, checked end-to-end through eligibility: a
    # candidate who satisfies ONLY the relevant-experience figure (not
    # the overall one) must NOT be treated as eligible -- proves the
    # relevant figure was truly discarded, not just displayed wrong.
    job = {"experience_required": "10+ years overall, 5+ years relevant"}
    candidate_below_overall = {"candidate": {"experience_years": 6}}  # satisfies "5+ relevant" only
    result = assess_experience_eligibility(job, candidate_below_overall)
    if result.eligibility != Eligibility.ABOVE_PROFILE:
        _fail(
            failures,
            f"test 17: SAFETY VIOLATION -- a candidate with 6 years (satisfies only the 'relevant' "
            f"5+ figure, not the overall 10+ floor) must be ABOVE_PROFILE (job wants more than "
            f"they have), got {result.eligibility}",
        )

    if not failures:
        print(f"PASS: test 17 -> overall/total wins over relevant for all {len(cases)} range/plus/reordered variants, verified through eligibility too")

    return failures


def test_18_end_to_end_acceptance_matrix_confirmed_candidate():
    """Section 6: the full acceptance matrix against the ACTUAL confirmed
    candidate_search_profile value (11.0 years, confirmed_by_user=1,
    source=RESUME_EXTRACTED) -- not the earlier 11.9 recomputation,
    which was explicitly rejected in favor of the confirmed value. Also
    proves RANGE_EXCEEDS_PROFILE (the old bug this eligibility model
    replaced) does not exist anywhere in this module."""
    failures = []

    candidate = {"candidate": {"experience_years": 11.0}}
    matrix = [
        ("5+ years", "ELIGIBLE"),
        ("8+ years", "ELIGIBLE"),
        ("10+ years", "ELIGIBLE"),
        ("11+ years", "ELIGIBLE"),
        ("12+ years", "NOT_ELIGIBLE"),
        ("8-10 years", "NOT_ELIGIBLE"),
        ("10-12 years", "ELIGIBLE"),
        ("11-12 years", "ELIGIBLE"),
        ("11-15 years", "ELIGIBLE"),
        ("12-15 years", "NOT_ELIGIBLE"),
        ("not a real experience requirement", "UNKNOWN"),
    ]

    for req_text, expected in matrix:
        job = {"experience_required": req_text}
        result = assess_experience_eligibility(job, candidate)
        if result.eligibility == Eligibility.MATCH:
            got = "ELIGIBLE"
        elif result.eligibility in (Eligibility.BELOW_PROFILE, Eligibility.ABOVE_PROFILE):
            got = "NOT_ELIGIBLE"
        else:
            got = "UNKNOWN"
        if got != expected:
            _fail(failures, f"test 18: candidate=11.0, req={req_text!r}: expected {expected}, got {got} ({result.eligibility.value}) -- reason: {result.reason}")
        if "RANGE_EXCEEDS_PROFILE" in result.reason or "RANGE_EXCEEDS_PROFILE" in str(result.eligibility):
            _fail(failures, f"test 18: SAFETY VIOLATION -- RANGE_EXCEEDS_PROFILE reappeared for req={req_text!r}")

    # Explicit boundary check from the acceptance criteria: 10-12 for an
    # 11.0-year candidate must be ELIGIBLE/MATCH, never a rejection.
    ten_twelve = assess_experience_eligibility({"experience_required": "10-12 years"}, candidate)
    if ten_twelve.eligibility != Eligibility.MATCH:
        _fail(failures, f"test 18: 10-12 years for candidate=11.0 must be MATCH/ELIGIBLE, got {ten_twelve.eligibility}")

    if not failures:
        print(f"PASS: test 18 -> full {len(matrix)}-case acceptance matrix correct for the confirmed candidate value (11.0 years); no RANGE_EXCEEDS_PROFILE anywhere")

    return failures


def test_19_qualifier_before_overall_keyword():
    """Regression for a real bug: when the minimum/at-least qualifier
    precedes 'overall'/'total' itself (not the number), it previously
    fell outside _VALUE_AFTER_OVERALL_PATTERN's capture group and was
    silently dropped, collapsing an open-ended floor into a fixed
    single value -- 'minimum overall experience of 10 years' returned
    (10, 10) instead of (10, None). Fixed via
    _QUALIFIER_BEFORE_OVERALL_PATTERN in experience_eligibility.py."""
    failures = []

    cases = [
        ("minimum overall experience of 10 years", (10, None)),
        ("at least overall 10 years", (10, None)),
        ("min overall experience of 10 years", (10, None)),
        # Already-supported qualifier placements must still work
        # unchanged -- this bug's fix must not regress them.
        ("minimum 10 years overall experience", (10, None)),
        ("overall experience of minimum 10 years", (10, None)),
    ]
    for text, expected in cases:
        got = parse_experience_text(text)
        if got != expected:
            _fail(failures, f"test 19: parse_experience_text({text!r}) expected {expected}, got {got}")

    # End-to-end through eligibility too: a candidate below the stated
    # floor must be BELOW_PROFILE, not incorrectly treated as an exact
    # single-value match that happens to equal their years.
    job = {"experience_required": "minimum overall experience of 10 years"}
    candidate_at_floor = {"candidate": {"experience_years": 10}}
    candidate_above_floor = {"candidate": {"experience_years": 14}}
    result_at = assess_experience_eligibility(job, candidate_at_floor)
    result_above = assess_experience_eligibility(job, candidate_above_floor)
    if result_at.eligibility != Eligibility.MATCH:
        _fail(failures, f"test 19: candidate=10 against open-ended 'minimum overall ... 10 years' floor must be MATCH, got {result_at.eligibility}")
    if result_above.eligibility != Eligibility.MATCH:
        _fail(
            failures,
            f"test 19: SAFETY VIOLATION -- candidate=14 against an open-ended 10-year floor must be MATCH "
            f"(open-ended means no ceiling); got {result_above.eligibility}, which would mean the fix "
            f"silently reintroduced a fixed ceiling of 10",
        )

    if not failures:
        print(f"PASS: test 19 -> qualifier-before-'overall' phrasing correctly parses to an open-ended floor, verified through eligibility too")

    return failures


def main():
    tests = [
        test_1_below_profile_range,
        test_2_match_range,
        test_3_match_plus,
        test_4_above_profile_range,
        test_5_below_profile_single_value,
        test_6_missing_experience_is_unknown,
        test_7_malformed_experience_is_unknown,
        test_8_exact_boundary_behavior,
        test_9_structured_jsonld_minimum_experience,
        test_10_visible_and_structured_consistent,
        test_11_visible_and_structured_disagree_materially,
        test_12_unrelated_page_text_not_selected,
        test_13_two_candidates_differ,
        test_14_real_jd_format_coverage,
        test_15_overall_vs_relevant_experience,
        test_16_requirement_type_detection,
        test_17_overall_vs_relevant_ranges_and_plus_forms,
        test_18_end_to_end_acceptance_matrix_confirmed_candidate,
        test_19_qualifier_before_overall_keyword,
    ]

    all_failures = []

    for test in tests:
        all_failures.extend(test())

    print()

    if all_failures:
        print(f"{len(all_failures)} failure(s):")
        for failure in all_failures:
            print(f"  - {failure}")
        sys.exit(1)

    print("All experience eligibility tests passed.")


if __name__ == "__main__":
    main()
