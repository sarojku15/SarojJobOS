#!/usr/bin/env python3

"""
Tests for scripts/candidate_profile.py -- the canonical candidate-
profile schema, lifecycle, and validation layer.

Fully isolated: pure in-memory computation, no DB, no network. Tests
14-16 exercise the REAL score_job.py / experience_eligibility.py /
location_taxonomy.py / job_eligibility.py functions to prove the
canonical profile is consumable by the existing scoring/eligibility
layer with zero changes to those modules.
"""

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import candidate_profile as cp
from candidate_profile import (
    CandidateProfile,
    Provenance,
    ProfileStatus,
    normalize_candidate_profile,
    promote_to_confirmed,
    serialize_candidate_profile,
    to_legacy_matching_profile,
    validate_candidate_profile,
)

from score_job import score_job
from experience_eligibility import assess_experience_eligibility, Eligibility
from location_taxonomy import assess_location_eligibility, LocationEligibility
from job_eligibility import assess_job_eligibility


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


# --------------------------------------------------------------------
# 1. valid minimal DRAFT profile
# --------------------------------------------------------------------

def test_1_valid_minimal_draft_profile():
    failures = []

    profile = normalize_candidate_profile({"identity": {"candidate_id": "cand-1"}})
    result = validate_candidate_profile(profile)

    if not result.valid:
        _fail(failures, f"test 1: expected a minimal DRAFT (only candidate_id) to be valid, got errors: {[e.message for e in result.error_issues]}")
    elif profile.metadata.profile_status != ProfileStatus.DRAFT:
        _fail(failures, f"test 1: expected default profile_status DRAFT, got {profile.metadata.profile_status}")
    else:
        print("PASS: test 1 -> minimal DRAFT profile (only candidate_id) is valid")

    return failures


# --------------------------------------------------------------------
# 2. valid CONFIRMED profile
# --------------------------------------------------------------------

def test_2_valid_confirmed_profile():
    failures = []

    profile = normalize_candidate_profile(
        {
            "identity": {"candidate_id": "cand-2", "name": "Jane Doe"},
            "professional_summary": {"total_experience_years": 6},
            "job_preferences": {"target_locations": ["Bengaluru"]},
            "metadata": {"profile_status": "CONFIRMED", "confirmed_by_user": True},
        }
    )
    result = validate_candidate_profile(profile)

    if not result.valid:
        _fail(failures, f"test 2: expected a complete CONFIRMED profile to be valid, got errors: {[e.message for e in result.error_issues]}")
    else:
        print("PASS: test 2 -> valid CONFIRMED profile passes validation")

    return failures


# --------------------------------------------------------------------
# 3. missing optional fields
# --------------------------------------------------------------------

def test_3_missing_optional_fields():
    """
    email, phone, salary, certifications, education, and target
    locations may all be absent without invalidating a DRAFT.
    """
    failures = []

    profile = normalize_candidate_profile(
        {"identity": {"candidate_id": "cand-3", "name": "No Extras"}}
    )
    result = validate_candidate_profile(profile)

    checks = [
        (profile.identity.email is None, "email"),
        (profile.identity.phone is None, "phone"),
        (profile.job_preferences.salary_expectation_min is None, "salary_expectation_min"),
        (profile.certifications == [], "certifications"),
        (profile.education == [], "education"),
        (profile.job_preferences.target_locations == [], "target_locations"),
    ]

    for is_missing, field_name in checks:
        if not is_missing:
            _fail(failures, f"test 3: expected {field_name} to default to missing/empty, but it did not")

    if not result.valid:
        _fail(failures, f"test 3: expected profile with only optional fields missing to remain valid, got: {[e.message for e in result.error_issues]}")

    if not failures:
        print("PASS: test 3 -> all optional fields (email/phone/salary/certifications/education/locations) may be absent without invalidating the profile")

    return failures


# --------------------------------------------------------------------
# 4. invalid lifecycle state
# --------------------------------------------------------------------

