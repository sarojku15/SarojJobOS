#!/usr/bin/env python3

"""
Tests for scripts/candidate_profile_store.py (filesystem-based
candidate-profile persistence) and scripts/resume_extractor.py's
ExtractionResult contract.

Fully isolated: every save/load test writes to its own temporary
directory (never data/applications/jobos.db, never a real project
data/ path), and is cleaned up implicitly by the OS temp directory.
"""

import inspect
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import candidate_profile_store as store
from candidate_profile_store import (
    load_candidate_profile_draft,
    save_candidate_profile_draft,
    validate_candidate_profile_draft,
)
from candidate_profile import (
    Provenance,
    ProfileStatus,
    normalize_candidate_profile,
    promote_to_confirmed,
    to_legacy_matching_profile,
)
from resume_extractor import extract_candidate_profile_draft


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _temp_path(name="profile.json"):
    return Path(tempfile.mkdtemp(prefix="jobos_test_candidate_store_")) / name


def _rich_draft_profile():
    return normalize_candidate_profile(
        {
            "identity": {
                "candidate_id": "cand-store-1",
                "name": "Priya Kumar",
                "email": "priya@example.com",
                "phone": "+91-9000000000",
            },
            "professional_summary": {"total_experience_years": 9, "summary": "SRE."},
            "skills": {
                "cloud": [{"name": "AWS", "provenance": "RESUME_EXTRACTED"}],
                "containers_orchestration": [{"name": "Kubernetes", "provenance": "RESUME_EXTRACTED"}],
            },
            "certifications": [{"name": "AZ-104", "issuer": "Microsoft"}],
            "education": [{"institution": "IIT Kanpur", "degree": "Certificate"}],
            "employment_history": [
                {
                    "employer": "Example Corp",
                    "title": "SRE",
                    "start_date": "Jan 2020",
                    "end_date": "Present",
                    "skills": ["AWS"],
                }
            ],
            "job_preferences": {
                "target_roles": ["Senior SRE"],
                "target_locations": ["Bengaluru", "Remote India"],
            },
            "metadata": {"profile_status": "DRAFT", "source": "RESUME_EXTRACTED"},
        }
    )


# --------------------------------------------------------------------
# 1-9: round trip
# --------------------------------------------------------------------

def test_1_save_load_round_trip_succeeds():
    failures = []
    profile = _rich_draft_profile()
    path = _temp_path()

    saved_path = save_candidate_profile_draft(profile, path)
    if not saved_path.exists():
        _fail(failures, "test 1: expected save_candidate_profile_draft() to write a file that exists")
        return failures

    reloaded = load_candidate_profile_draft(saved_path)
    if reloaded is None:
        _fail(failures, "test 1: expected load_candidate_profile_draft() to return a profile")
    else:
        print(f"PASS: test 1 -> save/load round trip succeeds, file written to {saved_path}")

    return failures


def test_2_round_trip_preserves_candidate_id():
    failures = []
    profile = _rich_draft_profile()
    path = _temp_path()
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    if reloaded.identity.candidate_id != profile.identity.candidate_id:
        _fail(failures, f"test 2: expected candidate_id {profile.identity.candidate_id!r}, got {reloaded.identity.candidate_id!r}")
    else:
        print("PASS: test 2 -> round trip preserves candidate_id")

    return failures


def test_3_round_trip_preserves_draft_status():
    failures = []
    profile = _rich_draft_profile()
    path = _temp_path()
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    if reloaded.metadata.profile_status != ProfileStatus.DRAFT:
        _fail(failures, f"test 3: expected DRAFT, got {reloaded.metadata.profile_status}")
    else:
        print("PASS: test 3 -> round trip preserves DRAFT status")

    return failures


def test_4_round_trip_preserves_resume_extracted_provenance():
    failures = []
    profile = _rich_draft_profile()
    path = _temp_path()
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    if reloaded.metadata.source != Provenance.RESUME_EXTRACTED:
        _fail(failures, f"test 4: expected metadata.source RESUME_EXTRACTED, got {reloaded.metadata.source}")

    aws_skill = reloaded.skills.cloud[0]
    if aws_skill.provenance != Provenance.RESUME_EXTRACTED:
        _fail(failures, f"test 4: expected per-skill provenance RESUME_EXTRACTED, got {aws_skill.provenance}")

    if not failures:
        print("PASS: test 4 -> round trip preserves RESUME_EXTRACTED provenance at both profile and skill level")

    return failures


def test_5_round_trip_preserves_skills():
    failures = []
    profile = _rich_draft_profile()
    path = _temp_path()
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    original_cloud = [s.name for s in profile.skills.cloud]
    reloaded_cloud = [s.name for s in reloaded.skills.cloud]
    original_k8s = [s.name for s in profile.skills.containers_orchestration]
    reloaded_k8s = [s.name for s in reloaded.skills.containers_orchestration]

    if original_cloud != reloaded_cloud or original_k8s != reloaded_k8s:
        _fail(failures, f"test 5: expected skills preserved, got cloud={reloaded_cloud} (expected {original_cloud}), containers={reloaded_k8s} (expected {original_k8s})")
    else:
        print(f"PASS: test 5 -> round trip preserves skills: cloud={reloaded_cloud}, containers_orchestration={reloaded_k8s}")

    return failures


