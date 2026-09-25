#!/usr/bin/env python3

"""
Tests for scripts/review_candidate_profile.py -- the CLI human
review/confirmation workflow for a persisted CandidateProfile DRAFT.

The CLI is exercised as a real subprocess (matching this project's
existing convention -- see test_scoring.py -- of testing a CLI entry
point through subprocess.run() rather than importing its main()), so
these tests also exercise argv parsing, printed output, and exit codes
exactly as a real user would experience them. Interactive (--edit) mode
is exercised by piping stdin.

Every test writes only to its own temporary directory; none opens
data/applications/jobos.db.
"""

import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import review_candidate_profile as review_cli
from candidate_profile_store import save_candidate_profile_draft
from candidate_profile import normalize_candidate_profile, to_legacy_matching_profile


CLI_PATH = ROOT / "scripts" / "review_candidate_profile.py"
REAL_DRAFT_PATH = ROOT / "data" / "private" / "resume_draft_preview.NOT_CONFIRMED.json"


def _fail(failures, message):
    failures.append(message)
    print(f"FAIL: {message}")


def _run_cli(args, input_text=None):
    return subprocess.run(
        [sys.executable, str(CLI_PATH)] + args,
        capture_output=True,
        text=True,
        input=input_text,
        cwd=str(ROOT),
    )


def _temp_dir():
    return Path(tempfile.mkdtemp(prefix="jobos_test_review_cli_"))


def _write_draft_fixture(tmp_dir, **overrides):
    raw = {
        "identity": {"candidate_id": "cand-review-1", "name": "Priya Kumar", "email": "priya@example.com"},
        "professional_summary": {"total_experience_years": 9},
        "skills": {"cloud": [{"name": "AWS", "provenance": "RESUME_EXTRACTED"}]},
        "job_preferences": {},
        "metadata": {"profile_status": "DRAFT", "source": "RESUME_EXTRACTED"},
    }
    raw.update(overrides)
    profile = normalize_candidate_profile(raw)
    path = tmp_dir / "draft.json"
    save_candidate_profile_draft(profile, path)
    return path


# --------------------------------------------------------------------
# 1-3: loading and review display
# --------------------------------------------------------------------

def test_1_load_valid_draft():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)

    result = _run_cli([str(draft_path)])

    if result.returncode != 0:
        _fail(failures, f"test 1: expected exit code 0 for a plain review, got {result.returncode}: {result.stderr}")
    elif "CANDIDATE PROFILE REVIEW" not in result.stdout:
        _fail(failures, "test 1: expected the review header in stdout")
    else:
        print("PASS: test 1 -> loading a valid DRAFT for review succeeds (exit 0)")

    return failures


def test_2_review_displays_expected_major_sections():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)

    result = _run_cli([str(draft_path)])

    expected_headers = [
        "1. IDENTITY", "2. PROFESSIONAL SUMMARY", "3. TARGET ROLES", "4. SKILLS",
        "5. CERTIFICATIONS", "6. EDUCATION", "7. EMPLOYMENT HISTORY",
        "8. JOB PREFERENCES", "9. METADATA", "10. VALIDATION",
    ]
    missing = [h for h in expected_headers if h not in result.stdout]

    if missing:
        _fail(failures, f"test 2: missing expected section headers: {missing}")
    else:
        print(f"PASS: test 2 -> all {len(expected_headers)} major sections are displayed")

    return failures


def test_3_missing_optional_fields_do_not_crash():
    failures = []
    tmp_dir = _temp_dir()
    # A profile with almost nothing beyond the required candidate_id.
    draft_path = _write_draft_fixture(
        tmp_dir,
        identity={"candidate_id": "cand-minimal"},
        professional_summary={},
        skills={},
        job_preferences={},
    )

    result = _run_cli([str(draft_path)])

    if result.returncode != 0:
        _fail(failures, f"test 3: expected a minimal DRAFT to review without crashing, got exit {result.returncode}: {result.stderr}")
    elif "(not provided)" not in result.stdout:
        _fail(failures, "test 3: expected missing fields to render as '(not provided)', not crash or show blanks")
    elif "Traceback" in result.stderr:
        _fail(failures, f"test 3: CLI crashed with a traceback: {result.stderr}")
    else:
        print("PASS: test 3 -> missing optional fields (email, skills, job_preferences, etc.) do not crash review")

    return failures