def test_4_invalid_lifecycle_state():
    failures = []

    profile = normalize_candidate_profile({"identity": {"candidate_id": "cand-4"}})
    profile.metadata.profile_status = "NOT_A_REAL_STATE"  # bypass normalize's own enum check

    result = validate_candidate_profile(profile)

    if result.valid:
        _fail(failures, "test 4: expected an invalid lifecycle state to fail validation")
    elif not any("lifecycle state" in e.message for e in result.error_issues):
        _fail(failures, f"test 4: expected a lifecycle-state error, got: {[e.message for e in result.error_issues]}")
    else:
        print("PASS: test 4 -> invalid lifecycle state fails validation")

    try:
        normalize_candidate_profile(
            {"identity": {"candidate_id": "cand-4b"}, "metadata": {"profile_status": "BOGUS"}}
        )
        _fail(failures, "test 4: expected normalize_candidate_profile() to reject an unparseable profile_status")
    except ValueError:
        print("PASS: test 4 -> normalize_candidate_profile() also rejects an unparseable profile_status string")

    return failures


# --------------------------------------------------------------------
# 5. invalid experience value
# --------------------------------------------------------------------

def test_5_invalid_experience_value():
    failures = []

    negative = normalize_candidate_profile(
        {"identity": {"candidate_id": "cand-5a"}, "professional_summary": {"total_experience_years": -3}}
    )
    result_negative = validate_candidate_profile(negative)

    non_numeric = normalize_candidate_profile(
        {"identity": {"candidate_id": "cand-5b"}, "professional_summary": {"total_experience_years": "eleven"}}
    )
    result_non_numeric = validate_candidate_profile(non_numeric)

    if result_negative.valid:
        _fail(failures, "test 5: expected negative total_experience_years to fail validation")
    if result_non_numeric.valid:
        _fail(failures, "test 5: expected a non-numeric total_experience_years to fail validation")

    if not failures:
        print("PASS: test 5 -> negative and non-numeric total_experience_years both fail validation")

    return failures


# --------------------------------------------------------------------
# 6. malformed target location
# --------------------------------------------------------------------

def test_6_malformed_target_location():
    failures = []

    not_a_list = normalize_candidate_profile({"identity": {"candidate_id": "cand-6a"}})
    not_a_list.job_preferences.target_locations = "Bengaluru"  # should be a list
    result_not_list = validate_candidate_profile(not_a_list)

    non_string_entry = normalize_candidate_profile(
        {"identity": {"candidate_id": "cand-6b"}, "job_preferences": {"target_locations": ["Bengaluru", 123]}}
    )
    result_non_string = validate_candidate_profile(non_string_entry)

    if result_not_list.valid:
        _fail(failures, "test 6: expected a non-list target_locations to fail validation")
    if result_non_string.valid:
        _fail(failures, "test 6: expected a non-string entry inside target_locations to fail validation")

    if not failures:
        print("PASS: test 6 -> malformed target_locations (wrong type / non-string entry) fails validation")

    return failures


# --------------------------------------------------------------------
# 7. invalid skill structure
# --------------------------------------------------------------------

def test_7_invalid_skill_structure():
    failures = []

    try:
        normalize_candidate_profile(
            {"identity": {"candidate_id": "cand-7a"}, "skills": {"cloud": [{"proficiency": "expert"}]}}
        )
        _fail(failures, "test 7: expected a skill entry with no 'name' to be rejected during normalization")
    except ValueError:
        print("PASS: test 7 -> normalize_candidate_profile() rejects a skill entry missing 'name'")

    bad_years = normalize_candidate_profile(
        {"identity": {"candidate_id": "cand-7b"}, "skills": {"cloud": [{"name": "AWS", "years": -2}]}}
    )
    result = validate_candidate_profile(bad_years)

    if result.valid:
        _fail(failures, "test 7: expected a negative skill 'years' value to fail validation")
    else:
        print("PASS: test 7 -> a skill with a negative 'years' value fails validation")

    return failures