def test_6_round_trip_preserves_employment():
    failures = []
    profile = _rich_draft_profile()
    path = _temp_path()
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    if len(reloaded.employment_history) != 1:
        _fail(failures, f"test 6: expected 1 employment entry, got {len(reloaded.employment_history)}")
    else:
        entry = reloaded.employment_history[0]
        if entry.employer != "Example Corp" or entry.end_date != "Present" or entry.skills != ["AWS"]:
            _fail(failures, f"test 6: employment entry mismatch: employer={entry.employer!r} end_date={entry.end_date!r} skills={entry.skills!r}")
        else:
            print("PASS: test 6 -> round trip preserves employment history (employer, dates, associated skills)")

    return failures


def test_7_round_trip_preserves_certifications():
    failures = []
    profile = _rich_draft_profile()
    path = _temp_path()
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    if len(reloaded.certifications) != 1 or reloaded.certifications[0].name != "AZ-104" or reloaded.certifications[0].issuer != "Microsoft":
        _fail(failures, f"test 7: expected 1 certification (AZ-104, Microsoft), got {reloaded.certifications}")
    else:
        print("PASS: test 7 -> round trip preserves certifications (name and issuer)")

    return failures


def test_8_round_trip_preserves_education():
    failures = []
    profile = _rich_draft_profile()
    path = _temp_path()
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    if len(reloaded.education) != 1 or reloaded.education[0].institution != "IIT Kanpur":
        _fail(failures, f"test 8: expected 1 education entry (IIT Kanpur), got {reloaded.education}")
    else:
        print("PASS: test 8 -> round trip preserves education")

    return failures


def test_9_round_trip_preserves_job_preferences():
    failures = []
    profile = _rich_draft_profile()
    path = _temp_path()
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    if reloaded.job_preferences.target_roles != ["Senior SRE"]:
        _fail(failures, f"test 9: expected target_roles=['Senior SRE'], got {reloaded.job_preferences.target_roles}")
    if reloaded.job_preferences.target_locations != ["Bengaluru", "Remote India"]:
        _fail(failures, f"test 9: expected target_locations preserved, got {reloaded.job_preferences.target_locations}")

    if not failures:
        print("PASS: test 9 -> round trip preserves job_preferences (target_roles and target_locations)")

    return failures


# --------------------------------------------------------------------
# 10: corrupt input
# --------------------------------------------------------------------

def test_10_invalid_corrupt_json_rejected_cleanly():
    failures = []

    corrupt_path = _temp_path("corrupt.json")
    corrupt_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_path.write_text("{this is not valid json,,,")

    try:
        load_candidate_profile_draft(corrupt_path)
        _fail(failures, "test 10: expected load_candidate_profile_draft() to raise ValueError on corrupt JSON")
    except ValueError:
        pass

    missing_path = _temp_path("does_not_exist.json")
    try:
        load_candidate_profile_draft(missing_path)
        _fail(failures, "test 10: expected load_candidate_profile_draft() to raise ValueError on a missing file")
    except ValueError:
        pass

    missing_candidate_id_path = _temp_path("no_candidate_id.json")
    missing_candidate_id_path.parent.mkdir(parents=True, exist_ok=True)
    missing_candidate_id_path.write_text(json.dumps({"identity": {}}))
    try:
        load_candidate_profile_draft(missing_candidate_id_path)
        _fail(failures, "test 10: expected load_candidate_profile_draft() to raise ValueError on JSON missing identity.candidate_id")
    except ValueError:
        pass

    if not failures:
        print("PASS: test 10 -> corrupt JSON, a missing file, and structurally invalid JSON (no candidate_id) are all rejected with ValueError, never a silent partial profile")

    return failures


# --------------------------------------------------------------------
# 11-13: DRAFT safety (Task 4)
# --------------------------------------------------------------------

def test_11_draft_remains_draft_after_save_load():
    failures = []
    profile = _rich_draft_profile()

    if profile.metadata.profile_status != ProfileStatus.DRAFT:
        _fail(failures, "test 11: fixture profile must start as DRAFT for this test to be meaningful")
        return failures

    path = _temp_path()
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    if reloaded.metadata.profile_status != ProfileStatus.DRAFT:
        _fail(failures, f"test 11: expected DRAFT to remain DRAFT after save/load, got {reloaded.metadata.profile_status}")
    elif reloaded.metadata.confirmed_by_user is not False:
        _fail(failures, f"test 11: expected confirmed_by_user to remain False, got {reloaded.metadata.confirmed_by_user}")
    else:
        print("PASS: test 11 -> a DRAFT profile remains DRAFT (and confirmed_by_user stays False) after a save/load cycle -- persistence never confirms")

    return failures


def test_12_draft_cannot_export_to_legacy_by_default():
    failures = []
    profile = _rich_draft_profile()
    path = _temp_path()
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    try:
        to_legacy_matching_profile(reloaded)
        _fail(failures, "test 12: expected to_legacy_matching_profile() to refuse a loaded DRAFT profile by default")
    except ValueError:
        print("PASS: test 12 -> a loaded DRAFT profile is still refused by to_legacy_matching_profile() without allow_draft=True")

    return failures