# --------------------------------------------------------------------
# 4-5: validation display and enforcement
# --------------------------------------------------------------------

def test_4_validation_errors_are_displayed():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)

    # Force an ERROR-severity issue directly in the saved JSON (negative experience).
    data = json.loads(draft_path.read_text())
    data["professional_summary"]["total_experience_years"] = -5
    draft_path.write_text(json.dumps(data))

    result = _run_cli([str(draft_path)])

    if "ERRORS:" not in result.stdout:
        _fail(failures, f"test 4: expected an ERRORS section in the validation display, got:\n{result.stdout[-500:]}")
    else:
        print("PASS: test 4 -> validation errors are displayed under section 10 (VALIDATION)")

    return failures


def test_5_invalid_profile_cannot_be_confirmed():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)

    data = json.loads(draft_path.read_text())
    data["professional_summary"]["total_experience_years"] = -5
    draft_path.write_text(json.dumps(data))

    output_path = tmp_dir / "confirmed.json"
    result = _run_cli([str(draft_path), "--confirm", "--output", str(output_path)])

    if result.returncode == 0:
        _fail(failures, "test 5: expected a non-zero exit code when confirming an invalid profile")
    if output_path.exists():
        _fail(failures, "test 5: expected no output file to be written when confirmation is refused")

    if not failures:
        print("PASS: test 5 -> a profile with ERROR-severity validation issues cannot be confirmed, and no output file is written")

    return failures


# --------------------------------------------------------------------
# 6-7: DRAFT remains DRAFT
# --------------------------------------------------------------------

def test_6_draft_remains_draft_after_review_only():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)
    before = draft_path.read_bytes()

    _run_cli([str(draft_path)])

    after = draft_path.read_bytes()
    data = json.loads(after)

    if data["metadata"]["profile_status"] != "DRAFT":
        _fail(failures, f"test 6: expected DRAFT to remain DRAFT after review-only, got {data['metadata']['profile_status']!r}")
    if before != after:
        _fail(failures, "test 6: expected the draft file to be byte-identical after a review-only run")

    if not failures:
        print("PASS: test 6 -> DRAFT remains DRAFT (and the file is byte-identical) after a review-only operation")

    return failures


def test_7_draft_remains_draft_after_edit_without_confirm():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)
    before = draft_path.read_bytes()

    _run_cli([str(draft_path), "--set", "name=Someone Else"])

    after = draft_path.read_bytes()

    if before != after:
        _fail(failures, "test 7: expected the draft file to remain byte-identical even when --set edits are supplied without --confirm")
    else:
        data = json.loads(after)
        if data["identity"]["name"] == "Someone Else":
            _fail(failures, "test 7: the edit leaked into the saved draft file even without --confirm")
        else:
            print("PASS: test 7 -> DRAFT file remains unchanged after edits + save/review without an explicit --confirm")

    return failures


# --------------------------------------------------------------------
# 8-9: confirmation requires explicit action / uses promote_to_confirmed
# --------------------------------------------------------------------

def test_8_confirmation_requires_explicit_action():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)

    result = _run_cli([str(draft_path)])

    if "PROFILE CONFIRMED" in result.stdout:
        _fail(failures, "test 8: a plain review (no --confirm) must never print PROFILE CONFIRMED")
    elif "No changes were saved" not in result.stdout:
        _fail(failures, "test 8: expected an explicit statement that nothing was saved without --confirm")
    else:
        print("PASS: test 8 -> confirmation never happens without the explicit --confirm flag")

    return failures


def test_9_promote_to_confirmed_is_actually_used():
    failures = []
    source = inspect.getsource(review_cli)

    if "promote_to_confirmed(" not in source:
        _fail(failures, "test 9: review_candidate_profile.py does not call promote_to_confirmed() at all")
    if "profile_status = " in source or ".profile_status=" in source:
        _fail(failures, "test 9: review_candidate_profile.py appears to assign profile_status directly instead of going through promote_to_confirmed()")
    if "confirmed_by_user = True" in source or "confirmed_by_user=True" in source:
        _fail(failures, "test 9: review_candidate_profile.py appears to set confirmed_by_user directly instead of going through promote_to_confirmed()")

    if not failures:
        print("PASS: test 9 -> the CLI calls candidate_profile.promote_to_confirmed() and never assigns profile_status/confirmed_by_user directly")

    return failures