# --------------------------------------------------------------------
# 8. duplicate skill normalization
# --------------------------------------------------------------------

def test_8_duplicate_skill_normalization():
    failures = []

    profile = normalize_candidate_profile(
        {
            "identity": {"candidate_id": "cand-8"},
            "skills": {"cloud": [{"name": "AWS"}, {"name": "aws"}, {"name": "  AWS  "}]},
        }
    )
    result = validate_candidate_profile(profile)

    warnings = [e for e in result.errors if e.severity == "WARNING" and "duplicate" in e.message.lower()]

    if not result.valid:
        _fail(failures, f"test 8: expected duplicate skills to be a WARNING, not invalidate the profile, got errors: {[e.message for e in result.error_issues]}")
    elif len(warnings) != 2:
        _fail(failures, f"test 8: expected 2 duplicate-skill warnings (for the 2nd and 3rd 'AWS' entries), got {len(warnings)}: {[w.message for w in warnings]}")
    else:
        print(f"PASS: test 8 -> {len(warnings)} duplicate-skill warnings raised, profile still valid (not silently deduplicated or rejected)")

    return failures


# --------------------------------------------------------------------
# 9. candidate-specific profile behavior
# --------------------------------------------------------------------

def test_9_candidate_specific_profile_behavior():
    """
    Two materially different, non-Saroj candidate profiles must produce
    materially different legacy matching views and eligibility results
    for the same job.
    """
    failures = []

    job = {
        "title": "Senior Site Reliability Engineer",
        "jd_text": "AWS Kubernetes Terraform Jenkins Prometheus SLO incident reliability production cloud platform",
        "location": "Bengaluru",
        "work_model": "Hybrid",
        "experience_required": "8-10 years",
        "mandatory_skills": [],
        "preferred_skills": [],
    }

    candidate_a = promote_to_confirmed(
        normalize_candidate_profile(
            {
                "identity": {"candidate_id": "cand-9a", "name": "Senior SRE"},
                "professional_summary": {"total_experience_years": 9},
                "skills": {"cloud": [{"name": "AWS"}], "containers_orchestration": [{"name": "Kubernetes"}]},
                "job_preferences": {"target_locations": ["Bengaluru"]},
            }
        )
    )
    candidate_b = promote_to_confirmed(
        normalize_candidate_profile(
            {
                "identity": {"candidate_id": "cand-9b", "name": "Junior Dev"},
                "professional_summary": {"total_experience_years": 1},
                "job_preferences": {"target_locations": ["Mumbai"]},
            }
        )
    )

    score_a = score_job(job, to_legacy_matching_profile(candidate_a))
    score_b = score_job(job, to_legacy_matching_profile(candidate_b))

    if score_a["score"] == score_b["score"]:
        _fail(failures, f"test 9: expected different scores for materially different candidate profiles, both got {score_a['score']}")
    else:
        print(f"PASS: test 9 -> candidate A (senior, Bengaluru) scores {score_a['score']}, candidate B (junior, Mumbai) scores {score_b['score']}")

    return failures


# --------------------------------------------------------------------
# 10. no Saroj-specific hardcoding
# --------------------------------------------------------------------

def test_10_no_saroj_specific_hardcoding():
    failures = []

    source = inspect.getsource(cp)

    if "import json" in source or "json.load" in source or "open(" in source:
        _fail(failures, "test 10: candidate_profile.py appears to read a file at runtime -- candidate data must only ever arrive as a function argument")

    name_tokens_found = [t for t in ("Saroj", "Nayak") if t in source]
    if name_tokens_found:
        _fail(failures, f"test 10: candidate_profile.py contains a literal candidate name token: {name_tokens_found}")

    signature = inspect.signature(normalize_candidate_profile)
    if len(signature.parameters) != 1:
        _fail(failures, f"test 10: expected normalize_candidate_profile(raw) to take exactly one required parameter, got {signature}")

    if not failures:
        print("PASS: test 10 -> candidate_profile.py never reads a config file, contains no literal candidate name, and takes candidate data only as an explicit argument")

    return failures