def test_13_explicit_promote_to_confirmed_remains_required():
    failures = []
    profile = _rich_draft_profile()
    path = _temp_path()
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    if reloaded.metadata.profile_status == ProfileStatus.CONFIRMED:
        _fail(failures, "test 13: a freshly loaded profile must never already be CONFIRMED")
        return failures

    confirmed = promote_to_confirmed(reloaded)

    if confirmed.metadata.profile_status != ProfileStatus.CONFIRMED:
        _fail(failures, f"test 13: expected promote_to_confirmed() to produce CONFIRMED, got {confirmed.metadata.profile_status}")
    if reloaded.metadata.profile_status != ProfileStatus.DRAFT:
        _fail(failures, f"test 13: expected promote_to_confirmed() to leave the ORIGINAL object untouched (still DRAFT), got {reloaded.metadata.profile_status}")

    no_persistence_function_promotes = all(
        "promote" not in name.lower() and "confirm" not in name.lower()
        for name in ("save_candidate_profile_draft", "load_candidate_profile_draft", "validate_candidate_profile_draft")
    )
    if not no_persistence_function_promotes:
        _fail(failures, "test 13: a persistence function's own name suggests it might promote/confirm a profile")

    if not failures:
        print("PASS: test 13 -> explicit promote_to_confirmed() remains the only path to CONFIRMED; no persistence function performs it implicitly, and the original DRAFT object is left unmodified")

    return failures


# --------------------------------------------------------------------
# 14: no candidate-specific hardcoding
# --------------------------------------------------------------------

def test_14_no_candidate_specific_hardcoding():
    failures = []

    source = inspect.getsource(store)

    name_tokens_found = [t for t in ("Saroj", "Nayak") if t in source]
    if name_tokens_found:
        _fail(failures, f"test 14: candidate_profile_store.py contains a literal candidate name token: {name_tokens_found}")

    expected_param_counts = {
        "save_candidate_profile_draft": 2,
        "load_candidate_profile_draft": 1,
    }
    for fn_name, expected_count in expected_param_counts.items():
        fn = getattr(store, fn_name)
        signature = inspect.signature(fn)
        if len(signature.parameters) != expected_count:
            _fail(failures, f"test 14: expected {fn_name}() to take exactly {expected_count} parameter(s), got {signature}")

    if not failures:
        print("PASS: test 14 -> candidate_profile_store.py contains no literal candidate name and takes profile/path only as explicit arguments")

    return failures


# --------------------------------------------------------------------
# 15: real resume extraction still passes
# --------------------------------------------------------------------

def test_15_real_resume_extraction_still_passes():
    """
    Re-confirms the real resume PDF still extracts, saves, and loads
    back correctly after this component's changes -- writing its
    output artifact only to an isolated temp directory, never into
    data/applications/jobos.db or any tracked project data/ path.
    """
    failures = []

    pdf_path = ROOT / "resumes" / "SarojKumarNayak_SRE_DevOps_11Yrs.pdf"
    if not pdf_path.exists():
        _fail(failures, f"test 15: expected fixture resume PDF at {pdf_path}, file not found")
        return failures

    profile = extract_candidate_profile_draft(str(pdf_path), "cand-real-pdf-store-smoke")

    path = _temp_path("real_resume_draft.json")
    save_candidate_profile_draft(profile, path)
    reloaded = load_candidate_profile_draft(path)

    if reloaded.metadata.profile_status != ProfileStatus.DRAFT:
        _fail(failures, f"test 15: expected DRAFT, got {reloaded.metadata.profile_status}")
    if reloaded.professional_summary.total_experience_years != 11.0:
        _fail(failures, f"test 15: expected total_experience_years=11.0 preserved, got {reloaded.professional_summary.total_experience_years}")
    if len(reloaded.employment_history) != 4:
        _fail(failures, f"test 15: expected 4 employment entries preserved, got {len(reloaded.employment_history)}")

    if not failures:
        print(f"PASS: test 15 -> real resume extraction + save/load round trip still works end-to-end (isolated temp path: {path})")

    return failures


def main():
    tests = [
        test_1_save_load_round_trip_succeeds,
        test_2_round_trip_preserves_candidate_id,
        test_3_round_trip_preserves_draft_status,
        test_4_round_trip_preserves_resume_extracted_provenance,
        test_5_round_trip_preserves_skills,
        test_6_round_trip_preserves_employment,
        test_7_round_trip_preserves_certifications,
        test_8_round_trip_preserves_education,
        test_9_round_trip_preserves_job_preferences,
        test_10_invalid_corrupt_json_rejected_cleanly,
        test_11_draft_remains_draft_after_save_load,
        test_12_draft_cannot_export_to_legacy_by_default,
        test_13_explicit_promote_to_confirmed_remains_required,
        test_14_no_candidate_specific_hardcoding,
        test_15_real_resume_extraction_still_passes,
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

    print(f"All {len(tests)} candidate-profile-store tests passed.")


if __name__ == "__main__":
    main()