# --------------------------------------------------------------------
# 10-11: confirmed profile state
# --------------------------------------------------------------------

def test_10_confirmed_profile_has_confirmed_status():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)
    output_path = tmp_dir / "confirmed.json"

    result = _run_cli([str(draft_path), "--confirm", "--output", str(output_path)])

    if result.returncode != 0:
        _fail(failures, f"test 10: expected confirmation to succeed for a valid draft, got exit {result.returncode}: {result.stdout[-300:]} {result.stderr}")
        return failures

    data = json.loads(output_path.read_text())
    if data["metadata"]["profile_status"] != "CONFIRMED":
        _fail(failures, f"test 10: expected CONFIRMED, got {data['metadata']['profile_status']!r}")
    else:
        print("PASS: test 10 -> confirmed profile has profile_status=CONFIRMED")

    return failures


def test_11_confirmed_by_user_is_true():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)
    output_path = tmp_dir / "confirmed.json"

    _run_cli([str(draft_path), "--confirm", "--output", str(output_path)])
    data = json.loads(output_path.read_text())

    if data["metadata"]["confirmed_by_user"] is not True:
        _fail(failures, f"test 11: expected confirmed_by_user=True, got {data['metadata']['confirmed_by_user']!r}")
    else:
        print("PASS: test 11 -> confirmed_by_user is True after confirmation")

    return failures


# --------------------------------------------------------------------
# 12-14: output safety
# --------------------------------------------------------------------

def test_12_original_draft_byte_identical_after_confirmation():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)
    before = draft_path.read_bytes()
    output_path = tmp_dir / "confirmed.json"

    _run_cli([str(draft_path), "--set", "name=Edited Name", "--confirm", "--output", str(output_path)])

    after = draft_path.read_bytes()

    if before != after:
        _fail(failures, "test 12: expected the original DRAFT JSON to remain byte-identical after a confirmation run (even one that applied edits)")
    else:
        print("PASS: test 12 -> original DRAFT JSON is byte-identical after confirmation, even with edits applied to the in-memory copy")

    return failures


def test_13_confirmed_profile_written_to_new_path():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)
    output_path = tmp_dir / "confirmed.json"

    if output_path.exists():
        _fail(failures, "test 13: output path should not exist before confirmation")

    _run_cli([str(draft_path), "--confirm", "--output", str(output_path)])

    if not output_path.exists():
        _fail(failures, "test 13: expected the confirmed profile to be written to the new --output path")
    elif output_path.resolve() == draft_path.resolve():
        _fail(failures, "test 13: output path must differ from the draft path")
    else:
        print(f"PASS: test 13 -> confirmed profile written to a new path distinct from the draft: {output_path.name}")

    return failures


def test_14_existing_output_not_overwritten_without_permission():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)
    output_path = tmp_dir / "confirmed.json"
    output_path.write_text('{"sentinel": "do-not-overwrite"}')

    result = _run_cli([str(draft_path), "--confirm", "--output", str(output_path)])

    if result.returncode == 0:
        _fail(failures, "test 14: expected a non-zero exit code when --output already exists without --force")

    still_sentinel = json.loads(output_path.read_text()).get("sentinel") == "do-not-overwrite"
    if not still_sentinel:
        _fail(failures, "test 14: the existing output file was overwritten without --force")
        return failures

    force_result = _run_cli([str(draft_path), "--confirm", "--output", str(output_path), "--force"])
    if force_result.returncode != 0:
        _fail(failures, f"test 14: expected --force to allow overwriting, got exit {force_result.returncode}")
    else:
        overwritten = json.loads(output_path.read_text()).get("metadata", {}).get("profile_status") == "CONFIRMED"
        if not overwritten:
            _fail(failures, "test 14: --force did not actually overwrite the output file")
        else:
            print("PASS: test 14 -> an existing output file is refused without --force, and correctly overwritten with --force")

    return failures


# --------------------------------------------------------------------
# 15-17: edits preserved, candidate_id/provenance stable
# --------------------------------------------------------------------

