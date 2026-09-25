#!/usr/bin/env python3

"""
Regression test: candidate_experience is a SINGLE authoritative value
(professional_summary.total_experience_years) that is never silently
overwritten by a derived employment-duration calculation.

Two independent guarantees, each tested directly against the real
function (no HTTP layer needed for either):

1. scripts/resume_extractor.py's own "NEVER-INVENT DISCIPLINE" docstring
   already states it deliberately never sums employment_history date
   ranges into total_experience_years (overlapping roles, gaps, and
   omitted early-career roles make that arithmetic unreliable) -- it
   only reads an explicit "N years of experience" phrase from the
   resume's own summary text. This test proves that holds even when a
   resume's employment_history span (here: ~20 years) sharply disagrees
   with its own stated summary figure (11 years) -- the explicit phrase
   wins, employment dates are never summed in.

2. api/profile_store.py's apply_profile_update() updates
   total_experience_years and employment_history as two completely
   independent fields -- updating employment_history alone must never
   change total_experience_years, and vice versa.

Never opens data/applications/jobos.db. Never makes a network call.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "api"))

from resume_extractor import parse_resume_text
from candidate_profile import normalize_candidate_profile, promote_to_confirmed

import profile_store
from schemas import ProfileUpdate, EmploymentEntryIn


# Employment history spans 2004-11-01 to Present (~20 years), but the
# summary explicitly states "11+ years" -- a deliberately large,
# unmissable disagreement so this test would fail loudly if the
# extractor ever started summing dates instead of reading the phrase.
_RESUME_TEXT = """JORDAN TAYLOR
Senior Infrastructure Engineer
jordan.taylor@example.com | (555) 123-4567

PROFESSIONAL SUMMARY
Infrastructure engineer with 11+ years of experience building resilient
cloud platforms.

TECHNICAL SKILLS
Cloud Platforms: AWS, Azure

PROFESSIONAL EXPERIENCE
Senior Infrastructure Engineer Jan 2015 - Present
Example Corp
- Owns production infrastructure for a multi-region platform.

Infrastructure Engineer Nov 2004 - Dec 2014
Legacy Systems Inc
- Maintained on-prem data center infrastructure.