# --------------------------------------------------------------------
# 11. provenance validation
# --------------------------------------------------------------------

def test_11_provenance_validation():
    failures = []

    profile = normalize_candidate_profile(
        {
            "identity": {"candidate_id": "cand-11"},
            "skills": {
                "cloud": [
                    {"name": "AWS", "provenance": "RESUME_EXTRACTED"},
                    {"name": "Azure", "provenance": "USER_ENTERED"},
                ]
            },
            "metadata": {"source": "RESUME_EXTRACTED"},
        }
    )

    aws_skill = profile.skills.cloud[0]
    azure_skill = profile.skills.cloud[1]

    if aws_skill.provenance != Provenance.RESUME_EXTRACTED:
        _fail(failures, f"test 11: expected AWS skill provenance RESUME_EXTRACTED, got {aws_skill.provenance}")
    if azure_skill.provenance != Provenance.USER_ENTERED:
        _fail(failures, f"test 11: expected Azure skill provenance USER_ENTERED, got {azure_skill.provenance}")
    if profile.metadata.source != Provenance.RESUME_EXTRACTED:
        _fail(failures, f"test 11: expected profile-level metadata.source RESUME_EXTRACTED, got {profile.metadata.source}")

    try:
        normalize_candidate_profile(
            {"identity": {"candidate_id": "cand-11b"}, "skills": {"cloud": [{"name": "AWS", "provenance": "NOT_REAL"}]}}
        )
        _fail(failures, "test 11: expected an invalid provenance value to raise ValueError")
    except ValueError:
        pass

    if not failures:
        print("PASS: test 11 -> per-skill and profile-level provenance are parsed correctly, invalid provenance values are rejected")

    return failures


# --------------------------------------------------------------------
# 12. DRAFT is not treated as CONFIRMED
# --------------------------------------------------------------------

def test_12_draft_is_not_treated_as_confirmed():
    failures = []

    draft = normalize_candidate_profile(
        {
            "identity": {"candidate_id": "cand-12", "name": "Draft Person"},
            "professional_summary": {"total_experience_years": 5},
            "job_preferences": {"target_locations": ["Bengaluru"]},
        }
    )

    if draft.metadata.profile_status != ProfileStatus.DRAFT:
        _fail(failures, f"test 12: expected default status DRAFT, got {draft.metadata.profile_status}")

    try:
        to_legacy_matching_profile(draft)
        _fail(failures, "test 12: expected to_legacy_matching_profile() to refuse a DRAFT profile by default")
    except ValueError:
        print("PASS: test 12a -> to_legacy_matching_profile() refuses a DRAFT profile by default (structural guard)")

    # The explicit opt-in path must still work (for a future "preview" feature).
    preview = to_legacy_matching_profile(draft, allow_draft=True)
    if preview["candidate"]["experience_years"] != 5:
        _fail(failures, "test 12: expected allow_draft=True to still produce a usable preview projection")
    else:
        print("PASS: test 12b -> allow_draft=True still allows an explicit preview projection")

    return failures


# --------------------------------------------------------------------
# 13-16: existing scoring/eligibility functions can consume the
# canonical profile (via its legacy projection) with zero changes.
# --------------------------------------------------------------------

_INTEGRATION_JOB = {
    "title": "Senior Site Reliability Engineer",
    "jd_text": (
        "AWS Azure Kubernetes EKS Terraform Jenkins GitHub Actions "
        "Prometheus Grafana SLO SLI incident reliability production "
        "support root cause RCA cloud platform microservices "
        "infrastructure distributed systems"
    ),
    "location": "Bengaluru",
    "work_model": "Hybrid",
    "experience_required": "8-10 years",
    "mandatory_skills": ["Kubernetes", "Terraform"],
    "preferred_skills": [],
}