def test_15_user_edits_are_preserved():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)
    output_path = tmp_dir / "confirmed.json"

    _run_cli(
        [
            str(draft_path),
            "--set", "name=Renamed Person",
            "--set", "target_locations=Bengaluru,Hyderabad",
            "--set", "skills.cloud=AWS,Azure,GCP",
            "--confirm", "--output", str(output_path),
        ]
    )

    data = json.loads(output_path.read_text())

    if data["identity"]["name"] != "Renamed Person":
        _fail(failures, f"test 15: expected edited name preserved, got {data['identity']['name']!r}")
    if data["job_preferences"]["target_locations"] != ["Bengaluru", "Hyderabad"]:
        _fail(failures, f"test 15: expected edited target_locations preserved, got {data['job_preferences']['target_locations']!r}")
    cloud_names = [s["name"] for s in data["skills"]["cloud"]]
    if cloud_names != ["AWS", "Azure", "GCP"]:
        _fail(failures, f"test 15: expected edited cloud skills preserved, got {cloud_names!r}")

    if not failures:
        print("PASS: test 15 -> user edits (name, target_locations, skills.cloud) are preserved through confirmation")

    return failures


def test_16_candidate_id_cannot_accidentally_change():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)
    output_path = tmp_dir / "confirmed.json"

    original_id = json.loads(draft_path.read_text())["identity"]["candidate_id"]

    # candidate_id is deliberately NOT in the editable-field list.
    result = _run_cli([str(draft_path), "--set", "candidate_id=hijacked", "--confirm", "--output", str(output_path)])

    if result.returncode == 0 and "unknown --set field" not in result.stdout:
        # If it succeeded, candidate_id must still be unchanged.
        data = json.loads(output_path.read_text())
        if data["identity"]["candidate_id"] != original_id:
            _fail(failures, f"test 16: candidate_id changed from {original_id!r} to {data['identity']['candidate_id']!r}")
        else:
            print("PASS: test 16 -> candidate_id is unaffected even if an unrelated --set is attempted")
    elif "unknown --set field" in result.stdout:
        print("PASS: test 16 -> candidate_id is not an editable field at all -- attempting to --set it is rejected outright")
    else:
        _fail(failures, f"test 16: unexpected CLI behavior: exit={result.returncode} stdout_tail={result.stdout[-300:]}")

    return failures


def test_17_provenance_remains_preserved():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)
    output_path = tmp_dir / "confirmed.json"

    _run_cli([str(draft_path), "--confirm", "--output", str(output_path)])
    data = json.loads(output_path.read_text())

    if data["metadata"]["source"] != "RESUME_EXTRACTED":
        _fail(failures, f"test 17: expected metadata.source to remain RESUME_EXTRACTED, got {data['metadata']['source']!r}")

    aws_skill = data["skills"]["cloud"][0]
    if aws_skill["provenance"] != "RESUME_EXTRACTED":
        _fail(failures, f"test 17: expected the original AWS skill's provenance to remain RESUME_EXTRACTED, got {aws_skill['provenance']!r}")

    if not failures:
        print("PASS: test 17 -> provenance (profile-level and untouched skill-level) is preserved through confirmation")

    return failures


# --------------------------------------------------------------------
# 18-20
# --------------------------------------------------------------------

def test_18_confirmed_profile_exports_via_legacy_matching_profile():
    failures = []
    tmp_dir = _temp_dir()
    draft_path = _write_draft_fixture(tmp_dir)
    output_path = tmp_dir / "confirmed.json"

    _run_cli([str(draft_path), "--confirm", "--output", str(output_path)])

    from candidate_profile_store import load_candidate_profile_draft

    confirmed = load_candidate_profile_draft(output_path)

    try:
        legacy = to_legacy_matching_profile(confirmed)
        if "candidate" not in legacy or "target_locations" not in legacy:
            _fail(failures, f"test 18: legacy projection missing expected keys, got {list(legacy.keys())}")
        else:
            print("PASS: test 18 -> the confirmed profile successfully exports via to_legacy_matching_profile()")
    except ValueError as error:
        _fail(failures, f"test 18: expected to_legacy_matching_profile() to succeed on a CONFIRMED profile, got: {error}")

    return failures