EDUCATION
Bachelor of Science - Computer Science
Sample University | 2004
"""


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def test_1_resume_extraction_never_sums_employment_dates():
    failures = []

    draft = parse_resume_text(_RESUME_TEXT, "jordan-taylor")

    naive_span_years = 2026 - 2004  # what a naive date-sum would produce (~22)
    stated_value = draft["professional_summary"]["total_experience_years"]

    if stated_value != 11.0:
        _fail(failures, f"test 1: expected the explicit '11+ years' phrase to win, got total_experience_years={stated_value}")
    elif abs(stated_value - naive_span_years) < 2:
        _fail(failures, f"test 1: extracted value ({stated_value}) suspiciously close to the naive employment-date span ({naive_span_years}) -- looks derived, not read from the phrase")
    else:
        print(f"PASS: test 1 -> total_experience_years=11.0 from the explicit phrase, NOT the ~{naive_span_years}-year employment_history span ({len(draft['employment_history'])} entries present but never summed)")

    # Sanity: employment_history WAS actually extracted (so this test
    # is proving non-substitution, not just an empty/skipped section).
    if len(draft["employment_history"]) < 2:
        _fail(failures, f"test 1: expected employment_history to actually be extracted (2 entries), got {draft['employment_history']}")
    else:
        print("PASS: test 1 -> employment_history was genuinely extracted (not skipped) alongside the correct summary-derived figure")

    # The same invariant must survive DRAFT -> CONFIRMED promotion --
    # promote_to_confirmed() must not recompute anything either.
    profile = normalize_candidate_profile(draft)
    confirmed = promote_to_confirmed(profile)
    if confirmed.professional_summary.total_experience_years != 11.0:
        _fail(failures, f"test 1: promote_to_confirmed() changed total_experience_years to {confirmed.professional_summary.total_experience_years}")
    else:
        print("PASS: test 1 -> total_experience_years unchanged (11.0) through DRAFT -> CONFIRMED promotion")

    return failures


def _base_confirmed_profile():
    draft = parse_resume_text(_RESUME_TEXT, "jordan-taylor")
    return promote_to_confirmed(normalize_candidate_profile(draft))


def test_2_employment_history_update_never_changes_experience():
    failures = []

    profile = _base_confirmed_profile()
    if profile.professional_summary.total_experience_years != 11.0:
        _fail(failures, f"test 2: sanity check failed, base profile total_experience_years={profile.professional_summary.total_experience_years}")
        return failures

    # Update ONLY employment_history, with a much longer span, and
    # explicitly leave total_experience_years untouched (None).
    update = ProfileUpdate(
        employment_history=[
            EmploymentEntryIn(employer="Old Co", title="Engineer", start_date="Jan 1990", end_date="Dec 2024"),
        ],
    )
    updated = profile_store.apply_profile_update(profile, update)

    if updated.professional_summary.total_experience_years != 11.0:
        _fail(
            failures,
            f"test 2: SAFETY VIOLATION -- updating employment_history alone changed "
            f"total_experience_years from 11.0 to {updated.professional_summary.total_experience_years}",
        )
    else:
        print("PASS: test 2 -> updating employment_history alone (34-year span) leaves total_experience_years untouched at 11.0")

    # The original profile object itself must be unmutated (apply_profile_update returns a copy).
    if profile.professional_summary.total_experience_years != 11.0 or profile.employment_history == updated.employment_history:
        _fail(failures, "test 2: apply_profile_update() mutated the input profile instead of returning a new one")
    else:
        print("PASS: test 2 -> apply_profile_update() did not mutate the original profile")

    return failures


def test_3_experience_update_never_touches_employment_history():
    failures = []

    profile = _base_confirmed_profile()
    original_employment_count = len(profile.employment_history)

    update = ProfileUpdate(total_experience_years=15.0)
    updated = profile_store.apply_profile_update(profile, update)

    if updated.professional_summary.total_experience_years != 15.0:
        _fail(failures, f"test 3: expected the explicit total_experience_years=15.0 update to apply, got {updated.professional_summary.total_experience_years}")
    elif len(updated.employment_history) != original_employment_count:
        _fail(
            failures,
            f"test 3: SAFETY VIOLATION -- updating total_experience_years alone changed "
            f"employment_history from {original_employment_count} to {len(updated.employment_history)} entries",
        )
    else:
        print(f"PASS: test 3 -> updating total_experience_years alone (11.0 -> 15.0) leaves employment_history untouched ({original_employment_count} entries)")

    return failures


def test_4_single_authoritative_value_feeds_eligibility():
    """The candidate_profile shape score_job.py/experience_eligibility.py
    consume (to_legacy_matching_profile()'s output) must read this SAME
    single value -- never a separately-derived one."""
    failures = []

    from candidate_profile import to_legacy_matching_profile

    profile = _base_confirmed_profile()
    legacy = to_legacy_matching_profile(profile)

    if legacy["candidate"]["experience_years"] != profile.professional_summary.total_experience_years:
        _fail(
            failures,
            f"test 4: to_legacy_matching_profile() experience_years "
            f"({legacy['candidate']['experience_years']}) does not match the confirmed "
            f"professional_summary.total_experience_years ({profile.professional_summary.total_experience_years}) "
            f"-- two different values would mean eligibility isn't reading the authoritative one",
        )
    else:
        print(f"PASS: test 4 -> the legacy matching profile score_job.py/experience_eligibility.py consume reads the SAME single authoritative value ({legacy['candidate']['experience_years']})")

    return failures


def main():
    failures = []
    print("EXPERIENCE PROFILE TRUTH REGRESSION TEST")
    print("=========================================")

    for test in [
        test_1_resume_extraction_never_sums_employment_dates,
        test_2_employment_history_update_never_changes_experience,
        test_3_experience_update_never_touches_employment_history,
        test_4_single_authoritative_value_feeds_eligibility,
    ]:
        failures.extend(test())

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)

    print("\nAll experience-profile-truth tests passed.")


if __name__ == "__main__":
    main()