def _confirmed_integration_profile():
    return promote_to_confirmed(
        normalize_candidate_profile(
            {
                "identity": {"candidate_id": "cand-integ", "name": "Priya Kumar"},
                "professional_summary": {"total_experience_years": 9},
                "skills": {
                    "cloud": [{"name": "AWS"}, {"name": "Azure"}],
                    "containers_orchestration": [{"name": "Kubernetes"}, {"name": "EKS"}],
                    "infrastructure_iac": [{"name": "Terraform"}],
                    "cicd": [{"name": "Jenkins"}, {"name": "GitHub Actions"}],
                    "observability": [{"name": "Prometheus"}, {"name": "Grafana"}],
                    "security_iam": [{"name": "SLO"}, {"name": "Incident Management"}],
                },
                "job_preferences": {"target_locations": ["Bengaluru", "Hyderabad"]},
            }
        )
    )


def test_13_existing_scoring_can_consume_canonical_profile():
    failures = []

    legacy = to_legacy_matching_profile(_confirmed_integration_profile())
    result = score_job(_INTEGRATION_JOB, legacy)

    if result["priority"] not in ("A", "B"):
        _fail(failures, f"test 13: expected a strong-fit canonical profile to score A/B, got priority={result['priority']} score={result['score']}")
    else:
        print(f"PASS: test 13 -> score_job() consumes the canonical profile's legacy projection unchanged -> {result['score']}/{result['priority']}")

    return failures


def test_14_existing_experience_eligibility_can_consume_canonical_profile():
    failures = []

    legacy = to_legacy_matching_profile(_confirmed_integration_profile())
    assessment = assess_experience_eligibility(_INTEGRATION_JOB, legacy)

    if assessment.eligibility != Eligibility.MATCH:
        _fail(failures, f"test 14: expected MATCH for a 9-year candidate against an 8-10 year job, got {assessment.eligibility}")
    else:
        print("PASS: test 14 -> experience_eligibility.assess_experience_eligibility() consumes the canonical profile's legacy projection unchanged")

    return failures


def test_15_existing_location_eligibility_can_consume_canonical_profile():
    failures = []

    legacy = to_legacy_matching_profile(_confirmed_integration_profile())
    assessment = assess_location_eligibility(_INTEGRATION_JOB, legacy)

    if assessment.eligibility != LocationEligibility.MATCH:
        _fail(failures, f"test 15: expected MATCH for a Bengaluru-targeting candidate against a Bengaluru job, got {assessment.eligibility}")
    else:
        print("PASS: test 15 -> location_taxonomy.assess_location_eligibility() consumes the canonical profile's legacy projection unchanged")

    return failures


def test_16_existing_job_eligibility_can_consume_canonical_profile():
    failures = []

    legacy = to_legacy_matching_profile(_confirmed_integration_profile())
    result = assess_job_eligibility(_INTEGRATION_JOB, legacy)

    if not result.eligible or result.reason_code != "ELIGIBLE":
        _fail(failures, f"test 16: expected eligible=True/ELIGIBLE, got eligible={result.eligible} reason_code={result.reason_code}")
    else:
        print("PASS: test 16 -> job_eligibility.assess_job_eligibility() consumes the canonical profile's legacy projection unchanged")

    return failures


def main():
    tests = [
        test_1_valid_minimal_draft_profile,
        test_2_valid_confirmed_profile,
        test_3_missing_optional_fields,
        test_4_invalid_lifecycle_state,
        test_5_invalid_experience_value,
        test_6_malformed_target_location,
        test_7_invalid_skill_structure,
        test_8_duplicate_skill_normalization,
        test_9_candidate_specific_profile_behavior,
        test_10_no_saroj_specific_hardcoding,
        test_11_provenance_validation,
        test_12_draft_is_not_treated_as_confirmed,
        test_13_existing_scoring_can_consume_canonical_profile,
        test_14_existing_experience_eligibility_can_consume_canonical_profile,
        test_15_existing_location_eligibility_can_consume_canonical_profile,
        test_16_existing_job_eligibility_can_consume_canonical_profile,
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

    print(f"All {len(tests)} candidate-profile tests passed.")


if __name__ == "__main__":
    main()