def test_19_no_candidate_specific_hardcoding():
    failures = []
    source = inspect.getsource(review_cli)

    name_tokens_found = [t for t in ("Saroj", "Nayak") if t in source]
    if name_tokens_found:
        _fail(failures, f"test 19: review_candidate_profile.py contains a literal candidate name token: {name_tokens_found}")

    if "config/profile.json" in source or "import json" in source and "config" in source:
        _fail(failures, "test 19: review_candidate_profile.py appears to reference config/profile.json")

    if not failures:
        print("PASS: test 19 -> review_candidate_profile.py contains no literal candidate name or config/profile.json reference")

    return failures


def test_20_no_sqlite_database_access():
    failures = []
    source = inspect.getsource(review_cli)

    forbidden = ["sqlite3", "jobos.db", "tracker", "init_tracker"]
    found = [f for f in forbidden if f in source]

    if found:
        _fail(failures, f"test 20: review_candidate_profile.py references database-related symbols it must not: {found}")
    else:
        print("PASS: test 20 -> review_candidate_profile.py imports no SQLite/database module and references no tracker/DB symbol")

    return failures


# --------------------------------------------------------------------
# Real artifact test (only if the fixture exists)
# --------------------------------------------------------------------

def test_21_real_draft_artifact_review_and_confirm():
    failures = []

    if not REAL_DRAFT_PATH.exists():
        print("SKIP: test 21 -> real draft artifact not present at data/private/resume_draft_preview.NOT_CONFIRMED.json (not a failure)")
        return failures

    before = REAL_DRAFT_PATH.read_bytes()

    review_result = _run_cli([str(REAL_DRAFT_PATH)])
    if review_result.returncode != 0:
        _fail(failures, f"test 21: expected review-only on the real draft to succeed, got exit {review_result.returncode}")

    after_review = REAL_DRAFT_PATH.read_bytes()
    if before != after_review:
        _fail(failures, "test 21: the real draft artifact was modified by a review-only run")
        return failures

    tmp_dir = _temp_dir()
    output_path = tmp_dir / "real_confirmed_TEST_ONLY.json"

    confirm_result = _run_cli(
        [str(REAL_DRAFT_PATH), "--set", "target_locations=Bengaluru,Remote India", "--confirm", "--output", str(output_path)]
    )

    after_confirm = REAL_DRAFT_PATH.read_bytes()
    if before != after_confirm:
        _fail(failures, "test 21: the real draft artifact was modified by a confirmation run")

    if confirm_result.returncode != 0:
        _fail(failures, f"test 21: expected confirmation of the real draft to succeed, got exit {confirm_result.returncode}: {confirm_result.stdout[-300:]}")
    elif not output_path.exists():
        _fail(failures, "test 21: expected a confirmed output file at the temporary test path")
    else:
        data = json.loads(output_path.read_text())
        if data["metadata"]["profile_status"] != "CONFIRMED":
            _fail(failures, f"test 21: expected CONFIRMED, got {data['metadata']['profile_status']!r}")

    if not failures:
        print(
            f"PASS: test 21 -> real draft artifact reviewed and confirmed successfully via a "
            f"temporary output path ({output_path}); original artifact remains byte-identical"
        )

    return failures


def main():
    tests = [
        test_1_load_valid_draft,
        test_2_review_displays_expected_major_sections,
        test_3_missing_optional_fields_do_not_crash,
        test_4_validation_errors_are_displayed,
        test_5_invalid_profile_cannot_be_confirmed,
        test_6_draft_remains_draft_after_review_only,
        test_7_draft_remains_draft_after_edit_without_confirm,
        test_8_confirmation_requires_explicit_action,
        test_9_promote_to_confirmed_is_actually_used,
        test_10_confirmed_profile_has_confirmed_status,
        test_11_confirmed_by_user_is_true,
        test_12_original_draft_byte_identical_after_confirmation,
        test_13_confirmed_profile_written_to_new_path,
        test_14_existing_output_not_overwritten_without_permission,
        test_15_user_edits_are_preserved,
        test_16_candidate_id_cannot_accidentally_change,
        test_17_provenance_remains_preserved,
        test_18_confirmed_profile_exports_via_legacy_matching_profile,
        test_19_no_candidate_specific_hardcoding,
        test_20_no_sqlite_database_access,
        test_21_real_draft_artifact_review_and_confirm,
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

    print(f"All {len(tests)} review-candidate-profile CLI tests passed (or skipped where noted).")


if __name__ == "__main__":
    main()
