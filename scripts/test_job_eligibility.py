#!/usr/bin/env python3

"""
Tests for scripts/job_eligibility.py -- the shared candidate-job
eligibility gate combining experience_eligibility and location_taxonomy.

Fully isolated: pure in-memory computation, no DB, no network.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from job_eligibility import EligibilityResult, assess_job_eligibility
from experience_eligibility import Eligibility as ExperienceEligibility
from location_taxonomy import LocationEligibility


CANDIDATE_11_BENGALURU = {
    "candidate": {"experience_years": 11},
    "target_locations": ["Bengaluru"],
}

CANDIDATE_3_MUMBAI = {
    "candidate": {"experience_years": 3},
    "target_locations": ["Mumbai"],
}


def _job(experience_required, location):
    return {
        "title": "Senior Site Reliability Engineer",
        "jd_text": "",
        "experience_required": experience_required,
        "location": location,
        "work_model": "",
    }


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def test_a1_eligible_experience_and_location():
    failures = []
    job = _job("10-15 years", "Bengaluru")
    result = assess_job_eligibility(job, CANDIDATE_11_BENGALURU)

    if not result.eligible:
        _fail(failures, f"A1: expected eligible=True, got {result.eligible} ({result.reason_code})")
    elif result.reason_code != "ELIGIBLE":
        _fail(failures, f"A1: expected reason_code=ELIGIBLE, got {result.reason_code}")
    elif result.experience_assessment.eligibility != ExperienceEligibility.MATCH:
        _fail(failures, f"A1: expected experience MATCH, got {result.experience_assessment.eligibility}")
    elif result.location_assessment.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"A1: expected location MATCH, got {result.location_assessment.eligibility}")
    else:
        print("PASS: A1 -> eligible experience + eligible location -> eligible=True, ELIGIBLE")
    return failures


def test_a2_below_profile_experience():
    failures = []
    job = _job("1-3 years", "Bengaluru")
    result = assess_job_eligibility(job, CANDIDATE_11_BENGALURU)

    if result.eligible:
        _fail(failures, f"A2: expected eligible=False, got True")
    elif result.reason_code != "EXPERIENCE_BELOW_PROFILE":
        _fail(failures, f"A2: expected reason_code=EXPERIENCE_BELOW_PROFILE, got {result.reason_code}")
    elif result.experience_assessment.eligibility != ExperienceEligibility.BELOW_PROFILE:
        _fail(failures, f"A2: expected experience BELOW_PROFILE, got {result.experience_assessment.eligibility}")
    elif result.location_assessment.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"A2: expected location still computed as MATCH, got {result.location_assessment.eligibility}")
    else:
        print("PASS: A2 -> BELOW_PROFILE experience -> ineligible, EXPERIENCE_BELOW_PROFILE (location still computed)")
    return failures


def test_a3_no_match_location():
    failures = []
    job = _job("10-15 years", "Dubai")
    result = assess_job_eligibility(job, CANDIDATE_11_BENGALURU)

    if result.eligible:
        _fail(failures, "A3: expected eligible=False, got True")
    elif result.reason_code != "LOCATION_NO_MATCH":
        _fail(failures, f"A3: expected reason_code=LOCATION_NO_MATCH, got {result.reason_code}")
    elif result.location_assessment.eligibility != LocationEligibility.NO_MATCH:
        _fail(failures, f"A3: expected location NO_MATCH, got {result.location_assessment.eligibility}")
    elif result.experience_assessment.eligibility != ExperienceEligibility.MATCH:
        _fail(failures, f"A3: expected experience still computed as MATCH, got {result.experience_assessment.eligibility}")
    else:
        print("PASS: A3 -> NO_MATCH location -> ineligible, LOCATION_NO_MATCH (experience still computed)")
    return failures


def test_a4_unknown_experience_stays_eligible():
    failures = []
    job = _job("", "Bengaluru")
    result = assess_job_eligibility(job, CANDIDATE_11_BENGALURU)

    if not result.eligible:
        _fail(failures, f"A4: expected UNKNOWN experience to remain eligible, got eligible=False ({result.reason_code})")
    elif result.experience_assessment.eligibility != ExperienceEligibility.UNKNOWN:
        _fail(failures, f"A4: expected experience UNKNOWN, got {result.experience_assessment.eligibility}")
    else:
        print("PASS: A4 -> UNKNOWN experience -> still eligible (never a rejection on its own)")
    return failures


def test_a5_unknown_location_stays_eligible():
    failures = []
    job = _job("10-15 years", "")
    result = assess_job_eligibility(job, CANDIDATE_11_BENGALURU)

    if not result.eligible:
        _fail(failures, f"A5: expected UNKNOWN location to remain eligible, got eligible=False ({result.reason_code})")
    elif result.location_assessment.eligibility != LocationEligibility.UNKNOWN:
        _fail(failures, f"A5: expected location UNKNOWN, got {result.location_assessment.eligibility}")
    else:
        print("PASS: A5 -> UNKNOWN location -> still eligible (never a rejection on its own)")
    return failures


def test_a6_both_experience_and_location_ineligible():
    failures = []
    job = _job("1-3 years", "Dubai")
    result = assess_job_eligibility(job, CANDIDATE_11_BENGALURU)

    if result.eligible:
        _fail(failures, "A6: expected eligible=False, got True")
    elif result.reason_code != "EXPERIENCE_AND_LOCATION_INELIGIBLE":
        _fail(failures, f"A6: expected reason_code=EXPERIENCE_AND_LOCATION_INELIGIBLE, got {result.reason_code}")
    elif result.experience_assessment.eligibility != ExperienceEligibility.BELOW_PROFILE:
        _fail(failures, f"A6: expected experience BELOW_PROFILE, got {result.experience_assessment.eligibility}")
    elif result.location_assessment.eligibility != LocationEligibility.NO_MATCH:
        _fail(failures, f"A6: expected location NO_MATCH, got {result.location_assessment.eligibility}")
    elif result.experience_assessment.reason not in result.reason or result.location_assessment.reason not in result.reason:
        _fail(failures, f"A6: expected combined reason text to mention both failures, got {result.reason!r}")
    else:
        print("PASS: A6 -> both experience AND location ineligible -> combined reason mentions both")
    return failures


def test_a7_candidate_specific_profile_behavior():
    """
    The same job must be assessed differently for two materially
    different candidate profiles -- proving the shared gate is
    candidate-specific, not hardcoded to any one profile.
    """
    failures = []
    job = _job("10-15 years", "Bengaluru")

    result_senior_bengaluru = assess_job_eligibility(job, CANDIDATE_11_BENGALURU)
    result_junior_mumbai = assess_job_eligibility(job, CANDIDATE_3_MUMBAI)

    if result_senior_bengaluru.eligible != True:
        _fail(failures, f"A7: expected candidate 1 eligible=True, got {result_senior_bengaluru.eligible}")
    if result_junior_mumbai.eligible != False:
        _fail(failures, f"A7: expected candidate 2 eligible=False, got {result_junior_mumbai.eligible}")
    if result_senior_bengaluru.reason_code == result_junior_mumbai.reason_code:
        _fail(
            failures,
            f"A7: expected different reason codes for two materially "
            f"different candidates on the same job, both got "
            f"{result_senior_bengaluru.reason_code}",
        )

    if not failures:
        print(
            f"PASS: A7 -> same job, candidate 1 (11yr, Bengaluru) -> "
            f"{result_senior_bengaluru.reason_code}, candidate 2 (3yr, "
            f"Mumbai) -> {result_junior_mumbai.reason_code}"
        )

    return failures


def test_a8_remote_matrix_behavior_unchanged():
    """
    Re-confirms (through the shared gate, not location_taxonomy
    directly) that the validated remote-scope matrix from the location
    component still behaves identically when reached via
    assess_job_eligibility().
    """
    failures = []

    cases = [
        (["Remote"], "Remote India", True, "ELIGIBLE"),
        (["Remote"], "Remote Global", False, "LOCATION_NO_MATCH"),
        (["Remote India"], "Remote Global", True, "ELIGIBLE"),  # UNKNOWN location -> still eligible
        (["Remote Global"], "Remote Global", True, "ELIGIBLE"),
    ]

    for target_locations, job_location, expected_eligible, expected_reason_code in cases:
        profile = {"candidate": {"experience_years": 11}, "target_locations": target_locations}
        job = _job("10-15 years", job_location)
        result = assess_job_eligibility(job, profile)

        if result.eligible != expected_eligible or result.reason_code != expected_reason_code:
            _fail(
                failures,
                f"A8: candidate {target_locations} + job {job_location!r} "
                f"expected eligible={expected_eligible}, "
                f"reason_code={expected_reason_code}, got "
                f"eligible={result.eligible}, reason_code={result.reason_code} "
                f"(location eligibility={result.location_assessment.eligibility})",
            )

    if not failures:
        print("PASS: A8 -> remote-scope matrix behavior unchanged when reached through the shared eligibility gate")

    return failures


def test_a9_result_is_deterministic():
    failures = []
    job = _job("8-10 years", "Bengaluru")

    results = [assess_job_eligibility(job, CANDIDATE_11_BENGALURU) for _ in range(5)]
    reason_codes = {r.reason_code for r in results}
    eligibilities = {r.eligible for r in results}

    if len(reason_codes) != 1 or len(eligibilities) != 1:
        _fail(
            failures,
            f"A9: expected deterministic output across repeated calls "
            f"with identical input, got reason_codes={reason_codes} "
            f"eligibilities={eligibilities}",
        )
    else:
        print("PASS: A9 -> assess_job_eligibility() is deterministic for identical inputs")

    return failures


def test_a10_no_default_candidate_profile():
    failures = []
    job = _job("10-15 years", "Bengaluru")

    try:
        assess_job_eligibility(job)
    except TypeError:
        print("PASS: A10 -> assess_job_eligibility() requires an explicit candidate_profile (no silent default)")
    else:
        _fail(failures, "A10: expected TypeError when candidate_profile is omitted, but call succeeded")

    return failures


def main():
    tests = [
        test_a1_eligible_experience_and_location,
        test_a2_below_profile_experience,
        test_a3_no_match_location,
        test_a4_unknown_experience_stays_eligible,
        test_a5_unknown_location_stays_eligible,
        test_a6_both_experience_and_location_ineligible,
        test_a7_candidate_specific_profile_behavior,
        test_a8_remote_matrix_behavior_unchanged,
        test_a9_result_is_deterministic,
        test_a10_no_default_candidate_profile,
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

    print(f"All {len(tests)} shared job-eligibility unit tests passed.")


if __name__ == "__main__":
    main()
